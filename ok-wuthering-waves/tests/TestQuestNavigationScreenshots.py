from pathlib import Path
import unittest

import cv2

from src.utils.QuestNavigator import calculate_camera_turn, compute_movement_action
from src.utils.QuestVision import detect_minimap_quest_arrow, detect_quest_beacon


class TestQuestNavigationScreenshots(unittest.TestCase):
    def test_world_beacons_and_turns(self):
        samples = (
            ("quest_navigation_start.png", (458, 315), "left"),
            ("quest_navigation_wrong_direction.png", (420, 626), "left"),
            ("quest_after_story_combat.png", (1456, 309), "right"),
        )
        for name, center, direction in samples:
            original = cv2.imread(str(Path(__file__).parent / "images" / name))
            self.assertIsNotNone(original)
            for width in (1024, 1280, 1920):
                with self.subTest(name=name, width=width):
                    scale = width / original.shape[1]
                    frame = cv2.resize(original, (width, round(original.shape[0] * scale)))
                    beacon = detect_quest_beacon(frame)
                    self.assertTrue(beacon.found)
                    cx = beacon.x + beacon.width / 2
                    cy = beacon.y + beacon.height / 2
                    self.assertAlmostEqual(cx, center[0] * scale, delta=5)
                    self.assertAlmostEqual(cy, center[1] * scale, delta=5)
                    turn = calculate_camera_turn(width, beacon_center_x=cx)
                    self.assertTrue(turn.need_turn)
                    self.assertEqual(turn.turn_direction, direction)

    def test_minimap_relative_bearings(self):
        samples = (
            ("quest_navigation_start.png", (90, 103), 325),
            ("quest_navigation_wrong_direction.png", (67, 165), 231),
        )
        for name, center, bearing in samples:
            with self.subTest(name=name):
                frame = cv2.imread(str(Path(__file__).parent / "images" / name))
                self.assertIsNotNone(frame)
                arrow = detect_minimap_quest_arrow(frame)
                self.assertTrue(arrow.found)
                self.assertAlmostEqual(arrow.center_x, center[0], delta=5)
                self.assertAlmostEqual(arrow.center_y, center[1], delta=5)
                self.assertAlmostEqual(arrow.bearing_deg, bearing, delta=10)
                turn = calculate_camera_turn(frame.shape[1], minimap_bearing_deg=arrow.bearing_deg, max_delta_x=120)
                self.assertEqual(turn.turn_direction, "left")
                self.assertLessEqual(abs(turn.delta_x_pixels), 120)

    def test_centered_goal_movement(self):
        turn = calculate_camera_turn(1920, beacon_center_x=960)
        self.assertFalse(turn.need_turn)
        self.assertEqual(compute_movement_action(39).keys, ["w", "shift"])
        self.assertEqual(compute_movement_action(12).keys, ["w"])
        self.assertEqual(compute_movement_action(2).keys, [])


if __name__ == "__main__":
    unittest.main()
