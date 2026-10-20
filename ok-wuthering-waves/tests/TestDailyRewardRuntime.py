import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import cv2

from config import config
from ok import OK
from src.task.DailyTask import DailyTask, DailyRewardIncompleteError
from src.utils.DailyAccountState import DailyAccountState
from src.utils.DailyEmail import build_daily_report


class TestDailyRewardRuntime(unittest.TestCase):
    def test_game_screens_without_activity_do_not_confirm_rewards(self):
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
                task = runtime.task_executor.get_task_by_class(DailyTask)
                runtime.task_executor.paused = False
                for name in ('account_logged_out_title.png', 'quest_map_detail.png'):
                    original = cv2.imread(str(Path(__file__).parent / 'images' / name))
                    if original is None:
                        raise FileNotFoundError(name)
                    for width, height in ((1920, 1080), (1280, 720)):
                        with self.subTest(image=name, resolution=(width, height)):
                            frame = cv2.resize(original, (width, height))
                            with self.assertRaises(DailyRewardIncompleteError):
                                task.get_total_daily_points(frame=frame)
                            self.assertFalse(task.daily_reward_claimed(frame=frame))
            finally:
                runtime.quit()
                runtime.task_executor.destroy()
                runtime.device_manager.close()

    def test_incomplete_activity_report_and_retryable_state(self):
        error = DailyRewardIncompleteError(
            '日常活跃度仅 90/100，活跃度奖励未全部领取', points=90,
        )
        result = {
            'account': '账号 3', 'status': '失败', 'daily_points': error.points,
            'daily_rewards_claimed': False, 'detail': str(error),
        }
        now = datetime.now(timezone.utc)
        message = build_daily_report([result], now, now, '完成（存在失败）', '', '')
        self.assertIn('成功 0/1', message['Subject'])
        self.assertIn('账号 3：失败', message.get_content())
        self.assertIn('活跃度：90/100', message.get_content())
        self.assertIn('活跃度奖励：未完成验证', message.get_content())
        self.assertIn(str(error), message.get_content())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'daily.sqlite3'
            state = DailyAccountState(path)
            day = state.day()
            state.record(day, ['账号 3'], 'running')
            state.record(day, ['账号 3'], 'failed')
            reopened = DailyAccountState(path)
            self.assertEqual(reopened.statuses(day), {'账号 3': 'failed'})
            self.assertEqual(reopened.completed(day), set())

    def test_failed_account_report_preserves_verified_rewards(self):
        now = datetime.now(timezone.utc)
        message = build_daily_report([{
            'account': '账号 3', 'status': '失败', 'daily_points': 100,
            'daily_rewards_claimed': True,
        }], now, now, '完成（存在失败）', '', '')
        self.assertIn('成功 0/1', message['Subject'])
        self.assertIn('账号 3：失败', message.get_content())
        self.assertIn('活跃度：100/100', message.get_content())
        self.assertIn('活跃度奖励：已全部领取', message.get_content())


if __name__ == '__main__':
    unittest.main()
