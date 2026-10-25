from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest

import cv2

sys.argv[0] = str(Path(__file__).resolve().parent.parent / "main.py")

from config import config
from ok import OK, ImageCaptureMethod
from src.task.QuestStoryTask import QuestStoryTask


class TestQuestCombatRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.images = Path(__file__).resolve().parent / "images"
        cls.data_directory = tempfile.TemporaryDirectory(prefix="quest-combat-runtime-", dir=cls.images.parents[2] / "cache")
        runtime_config = deepcopy(config)
        runtime_config.update({
            "check_mutex": False,
            "gui": None,
            "custom_tasks": False,
            "onetime_tasks": [["src.task.QuestStoryTask", "QuestStoryTask"]],
            "trigger_tasks": [],
            "disable_file_log": True,
            "config_folder": str(Path(cls.data_directory.name) / "configs"),
            "screenshots_folder": str(Path(cls.data_directory.name) / "screenshots"),
            "windows": None,
        })
        cls.runtime = OK(runtime_config)
        cls.task = cls.runtime.get_onetime_task(QuestStoryTask)
        cls.runtime.task_executor.paused = False

    @classmethod
    def tearDownClass(cls):
        cls.runtime.quit()
        cls.runtime.task_executor.destroy()
        cls.runtime.device_manager.close()
        cls.data_directory.cleanup()

    def setUp(self):
        self.task.do_reset_to_false()
        self.task.chars = [None, None, None]

    def set_image(self, name):
        path = self.images / name
        frame = cv2.imread(str(path))
        if frame is None:
            raise FileNotFoundError(str(path))
        self.runtime.device_manager.capture_method = ImageCaptureMethod(self.runtime.exit_event, [str(path)])
        self.runtime.task_executor._frame = frame
        self.task.scene.reset()
        self.runtime.feature_set.check_size(frame)

    def test_enter_combat_with_real_locked_enemy(self):
        self.set_image("in_combat.png")
        self.assertTrue(self.task.has_target())
        self.assertTrue(self.task.in_combat())
        self.assertTrue(self.task._in_combat)
        self.assertIsNotNone(self.task.get_current_char())
        self.assertFalse(self.task.in_sleep_check)

    def test_existing_lock_confirms_target_and_continues_combat(self):
        self.set_image("in_combat.png")
        self.assertTrue(self.task.in_combat())
        self.task.scene.reset()
        self.assertTrue(self.task.target_enemy(wait=True))
        self.task.scene.reset()
        self.assertTrue(self.task.in_combat())
        self.assertTrue(self.task.scene.in_combat())

    def test_world_without_enemies_does_not_enter_combat(self):
        self.set_image("quest_after_story_combat.png")
        self.assertFalse(self.task.check_health_bar())
        self.assertFalse(self.task.has_target())
        self.assertFalse(self.task.in_combat())
        self.assertFalse(self.task._in_combat)


if __name__ == "__main__":
    unittest.main(verbosity=2)
