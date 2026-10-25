from pathlib import Path
import unittest

import cv2

from src.utils.QuestDialogAutoPlay import detect_dialog_auto_play


class TestQuestDialogAutoPlay(unittest.TestCase):
    root = Path(__file__).resolve().parent

    @classmethod
    def read_image(cls, name):
        path = cls.root / "images" / name
        frame = cv2.imread(str(path))
        if frame is None:
            raise FileNotFoundError(str(path))
        return frame

    def test_actual_dialog_modes_at_multiple_resolutions(self):
        for mode in ("manual", "auto"):
            original = self.read_image(f"quest_dialog_{mode}.png")
            for width in (1024, 1280, 1536, 1920, 2560, 3840):
                with self.subTest(mode=mode, width=width):
                    frame = cv2.resize(original, (width, round(width * 1080 / 1920)))
                    result = detect_dialog_auto_play(frame)
                    self.assertTrue(result.found, result)
                    self.assertEqual(result.mode, mode)
                    self.assertEqual(result.enabled, mode == "auto")
                    cx = result.x + result.width / 2
                    cy = result.y + result.height / 2
                    self.assertAlmostEqual((width - cx) / frame.shape[0], 217 / 1080, delta=.008)
                    self.assertAlmostEqual(cy / frame.shape[0], 66 / 1080, delta=.008)

    def test_button_position_is_relative_to_right_edge(self):
        for mode in ("manual", "auto"):
            with self.subTest(mode=mode):
                frame = self.read_image(f"quest_dialog_{mode}.png")[:, 400:]
                result = detect_dialog_auto_play(frame)
                self.assertTrue(result.found, result)
                self.assertEqual(result.mode, mode)
                self.assertAlmostEqual(frame.shape[1] - result.x - result.width / 2, 217, delta=3)

    def test_world_and_menu_images_have_no_auto_dialog_button(self):
        for name in ("quest_navigation_aligned.png", "quest_story_synopsis.png",
                     "quest_teleport_closer_dialog.png", "quest_domain_confirmation.png",
                     "quest_after_story_combat.png", "quest_story_normal_team_bar.png"):
            with self.subTest(image=name):
                result = detect_dialog_auto_play(self.read_image(name))
                self.assertFalse(result.found, result)
                self.assertFalse(result.enabled)

    def test_missing_frame_raises(self):
        with self.assertRaises(ValueError):
            detect_dialog_auto_play(None)


if __name__ == "__main__":
    unittest.main()
