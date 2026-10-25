from pathlib import Path
import unittest

import cv2

from src.utils.QuestSensorTool import find_sensor_icon, requires_sensor, sensor_wheel_selected


class TestQuestSensorTool(unittest.TestCase):
    images = Path(__file__).resolve().parent / "images"

    def read_image(self, name):
        frame = cv2.imread(str(self.images / name))
        if frame is None:
            raise FileNotFoundError(name)
        return frame

    def test_sensor_wheel_at_multiple_resolutions(self):
        for name, center in (("quest_sensor_wheel.jpg", (.269, .332)),
                             ("quest_sensor_wheel_touch.jpg", (.377, .382))):
            original = self.read_image(name)
            for scale in (.75, 1.0, 1.5):
                with self.subTest(image=name, scale=scale):
                    frame = cv2.resize(original, None, fx=scale, fy=scale)
                    height, width = frame.shape[:2]
                    match = find_sensor_icon(frame, (round(width * .15), round(height * .08),
                                                     round(width * .70), round(height * .80)), wheel=True)
                    self.assertIsNotNone(match)
                    x, y, w, h = match
                    self.assertAlmostEqual((x + w / 2) / width, center[0], delta=.025)
                    self.assertAlmostEqual((y + h / 2) / height, center[1], delta=.025)

    def test_equipped_sensor_at_multiple_resolutions(self):
        original = self.read_image("quest_area_runtime.png")
        for width in (960, 1280, 1920, 2560):
            with self.subTest(width=width):
                frame = cv2.resize(original, (width, round(width * original.shape[0] / original.shape[1])))
                height = frame.shape[0]
                box = (round(width * .77), round(height * .85), round(width * .06), round(height * .09))
                self.assertIsNotNone(find_sensor_icon(frame, box))

    def test_wheel_selected_sector_at_multiple_resolutions(self):
        for name, expected in (("quest_sensor_wheel.jpg", True), ("quest_sensor_wheel_touch.jpg", False)):
            original = self.read_image(name)
            for scale in (.75, 1.0, 1.5):
                with self.subTest(image=name, scale=scale):
                    frame = cv2.resize(original, None, fx=scale, fy=scale)
                    height, width = frame.shape[:2]
                    match = find_sensor_icon(frame, (round(width * .15), round(height * .08),
                                                     round(width * .70), round(height * .80)), wheel=True)
                    self.assertIsNotNone(match)
                    self.assertEqual(sensor_wheel_selected(frame, match), expected)

    def test_other_tools_have_no_sensor_icon(self):
        for name in ("quest_tool_camera_world.png", "in_combat.png"):
            with self.subTest(image=name):
                frame = self.read_image(name)
                height, width = frame.shape[:2]
                box = (round(width * .77), round(height * .85), round(width * .06), round(height * .09))
                self.assertIsNone(find_sensor_icon(frame, box))

    def test_sensor_instructions(self):
        for text in ("使用感知寻找线索", "使用探索工具「感知」调查附近", "使用「感知」探索工具",
                     "利用感知扫描附近的痕迹", "使用感知尋找線索", "Use the Sensor to find clues",
                     "Find the operation traces with Sensor"):
            with self.subTest(text=text):
                self.assertTrue(requires_sensor(text))

    def test_other_objectives_and_negative_instructions(self):
        for text in ("跟随花朵的指引", "与感知者对话", "不要使用感知", "无需使用感知寻找线索",
                     "Do not use the Sensor", "Avoid using Sensor", ""):
            with self.subTest(text=text):
                self.assertFalse(requires_sensor(text))


if __name__ == "__main__":
    unittest.main(verbosity=2)
