import tempfile
import time
import unittest

from config import config
from ok.test.TaskTestCase import TaskTestCase
from src.task.QuestStoryTask import QuestStoryTask


class TestQuestCombatContinuity(TaskTestCase):
    task_class = QuestStoryTask

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.config = dict(config, config_folder=cls.directory.name, disable_file_log=True)
        super().setUpClass()

    def test_normal_team_keeps_combat_with_target(self):
        self.set_image('tests/images/in_combat.png')
        self.assertTrue(self.task.load_chars())
        self.assertEqual(len(self.task.chars), 3)
        self.assertTrue(all(not getattr(char, 'story_fallback', False) for char in self.task.chars))
        self.assertTrue(self.task.has_target())
        self.task._in_combat = True
        self.task.last_enemy_health_seen_time = time.monotonic() - 10
        self.assertTrue(self.task.do_check_in_combat())
        self.assertTrue(self.task._in_combat)


if __name__ == '__main__':
    unittest.main()
