import json
from pathlib import Path
import unittest

import cv2
from onnxocr.onnx_paddleocr import ONNXPaddleOcr

from src.utils.QuestAreaSearch import QuestAreaSearch, detect_quest_area, minimap_box
from src.utils.QuestDecisionEngine import build_jev_payload, parse_jev_response
from src.utils.QuestDecisionSession import QuestDecisionSession
from src.utils.QuestOcrPrivacy import prepare_quest_ocr_frame, sanitize_quest_text


class TestQuestDecisionEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = Path(__file__).parent
        cls.frame = cv2.imread(str(directory / "images" / "quest_flower_guidance.png"))
        if cls.frame is None:
            raise FileNotFoundError("任务区域截图不存在")
        cls.response = json.loads((directory / "data" / "quest_area_jev_response.json").read_text(encoding="utf-8"))
        cls.action = parse_jev_response(cls.response)
        engine = ONNXPaddleOcr(use_angle_cls=False, use_openvino=True, use_npu=True)
        cls.text = sanitize_quest_text("\n".join(entry[1][0] for entry in engine.ocr(prepare_quest_ocr_frame(cls.frame), cls=False)[0]))
        cls.search = QuestAreaSearch()
        x, y, w, h = minimap_box(cls.frame)
        cls.search.observe(detect_quest_area(cls.frame), cls.frame[y:y + h, x:x + w])

    def test_actual_response_retains_confidence_and_classification(self):
        self.assertEqual(self.action.action_type, "search")
        self.assertTrue(self.action.confident)
        self.assertEqual(self.action.task_kind, "follow")
        self.assertLess(self.action.interaction_relevance, .2)
        self.assertGreater(self.action.total_tokens, 0)
        self.assertAlmostEqual(sum(self.action.probabilities.values()), 1, delta=.01)

    def test_actual_rock_recovery_response_selects_jump(self):
        response = json.loads((Path(__file__).parent / "data/quest_recovery_jev_response.json").read_text(encoding="utf-8"))
        action = parse_jev_response(response)
        self.assertEqual(action.action_type, "jump")
        self.assertEqual(action.key, "space")
        self.assertTrue(action.recovery_confident)
        self.assertEqual(action.task_kind, "follow")
        self.assertGreater(action.total_tokens, 0)

    def test_structured_request_contains_real_observations(self):
        context = {"area": self.search.context(), "available_actions": ["search", "wait"], "has_f_button": False}
        payload = build_jev_payload(self.frame, self.text, ocr_text=self.text, context=context)
        self.assertIn("跟随", payload["state"]["quest_goal"])
        self.assertEqual(payload["state"]["observations"]["area"]["phase"], "center")
        self.assertEqual(set(payload["questions"]), {"action", "task_kind", "interaction_relevant"})
        self.assertEqual(set(payload["questions"]["action"]["criteria"]), {"search", "wait"})

    def test_privacy_filter_applies_to_nested_history(self):
        frame = cv2.imread(str(Path(__file__).parent / "images" / "quest_navigation_start.png"))
        engine = ONNXPaddleOcr(use_angle_cls=False, use_openvino=True, use_npu=True)
        original_text = "\n".join(entry[1][0] for entry in engine.ocr(frame, cls=False)[0])
        context = {"recent_actions": [{"goal": original_text, "interaction": original_text}], "available_actions": ["wait"]}
        payload = build_jev_payload(frame, original_text, ocr_text=original_text, context=context)
        encoded = json.dumps(payload, ensure_ascii=False)
        self.assertNotRegex(encoded, r"\d{8,}|特征码|特征碼")
        self.assertIn("欲知天将雨", encoded)

    def test_identical_state_does_not_repeat_model_call(self):
        session = QuestDecisionSession()
        session.observe(self.text, "", 0)
        self.assertTrue(session.can_call("area_entry", "", "center", 0))
        self.assertFalse(session.can_call("area_entry", "", "center", 10))
        self.assertTrue(session.can_call("area_ring", "", "1", 10))

    def test_action_results_and_scene_transitions(self):
        session = QuestDecisionSession()
        session.observe(self.text, "", 0)
        session.record(self.action.action_type, "", "center", 0)
        session.observe(self.text, "", 2)
        self.assertEqual(session.context()[-1]["outcome"], "no_observed_progress")
        session.record(self.action.action_type, "", "center", 3)
        session.mark_transition("dialog_started")
        self.assertEqual(session.context()[-1]["outcome"], "dialog_started")
        self.assertIsNone(session.pending)

    def test_repeated_failed_actions_are_excluded(self):
        session = QuestDecisionSession()
        session.observe(self.text, "", 0)
        for now in (0, 3):
            session.record(self.action.action_type, "", "center", now)
            session.observe(self.text, "", now + 2)
        self.assertIn("search", session.failed_actions("", "center"))
        self.assertNotIn("search", session.failed_actions("", "1"))


if __name__ == "__main__":
    unittest.main()
