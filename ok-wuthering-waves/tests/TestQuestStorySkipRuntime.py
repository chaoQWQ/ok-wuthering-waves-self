from copy import deepcopy
from pathlib import Path
import sys
import unittest

import cv2

sys.argv[0] = str(Path(__file__).resolve().parent.parent / "main.py")

from config import config
from ok import OK
from src.task.QuestStoryTask import QuestStoryTask


class TestQuestStorySkipRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parent.parent
        runtime_config = deepcopy(config)
        runtime_config.update({
            "check_mutex": False,
            "gui": None,
            "onetime_tasks": [["src.task.QuestStoryTask", "QuestStoryTask"]],
            "trigger_tasks": [],
            "log_file": "logs/quest-skip-verification.log",
        })
        cls.runtime = OK(runtime_config)
        cls.task = cls.runtime.get_onetime_task(QuestStoryTask)
        cls.runtime.task_executor.paused = False
        cls.synopsis = cls.read_image("quest_story_synopsis.png")

    @classmethod
    def tearDownClass(cls):
        cls.runtime.quit()

    @classmethod
    def read_image(cls, name):
        path = cls.root / "tests/images" / name
        frame = cv2.imread(str(path))
        if frame is None:
            raise FileNotFoundError(str(path))
        return frame

    def test_synopsis_skip_button_at_multiple_resolutions(self):
        for width in (1024, 1280, 1536, 1920):
            with self.subTest(width=width):
                height = round(self.synopsis.shape[0] * width / self.synopsis.shape[1])
                frame = cv2.resize(self.synopsis, (width, height))
                button = self.task._find_story_skip_confirm(frame)
                self.assertIsNotNone(button)
                self.assertEqual(button.name.replace(" ", ""), "跳过剧情")
                cx, cy = button.center()
                self.assertTrue(.64 < cx / width < .72)
                self.assertTrue(.67 < cy / height < .74)

    def test_world_and_other_dialogs_have_no_story_confirmation(self):
        for name in ("quest_navigation_aligned.png", "quest_teleport_closer_dialog.png",
                     "quest_domain_confirmation.png", "quest_yangyang_interaction.png"):
            with self.subTest(image=name):
                self.assertIsNone(self.task._find_story_skip_confirm(self.read_image(name)))


if __name__ == "__main__":
    sys.argv[0] = str(Path(__file__).resolve().parent.parent / "main.py")
    unittest.main(verbosity=2)
