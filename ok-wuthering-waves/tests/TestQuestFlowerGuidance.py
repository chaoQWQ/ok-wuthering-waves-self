from pathlib import Path
import unittest

import cv2
from onnxocr.onnx_paddleocr import ONNXPaddleOcr

from src.utils.QuestNavigator import calculate_camera_turn
from src.utils.QuestTargetSearch import QuestTargetSearch
from src.utils.QuestVision import detect_flower_guidance, detect_quest_beacon


class TestQuestFlowerGuidance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frame = cv2.imread(str(Path(__file__).parent / "images" / "quest_flower_guidance.png"))
        if cls.frame is None:
            raise FileNotFoundError("花朵指引截图不存在")

    def test_current_task_text_and_missing_gold_marker(self):
        height, width = self.frame.shape[:2]
        roi = self.frame[int(height * 0.20):int(height * 0.43), int(width * 0.01):int(width * 0.28)]
        engine = ONNXPaddleOcr(use_angle_cls=False, use_openvino=True, use_npu=True)
        text = " ".join(entry[1][0] for entry in engine.ocr(roi, cls=False)[0])
        self.assertRegex(text, "跟随.*花朵")
        self.assertFalse(detect_quest_beacon(self.frame).found)

    def test_blue_guidance_target(self):
        for width in (1024, self.frame.shape[1]):
            with self.subTest(width=width):
                scale = width / self.frame.shape[1]
                frame = cv2.resize(self.frame, (width, round(self.frame.shape[0] * scale)))
                target = detect_flower_guidance(frame)
                self.assertTrue(target.found)
                self.assertAlmostEqual(target.x + target.width / 2, 359 * scale, delta=5)
                self.assertAlmostEqual(target.y + target.height / 2, 270 * scale, delta=5)
                self.assertEqual(calculate_camera_turn(width, beacon_center_x=target.x + target.width / 2).turn_direction, "left")

    def test_missing_target_search_terminates(self):
        search = QuestTargetSearch()
        for _ in range(32):
            self.assertEqual(search.next_turn(), 120)
        with self.assertRaisesRegex(RuntimeError, "仍未识别"):
            search.next_turn()
        search.reset()
        self.assertEqual(search.next_turn(), 120)


if __name__ == "__main__":
    unittest.main()
