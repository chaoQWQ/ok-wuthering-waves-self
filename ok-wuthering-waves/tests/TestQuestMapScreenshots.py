from pathlib import Path
import unittest

import cv2
from onnxocr.onnx_paddleocr import ONNXPaddleOcr


class TestQuestMapScreenshots(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = ONNXPaddleOcr(use_angle_cls=False, use_openvino=True, use_npu=True)

    def read_controls(self, name):
        frame = cv2.imread(str(Path(__file__).parent / "images" / name))
        self.assertIsNotNone(frame)
        height, width = frame.shape[:2]
        region = frame[round(height * .82):round(height * .99), round(width * .65):round(width * .99)]
        return self.engine.ocr(region, cls=False)[0], width

    def test_actual_quest_card_proceed_and_cancel_are_separate(self):
        controls, width = self.read_controls("quest_map_detail.png")
        proceed = [points for points, (name, _) in controls if "前往" in name]
        cancel = [points for points, (name, _) in controls if "取消追踪" in name]
        self.assertEqual(len(proceed), 1)
        self.assertEqual(len(cancel), 1)
        self.assertGreater(min(point[0] for point in proceed[0]) + width * .65, width * .85)
        self.assertLess(max(point[0] for point in cancel[0]), min(point[0] for point in proceed[0]))

    def test_plain_map_has_visible_map_control(self):
        controls, _ = self.read_controls("big_map.png")
        self.assertTrue(any("切换地图" in name for _, (name, _) in controls))

    def test_world_frames_do_not_have_map_controls(self):
        for name in ("quest_navigation_start.png", "quest_navigation_wrong_direction.png",
                     "quest_after_story_combat.png"):
            with self.subTest(name=name):
                controls, _ = self.read_controls(name)
                self.assertFalse(any(any(text in name for text in ("前往", "取消追踪", "切换地图", "快速旅行", "传送"))
                                     for _, (name, _) in controls))
