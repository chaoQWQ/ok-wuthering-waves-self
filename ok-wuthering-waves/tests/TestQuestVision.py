import os
import unittest
import cv2
import numpy as np

from src.utils.QuestVision import (
    BeaconResult,
    LetterboxResult,
    detect_edge_turn_hint,
    detect_interact_action,
    detect_letterbox,
    detect_minimap_quest_arrow,
    detect_quest_beacon,
    detect_screen_freeze,
    parse_distance_text,
)


class TestQuestVision(unittest.TestCase):

    def setUp(self):
        self.img_letterbox_path = r"C:\Users\zc\.gemini\antigravity\brain\c0a6ae12-fc8b-477d-b1ca-5cbeb3872327\.user_uploaded\media_1791092288151.png"
        self.img_normal_path = r"C:\Users\zc\.gemini\antigravity\brain\c0a6ae12-fc8b-477d-b1ca-5cbeb3872327\.user_uploaded\media_1791091315830.jpg"
        self.img_beacon_path = r"C:\Users\zc\.gemini\antigravity\brain\c0a6ae12-fc8b-477d-b1ca-5cbeb3872327\.user_uploaded\media_1791091183192.jpg"

    def test_detect_letterbox_true(self):
        if not os.path.exists(self.img_letterbox_path):
            self.skipTest("剧情动画样本图片不存在")
        frame = cv2.imread(self.img_letterbox_path)
        res = detect_letterbox(frame)
        self.assertTrue(res.is_letterbox)
        self.assertLess(res.top_brightness, 5.0)
        self.assertLess(res.bottom_brightness, 5.0)
        self.assertGreater(res.center_brightness, 20.0)

    def test_detect_letterbox_false(self):
        if not os.path.exists(self.img_normal_path):
            self.skipTest("大世界样本图片不存在")
        frame = cv2.imread(self.img_normal_path)
        res = detect_letterbox(frame)
        self.assertFalse(res.is_letterbox)
        self.assertGreater(res.top_brightness, 15.0)

    def test_detect_letterbox_invalid_input(self):
        with self.assertRaises(ValueError):
            detect_letterbox(None)
        with self.assertRaises(ValueError):
            detect_letterbox(np.zeros((4, 4, 3), dtype=np.uint8))

    def test_detect_screen_freeze(self):
        frame1 = np.ones((100, 100, 3), dtype=np.uint8) * 120
        frame2 = np.ones((100, 100, 3), dtype=np.uint8) * 121
        frame3 = np.ones((100, 100, 3), dtype=np.uint8) * 160

        frozen, diff = detect_screen_freeze(frame1, frame2, diff_threshold=2.0)
        self.assertTrue(frozen)
        self.assertAlmostEqual(diff, 1.0, places=1)

        not_frozen, diff2 = detect_screen_freeze(frame1, frame3, diff_threshold=2.0)
        self.assertFalse(not_frozen)
        self.assertGreater(diff2, 30.0)

    def test_detect_screen_freeze_shape_mismatch(self):
        f1 = np.zeros((100, 100, 3), dtype=np.uint8)
        f2 = np.zeros((200, 200, 3), dtype=np.uint8)
        with self.assertRaises(ValueError):
            detect_screen_freeze(f1, f2)

    def test_parse_distance_text(self):
        self.assertEqual(parse_distance_text("178米"), 178.0)
        self.assertEqual(parse_distance_text("1米"), 1.0)
        self.assertEqual(parse_distance_text("25.5米"), 25.5)
        self.assertEqual(parse_distance_text("100m"), 100.0)
        self.assertEqual(parse_distance_text("目标距离 300 米"), 300.0)
        self.assertIsNone(parse_distance_text(""))
        self.assertIsNone(parse_distance_text("无数字文本"))

    def test_detect_edge_turn_hint(self):
        frame = np.zeros((600, 1000, 3), dtype=np.uint8)

        beacon_right = BeaconResult(found=True, x=850, y=300, width=20, height=20, confidence=0.9)
        hint_right = detect_edge_turn_hint(frame, beacon_right)
        self.assertTrue(hint_right.has_hint)
        self.assertEqual(hint_right.direction, "right")

        beacon_left = BeaconResult(found=True, x=150, y=300, width=20, height=20, confidence=0.9)
        hint_left = detect_edge_turn_hint(frame, beacon_left)
        self.assertTrue(hint_left.has_hint)
        self.assertEqual(hint_left.direction, "left")

        beacon_center = BeaconResult(found=True, x=500, y=300, width=20, height=20, confidence=0.9)
        hint_center = detect_edge_turn_hint(frame, beacon_center)
        self.assertFalse(hint_center.has_hint)
        self.assertEqual(hint_center.direction, "none")

    def test_detect_interact_action_on_real_image(self):
        if not os.path.exists(self.img_normal_path):
            self.skipTest("大世界样本图片不存在")
        frame = cv2.imread(self.img_normal_path)
        res = detect_interact_action(frame)
        self.assertTrue(res.has_f)
        self.assertIsNotNone(res.box)


if __name__ == "__main__":
    unittest.main()
