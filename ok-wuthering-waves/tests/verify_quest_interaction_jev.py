import json
from pathlib import Path
import re
import sys
import urllib.request

import cv2
from onnxocr.onnx_paddleocr import ONNXPaddleOcr

from src.utils.QuestDecisionEngine import build_jev_payload, parse_jev_response
from src.utils.QuestOcrPrivacy import is_named_quest_interaction
from tests.quest_interaction_observations import read_interaction_observations


sys.stdout.reconfigure(encoding="utf-8")
root = Path(__file__).resolve().parent.parent
settings = json.loads((root / "configs/QuestStoryTask.json").read_text(encoding="utf-8"))
frame = cv2.imread(str(root / "tests/images/quest_yangyang_interaction.png"))
if frame is None:
    raise FileNotFoundError("秧秧交互截图不存在")
engine = ONNXPaddleOcr(use_angle_cls=False, use_openvino=True, use_npu=True)
goal, interaction, distance, text = read_interaction_observations(frame, engine)
print(json.dumps({"goal": goal, "has_f": interaction.has_f, "interaction": interaction.action_text,
                  "distance": distance, "observed_text": text}, ensure_ascii=False))
if not interaction.has_f or "秧秧" not in interaction.action_text or distance != 1:
    raise AssertionError("实际截图中的 F 秧秧交互没有被完整识别")
context = {"event": "near_interaction", "has_f_button": interaction.has_f,
           "interaction_text": interaction.action_text, "distance_meters": distance,
           "named_target_match": is_named_quest_interaction(goal, interaction.action_text),
           "area": None, "recent_actions": [], "available_actions": ["wait", "search", "interact"]}
payload = build_jev_payload(frame, goal, ocr_text=text, context=context)
body = json.dumps(payload, ensure_ascii=False)
if re.search(r"特征[码碼]|\d{8,}", body):
    raise ValueError("交互决策请求包含特征码")
request = urllib.request.Request(settings["API URL"], data=body.encode("utf-8"),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {settings['API Key']}"})
with urllib.request.urlopen(request, timeout=30) as response:
    result = json.load(response)
action = parse_jev_response(result)
(root / "tests/data/quest_interaction_jev_response.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"action": action.action_type, "confidence": action.confidence,
                  "relevance": action.interaction_relevance, "tokens": action.total_tokens}, ensure_ascii=False))
model_relevant = action.interaction_relevance is not None and action.interaction_relevance >= .8
named_target_match = distance <= 8 and is_named_quest_interaction(goal, interaction.action_text)
if action.action_type != "interact" or not action.confident or not (model_relevant or named_target_match):
    raise AssertionError("实际秧秧交互决策没有确认按下 F")
