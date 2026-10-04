import json
from pathlib import Path
import re
import urllib.request

import cv2
from onnxocr.onnx_paddleocr import ONNXPaddleOcr

from src.utils.QuestAreaSearch import QuestAreaSearch, detect_quest_area, minimap_box
from src.utils.QuestDecisionEngine import build_jev_payload, parse_jev_response
from src.utils.QuestOcrPrivacy import prepare_quest_ocr_frame, sanitize_quest_text


root = Path(__file__).resolve().parent.parent
settings = json.loads((root / "configs" / "QuestStoryTask.json").read_text(encoding="utf-8"))
api_key = settings["API Key"]
if not api_key:
    raise ValueError("JEV API Key 尚未配置")
frame = cv2.imread(str(root / "tests" / "images" / "quest_flower_guidance.png"))
if frame is None:
    raise FileNotFoundError("任务区域截图不存在")
engine = ONNXPaddleOcr(use_angle_cls=False, use_openvino=True, use_npu=True)
height, width = frame.shape[:2]
panel = frame[int(height * .20):int(height * .43), int(width * .01):int(width * .28)]
goal = sanitize_quest_text(" ".join(entry[1][0] for entry in engine.ocr(panel, cls=False)[0]))
ocr_text = sanitize_quest_text("\n".join(entry[1][0] for entry in engine.ocr(prepare_quest_ocr_frame(frame), cls=False)[0]))
area = detect_quest_area(frame)
search = QuestAreaSearch()
x, y, w, h = minimap_box(frame)
search.observe(area, frame[y:y + h, x:x + w])
context = {
    "event": "area_entry",
    "has_f_button": False,
    "interaction_text": "",
    "area": search.context(),
    "recent_actions": [],
    "available_actions": ["wait", "search"],
}
payload = build_jev_payload(frame, goal, ocr_text=ocr_text, context=context)
body = json.dumps(payload, ensure_ascii=False)
if re.search(r"特征[码碼]|\d{8,}", body):
    raise RuntimeError("任务决策请求仍然包含特征码")
request = urllib.request.Request(settings["API URL"], data=body.encode("utf-8"),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"})
with urllib.request.urlopen(request, timeout=30) as response:
    response_data = json.load(response)
action = parse_jev_response(response_data)
print(json.dumps({"action": action.action_type, "confidence": action.confidence, "task_kind": action.task_kind,
                  "interaction_relevance": action.interaction_relevance, "tokens": action.total_tokens}, ensure_ascii=False))
if action.action_type != "search" or not action.confident or action.task_kind != "follow" or action.interaction_relevance >= .2:
    raise AssertionError("当前任务区域的真实 JEV 判断没有满足预期")
(root / "tests" / "data" / "quest_area_jev_response.json").write_text(json.dumps(response_data, ensure_ascii=False, indent=2), encoding="utf-8")
