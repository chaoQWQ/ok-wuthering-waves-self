import tempfile
import unittest
from pathlib import Path

import cv2

from config import config
from ok import OK


class TestMultiAccountStateRuntime(unittest.TestCase):
    def test_real_task_reloads_daily_state_and_recognizes_title_login(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime_config = dict(config)
            runtime_config.update({
                'gui': None,
                'check_mutex': False,
                'custom_tasks': False,
                'disable_file_log': True,
                'config_folder': str(Path(directory) / 'configs'),
            })
            runtime = OK(runtime_config)
            try:
                from src.task.MultiAccountDailyTask import MultiAccountDailyTask

                task = runtime.task_executor.get_task_by_class(MultiAccountDailyTask)
                # 同步处理截图，执行线程保持未启动。
                runtime.task_executor.paused = False
                frame = cv2.imread(str(Path(__file__).parent / 'images' / 'account_logged_out_title.png'))
                if frame is None:
                    raise FileNotFoundError('登出后的标题界面截图不存在')
                for width, height in ((1920, 1080), (1280, 720)):
                    with self.subTest(resolution=(width, height)):
                        image = cv2.resize(frame, (width, height))
                        button = task._find_title_login_button(frame=image)
                        self.assertIsNotNone(button)
                        self.assertEqual(button.name, '登入')
                        self.assertGreater(button.x, width * 0.9)
                task._load_daily_state()
                task._mark_done('185****6758')
                task._mark_failed('134****1892')
                task._account_state.record(task._daily_date, ['185****o362'], 'running')
                self.assertTrue(task._is_done('185****6758'))
                self.assertTrue(task._is_done('134****1892'))

                reopened = MultiAccountDailyTask(
                    executor=runtime.task_executor, app=runtime.headless_app
                )
                reopened.after_init(executor=runtime.task_executor, scene=runtime.task_executor.scene)
                reopened._load_daily_state()
                self.assertTrue(reopened._is_done('185****6758'))
                self.assertFalse(reopened._is_done('134****1892'))
                self.assertEqual(reopened.failed_set, set())
                self.assertEqual(reopened._account_state.statuses(reopened._daily_date), {
                    '185****6758': 'success', '134****1892': 'failed',
                    '185****o362': 'interrupted',
                })
            finally:
                runtime.quit()
                runtime.task_executor.destroy()
                runtime.device_manager.close()


if __name__ == '__main__':
    unittest.main()
