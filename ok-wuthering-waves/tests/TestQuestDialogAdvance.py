from pathlib import Path
import unittest

import cv2

from src.utils.QuestAreaSearch import minimap_visible
from src.utils.QuestVision import detect_dialog_advance_indicator


class TestQuestDialogAdvance(unittest.TestCase):
    directory = Path(__file__).resolve().parent / "images"

    def read_image(self, path):
        frame = cv2.imread(str(path))
        self.assertIsNotNone(frame, str(path))
        return frame

    def test_dialogue_with_controls_at_multiple_resolutions(self):
        original = self.read_image(self.directory / "quest_dialog_advance.png")
        for width in (1024, 1280, 1536, 1920):
            with self.subTest(width=width):
                height = round(original.shape[0] * width / original.shape[1])
                frame = cv2.resize(original, (width, height))
                result = detect_dialog_advance_indicator(frame)
                self.assertTrue(result.found)
                self.assertTrue(.48 < result.x / width < .52)
                self.assertTrue(.80 < result.y / height < .87)

    def test_world_arrow_does_not_advance_dialogue(self):
        frame = self.read_image(self.directory / "quest_tool_blue_world.png")
        self.assertTrue(minimap_visible(frame))
        result = detect_dialog_advance_indicator(frame)
        self.assertGreaterEqual(result.confidence, .75)
        self.assertFalse(result.found)

    def test_map_arrow_without_dialogue_controls(self):
        frame = self.read_image(self.directory / "quest_map_detail.png")
        self.assertFalse(minimap_visible(frame))
        result = detect_dialog_advance_indicator(frame)
        self.assertFalse(result.found)

    def test_quest_world_and_confirmation_screens_at_multiple_resolutions(self):
        for path in sorted(self.directory.glob("quest*")):
            if path.suffix.lower() not in (".png", ".jpg") or path.name == "quest_dialog_advance.png":
                continue
            original = self.read_image(path)
            for width in (1024, 1280, 1536, 1920):
                with self.subTest(image=path.name, width=width):
                    height = round(original.shape[0] * width / original.shape[1])
                    frame = cv2.resize(original, (width, height))
                    self.assertFalse(detect_dialog_advance_indicator(frame).found)


if __name__ == "__main__":
    unittest.main(verbosity=2)
