import os
import unittest
import cv2
import numpy as np

from src.utils.QuestVision import (
    BeaconResult,
    ClimbStateResult,
    LetterboxResult,
    detect_climbing_state,
    detect_dialog_advance_indicator,
    detect_edge_turn_hint,
    detect_interact_action,
    detect_letterbox,
    detect_minimap_quest_arrow,
    detect_quest_beacon,
    detect_screen_freeze,
    detect_top_left_skip_button,
    parse_distance_text,
)


class TestQuestVision(unittest.TestCase):

    def setUp(self):
        self.img_dialog_advance_path = r"C:\Users\zc\.gemini\antigravity\brain\c0a6ae12-fc8b-477d-b1ca-5cbeb3872327\.user_uploaded\media_1791092805274.png"
        self.img_letterbox_path = r"C:\Users\zc\.gemini\antigravity\brain\c0a6ae12-fc8b-477d-b1ca-5cbeb3872327\.user_uploaded\media_1791092288151.png"
        self.img_normal_path = r"C:\Users\zc\.gemini\antigravity\brain\c0a6ae12-fc8b-477d-b1ca-5cbeb3872327\.user_uploaded\media_1791091315830.jpg"
        self.img_beacon_path = r"C:\Users\zc\.gemini\antigravity\brain\c0a6ae12-fc8b-477d-b1ca-5cbeb3872327\.user_uploaded\media_1791091183192.jpg"
        self.img_top_left_skip_path = r"C:\Users\zc\.gemini\antigravity\brain\c0a6ae12-fc8b-477d-b1ca-5cbeb3872327\.user_uploaded\media_1791096463074.png"

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

    def test_detect_interact_action_negative(self):
        if not os.path.exists(self.img_beacon_path):
            self.skipTest("大世界远距离信标样本不存在")
        frame = cv2.imread(self.img_beacon_path)
        res = detect_interact_action(frame)
        self.assertFalse(res.has_f)
        self.assertIsNone(res.box)

    def test_detect_quest_beacon_on_real_image(self):
        if not os.path.exists(self.img_beacon_path):
            self.skipTest("大世界远距离信标样本不存在")
        frame = cv2.imread(self.img_beacon_path)
        res = detect_quest_beacon(frame)
        self.assertTrue(res.found)
        self.assertGreater(res.x, 700)
        self.assertGreaterEqual(res.confidence, 0.75)

    def test_detect_dialog_advance_indicator_positive(self):
        if not os.path.exists(self.img_dialog_advance_path):
            self.skipTest("剧情推进样本图片不存在")
        frame = cv2.imread(self.img_dialog_advance_path)
        res = detect_dialog_advance_indicator(frame)
        self.assertTrue(res.found)
        self.assertGreaterEqual(res.confidence, 0.75)
        self.assertGreater(res.x, 450)
        self.assertLess(res.x, 550)

    def test_detect_dialog_advance_indicator_positive_1080p_debug(self):
        debug_path = os.path.join(os.path.dirname(__file__), "..", "logs", "wait_state_debug.png")
        if not os.path.exists(debug_path):
            self.skipTest("1080p等待状态诊断图片不存在")
        frame = cv2.imread(debug_path)
        res = detect_dialog_advance_indicator(frame)
        self.assertTrue(res.found)
        self.assertGreaterEqual(res.confidence, 0.75)
        self.assertGreater(res.x, 900)
        self.assertLess(res.x, 1000)
        self.assertGreater(res.y, 1000)

    def test_detect_dialog_advance_indicator_negative(self):
        if not os.path.exists(self.img_normal_path):
            self.skipTest("大世界样本图片不存在")
        frame = cv2.imread(self.img_normal_path)
        res = detect_dialog_advance_indicator(frame)
        self.assertFalse(res.found)

    def test_detect_dialog_advance_indicator_invalid_input(self):
        with self.assertRaises(ValueError):
            detect_dialog_advance_indicator(None)
        with self.assertRaises(FileNotFoundError):
            frame = np.zeros((100, 100, 3), dtype=np.uint8)
            detect_dialog_advance_indicator(frame, template_path="non_existent.png")

    def test_detect_top_left_skip_button_positive(self):
        if not os.path.exists(self.img_top_left_skip_path):
            self.skipTest("左上角跳过按钮样本图片不存在")
        frame = cv2.imread(self.img_top_left_skip_path)
        res = detect_top_left_skip_button(frame)
        self.assertTrue(res.found)
        self.assertGreaterEqual(res.confidence, 0.75)
        self.assertLess(res.x, 150)
        self.assertLess(res.y, 100)

    def test_detect_top_left_skip_button_negative(self):
        if not os.path.exists(self.img_normal_path):
            self.skipTest("大世界样本图片不存在")
        frame = cv2.imread(self.img_normal_path)
        res = detect_top_left_skip_button(frame)
        self.assertFalse(res.found)

    def test_detect_top_left_skip_button_invalid_input(self):
        with self.assertRaises(ValueError):
            detect_top_left_skip_button(None)
        with self.assertRaises(FileNotFoundError):
            frame = np.zeros((100, 100, 3), dtype=np.uint8)
            detect_top_left_skip_button(frame, template_path="non_existent.png")

    def test_detect_climbing_state_positive(self):
        img_climb_path = r"C:\Users\zc\.gemini\antigravity\brain\c0a6ae12-fc8b-477d-b1ca-5cbeb3872327\.user_uploaded\media_1791109409398.jpg"
        if not os.path.exists(img_climb_path):
            self.skipTest("攀爬状态样本图片不存在")
        frame = cv2.imread(img_climb_path)
        res = detect_climbing_state(frame)
        self.assertTrue(res.is_climbing)
        self.assertGreaterEqual(res.confidence, 0.75)
        self.assertGreater(res.x, int(frame.shape[1] * 0.75))
        self.assertGreater(res.y, int(frame.shape[0] * 0.80))

    def test_detect_climbing_state_negative(self):
        img_stand_path = r"C:\Users\zc\.gemini\antigravity\brain\c0a6ae12-fc8b-477d-b1ca-5cbeb3872327\.user_uploaded\media_1791109804023.jpg"
        if not os.path.exists(img_stand_path):
            self.skipTest("站立状态样本图片不存在")
        frame = cv2.imread(img_stand_path)
        res = detect_climbing_state(frame)
        self.assertFalse(res.is_climbing)
        self.assertLess(res.confidence, 0.60)

    def test_detect_climbing_state_invalid_input(self):
        with self.assertRaises(ValueError):
            detect_climbing_state(None)
        with self.assertRaises(FileNotFoundError):
            frame = np.zeros((100, 100, 3), dtype=np.uint8)
            detect_climbing_state(frame, template_path="non_existent.png")


if __name__ == "__main__":
    unittest.main()

