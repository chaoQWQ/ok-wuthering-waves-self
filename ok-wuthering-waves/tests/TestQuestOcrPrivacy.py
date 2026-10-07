from pathlib import Path
import re
import unittest

import cv2
import numpy as np
from onnxocr.onnx_paddleocr import ONNXPaddleOcr

from src.utils.QuestDecisionEngine import build_jev_payload
from src.utils.QuestOcrPrivacy import (
    is_named_quest_interaction,
    prepare_quest_ocr_frame,
    quest_goal_from_lines,
    sanitize_quest_text,
)
from src.utils.QuestVision import parse_distance_text


class TestQuestOcrPrivacy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frame = cv2.imread(str(Path(__file__).parent / "images" / "quest_navigation_start.png"))
        if cls.frame is None:
            raise FileNotFoundError("任务导航截图不存在")
        cls.protected_frame = prepare_quest_ocr_frame(cls.frame)
        engine = ONNXPaddleOcr(use_angle_cls=False, use_openvino=True, use_npu=True)
        cls.original_text = "\n".join(entry[1][0] for entry in engine.ocr(cls.frame, cls=False)[0])
        cls.protected_text = "\n".join(entry[1][0] for entry in engine.ocr(cls.protected_frame, cls=False)[0])

    def test_identity_region_is_excluded_before_ocr(self):
        height, width = self.frame.shape[:2]
        self.assertTrue(np.all(self.protected_frame[int(height * 0.90):, int(width * 0.70):] == 0))
        self.assertTrue(np.array_equal(self.frame[:int(height * 0.90)], self.protected_frame[:int(height * 0.90)]))
        self.assertRegex(self.original_text, r"\d{8,}")
        self.assertNotRegex(self.protected_text, r"\d{8,}")

    def test_request_removes_identity_from_original_ocr(self):
        payload = build_jev_payload(self.frame, self.original_text, ocr_text=self.original_text)
        self.assertNotRegex(payload["state"], r"\d{8,}|特征码|特征碼")
        self.assertIn("欲知天将雨", payload["state"])
        self.assertIn("39", payload["state"])

    def test_normalized_and_separated_identity_numbers(self):
        identity = re.search(r"\d{8,}", self.original_text).group()
        fullwidth_identity = identity.translate(str.maketrans("0123456789", "０１２３４５６７８９"))
        for text in (identity, fullwidth_identity, " ".join(identity), "-".join(identity)):
            with self.subTest(text_length=len(text)):
                self.assertEqual(sanitize_quest_text(text), "")

    def test_actual_rock_objective_excludes_navigation_distance(self):
        frame = cv2.imread(str(Path(__file__).parent / "images" / "quest_rock_obstruction.png"))
        self.assertIsNotNone(frame)
        height, width = frame.shape[:2]
        panel = frame[int(height * .20):int(height * .43), int(width * .01):int(width * .28)]
        engine = ONNXPaddleOcr(use_angle_cls=False, use_openvino=True, use_npu=True)
        lines = [entry[1][0] for entry in engine.ocr(panel, cls=False)[0]]
        self.assertTrue(any("70" in line for line in lines))
        goal = quest_goal_from_lines(lines)
        self.assertIn("跟随花朵的指引", goal)
        self.assertNotIn("70", goal)

    def test_recorded_objective_symbols_keep_task_identity(self):
        lines = (Path(__file__).parent / "data" / "quest_objective_ocr_log.txt").read_text(encoding="utf-8-sig").splitlines()
        goals = [line.split("任务要求已经更新：", 1)[1] for line in lines if "寻找音源" in line]
        self.assertGreaterEqual(len(goals), 4)
        for goal in goals:
            with self.subTest(goal=goal):
                self.assertEqual(quest_goal_from_lines(goal.split()), "欲知天将雨 寻找音源")

    def test_actual_post_combat_task_panel(self):
        frame = cv2.imread(str(Path(__file__).parent / "images" / "quest_after_story_combat.png"))
        self.assertIsNotNone(frame)
        height, width = frame.shape[:2]
        engine = ONNXPaddleOcr(use_angle_cls=False, use_openvino=True, use_npu=True)
        panel = frame[int(height * .23):int(height * .39), int(width * .01):int(width * .25)]
        lines = [entry[1][0] for entry in engine.ocr(panel, cls=False)[0]
                 if min(point[0] for point in entry[0]) + width * .01 <= width * .05]
        goal = quest_goal_from_lines(lines)
        self.assertIn("欲知天将雨", goal)
        self.assertIn("引出跟踪者", goal)
        self.assertNotIn("93", goal)
        distances = [parse_distance_text(line, require_unit=True) for line in lines]
        self.assertEqual([value for value in distances if value is not None], [93])

    def test_traditional_interaction_name_normalized_to_simplified(self):
        # 游戏内 NPC 名以繁体渲染（OCR 读出"煙舒"），任务指引是简体"和烟舒聊聊"
        self.assertEqual(sanitize_quest_text("煙舒"), "烟舒")
        self.assertEqual(sanitize_quest_text("和煙舒聊聊"), "和烟舒聊聊")
        self.assertTrue(is_named_quest_interaction("和烟舒聊聊", "煙舒"))
        self.assertTrue(is_named_quest_interaction("和煙舒聊聊", "烟舒"))

    def test_actual_hud_numbers_are_excluded_from_quest_distance(self):
        lines = self.protected_text.splitlines()
        numeric_lines = [line for line in lines if re.fullmatch(r"[\d/ ]+", line)]
        self.assertTrue(numeric_lines)
        for line in numeric_lines:
            self.assertIsNone(parse_distance_text(line, require_unit=True))


if __name__ == "__main__":
    unittest.main()
