import json
from pathlib import Path
import re
import sys
import urllib.request

import cv2
from onnxocr.onnx_paddleocr import ONNXPaddleOcr

from src.utils.QuestDecisionEngine import build_jev_payload, parse_jev_response
from src.utils.QuestOcrPrivacy import prepare_quest_ocr_frame, quest_goal_from_lines, sanitize_quest_text


root = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
settings = json.loads((root / "configs/QuestStoryTask.json").read_text(encoding="utf-8"))
frame = cv2.imread(str(root / "tests/images/quest_rock_obstruction.png"))
if frame is None:
    raise FileNotFoundError("岩壁受阻截图不存在")
log = (root / "logs/ok-script.2026-10-04.log").read_text(encoding="utf-8")
if "22:44:08,210" not in log or "累计前进 3.0 秒后背景保持静止且任务距离没有缩减" not in log:
    raise ValueError("岩壁受阻的实际运行记录不存在")
engine = ONNXPaddleOcr(use_angle_cls=False, use_openvino=True, use_npu=True)
height, width = frame.shape[:2]
panel = frame[int(height * .20):int(height * .43), int(width * .01):int(width * .28)]
goal = quest_goal_from_lines([entry[1][0] for entry in engine.ocr(panel, cls=False)[0]])
screen_text = sanitize_quest_text("\n".join(entry[1][0] for entry in engine.ocr(prepare_quest_ocr_frame(frame), cls=False)[0]))
context = {
    "event": "movement_blocked",
    "has_f_button": False,
    "interaction_text": "",
    "distance_meters": 70,
    "area": None,
    "movement": {"source": "recorded_runtime_log", "moving": False},
    "stationary_movement_seconds": 3.0,
    "jump_attempts": 0,
    "recent_actions": [{"action": "walk", "outcome": "no_observed_progress", "goal": goal, "distance_meters": 70}],
    "available_actions": ["jump", "recover", "wait"],
}
payload = build_jev_payload(frame, goal, ocr_text=screen_text, context=context)
body = json.dumps(payload, ensure_ascii=False)
if re.search(r"特征[码碼]|\d{8,}", body):
    raise ValueError("受阻决策请求包含特征码")
request = urllib.request.Request(settings["API URL"], data=body.encode("utf-8"),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {settings['API Key']}"})
with urllib.request.urlopen(request, timeout=30) as response:
    result = json.load(response)
action = parse_jev_response(result)
print(json.dumps({"action": action.action_type, "confidence": action.confidence,
                  "probabilities": action.probabilities,
                  "recovery_confident": action.recovery_confident, "task_kind": action.task_kind,
                  "tokens": action.total_tokens}, ensure_ascii=False))
(root / "tests/data/quest_recovery_jev_response.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
if not action.recovery_confident or action.task_kind != "follow":
    raise AssertionError("实际岩壁受阻决策没有确认可执行的通行操作")
