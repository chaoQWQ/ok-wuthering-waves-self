import math
from pathlib import Path
import unittest

import cv2

from src.utils.QuestAreaSearch import QuestAreaSearch, detect_minimap_rotation, detect_quest_area, minimap_box


class TestQuestAreaSearch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = Path(__file__).parent / "images"
        cls.crop = cv2.imread(str(directory / "quest_yellow_area.png"))
        cls.frame = cv2.imread(str(directory / "quest_flower_guidance.png"))
        if cls.crop is None or cls.frame is None:
            raise FileNotFoundError("黄色圈验证截图不存在")

    def test_area_center_and_player_heading_in_user_crop(self):
        for width in (160, 228, 320):
            with self.subTest(width=width):
                scale = width / self.crop.shape[1]
                frame = cv2.resize(self.crop, (width, round(self.crop.shape[0] * scale)))
                area = detect_quest_area(frame, (0, 0, frame.shape[1], frame.shape[0]))
                self.assertIsNotNone(area)
                self.assertAlmostEqual(area.center_x, 102 * scale, delta=3)
                self.assertAlmostEqual(area.center_y, 137 * scale, delta=3)
                self.assertAlmostEqual(area.radius, 18 * scale, delta=3)
                self.assertIsNotNone(area.heading_deg)
                self.assertLess(abs(abs(area.heading_deg) - 180), 12)

    def test_area_in_actual_game_screenshot(self):
        for width in (1024, 1538, 1920):
            with self.subTest(width=width):
                scale = width / self.frame.shape[1]
                frame = cv2.resize(self.frame, (width, round(self.frame.shape[0] * scale)))
                area = detect_quest_area(frame)
                self.assertIsNotNone(area)
                self.assertIsNotNone(area.heading_deg)
                self.assertAlmostEqual(area.center_x, 108 * scale, delta=4)
                self.assertAlmostEqual(area.center_y, 146 * scale, delta=4)

    def test_quest_point_icons_are_not_search_areas(self):
        for name in ("quest_navigation_start.png", "quest_navigation_wrong_direction.png", "quest_climbing.png", "quest_rock_obstruction.png"):
            with self.subTest(name=name):
                frame = cv2.imread(str(Path(__file__).parent / "images" / name))
                self.assertIsNotNone(frame)
                self.assertIsNone(detect_quest_area(frame))

    def test_actual_minimap_edge_and_pulsing_point_are_excluded(self):
        frame = cv2.imread(str(Path(__file__).parent / "images/quest_minimap_point_pulse.png"))
        self.assertIsNotNone(frame)
        self.assertIsNone(detect_quest_area(frame, (0, 0, frame.shape[1], frame.shape[0])))

    def test_runtime_screenshot_with_arrow_occlusion(self):
        frame = cv2.imread(str(Path(__file__).parent / "images" / "quest_area_runtime.png"))
        self.assertIsNotNone(frame)
        area = detect_quest_area(frame)
        self.assertIsNotNone(area)
        self.assertAlmostEqual(area.center_x, 135, delta=3)
        self.assertAlmostEqual(area.center_y, 145, delta=3)
        search = QuestAreaSearch()
        x, y, w, h = minimap_box(frame)
        search.observe(area, frame[y:y + h, x:x + w])
        observation = search.find_observation(frame)
        self.assertIsNotNone(observation)
        self.assertTrue(search.observe(observation, frame[y:y + h, x:x + w]))
        self.assertAlmostEqual(search.rotation, 0, places=4)

    def test_map_tracking_and_initial_navigation(self):
        area = detect_quest_area(self.frame)
        x, y, w, h = minimap_box(self.frame)
        minimap = self.frame[y:y + h, x:x + w]
        self.assertAlmostEqual(detect_minimap_rotation(minimap, minimap), 0, places=4)
        search = QuestAreaSearch()
        search.observe(area, minimap)
        search.observe(area, minimap)
        tx, ty, _ = search.target()
        self.assertEqual((tx, ty), (area.center_x, area.center_y))
        mode, bearing, _ = search.navigate(0)
        self.assertEqual(mode, "calibrate")
        self.assertEqual(search.movement_keys, ["w"])

    def test_search_path_grows_within_area(self):
        search = QuestAreaSearch()
        radii = [math.hypot(x, y) for x, y in search.waypoints]
        self.assertEqual(radii[0], 0)
        self.assertGreater(radii[-1], .88)
        self.assertLessEqual(max(radii), .9)
        self.assertTrue(all(current >= previous for previous, current in zip(radii, radii[1:])))
        distances = [math.dist(a, b) for a, b in zip(search.waypoints, search.waypoints[1:])]
        self.assertLessEqual(max(distances), .19)
        self.assertGreater(sum(distances), 10)

    def test_direction_calibration_with_unchanged_screenshot_terminates(self):
        search = QuestAreaSearch()
        area = detect_quest_area(self.frame)
        x, y, w, h = minimap_box(self.frame)
        minimap = self.frame[y:y + h, x:x + w]
        keys = ("w", "w", "s", "s", "a", "a", "d", "d")
        for now, key in enumerate(keys):
            search.observe(area, minimap)
            self.assertEqual(search.navigate(now)[0], "calibrate")
            self.assertEqual(search.movement_keys, [key])
            search.record_movement([key], .3)
        search.observe(area, minimap)
        with self.assertRaisesRegex(RuntimeError, "无法确认行进方向"):
            search.navigate(8)


if __name__ == "__main__":
    unittest.main()
