import tempfile
import unittest

from PySide6.QtCore import QCoreApplication

import config
from ok import TaskDisabledException
from ok.core.start_controller import StartController
from ok.device.DeviceManager import DeviceManager
from ok.task.TaskExecutor import TaskExecutor
from ok.util.GlobalConfig import GlobalConfig, basic_options
from ok.util.config import Config
from ok.util.handler import ExitEvent
from src.task.MultiAccountDailyTask import MultiAccountDailyTask


class TestMultiAccountLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.previous_folder = Config.config_folder
        self.temp_dir = tempfile.TemporaryDirectory()
        Config.config_folder = self.temp_dir.name
        self.exit_event = ExitEvent()
        global_config = GlobalConfig([basic_options, *config.config['global_configs']])
        self.device_manager = DeviceManager({}, self.exit_event, global_config)
        self.executor = TaskExecutor(
            self.device_manager, exit_event=self.exit_event,
            global_config=global_config, config={}, config_folder=self.temp_dir.name,
        )
        self.task = MultiAccountDailyTask(executor=self.executor, app=self.app)
        self.task.after_init()
        self.executor.onetime_tasks.append(self.task)

    def tearDown(self):
        self.exit_event.set()
        self.device_manager.close()
        Config.config_folder = self.previous_folder
        self.temp_dir.cleanup()

    def test_disconnected_run_disables_task_and_removes_pending_execution(self):
        StartController._mark_task_enabled(self.task)
        self.assertTrue(self.task.enabled)
        self.assertIn(self.task, self.executor.onetime_task_queue)

        with self.assertRaises(TaskDisabledException):
            self.task.run()

        self.assertFalse(self.task.enabled)
        self.assertNotIn(self.task, self.executor.onetime_task_queue)
        self.assertIsNone(self.executor.next_task()[0])

    def test_startup_failure_preserves_account_state_and_stops_once_per_restart(self):
        for _ in range(2):
            self.task.done_set.add('previous_completed')
            self.task.failed_set.add('previous_failed')
            self.task.all_accounts.add('previous_account')
            StartController._mark_task_enabled(self.task)

            with self.assertRaises(TaskDisabledException):
                self.task.run()

            self.assertEqual({'previous_completed'}, self.task.done_set)
            self.assertEqual({'previous_failed'}, self.task.failed_set)
            self.assertEqual({'previous_account'}, self.task.all_accounts)
            self.assertEqual([], self.task._email_results)
            self.assertFalse(self.task.enabled)
            self.assertIsNone(self.executor.next_task()[0])


if __name__ == '__main__':
    unittest.main()
