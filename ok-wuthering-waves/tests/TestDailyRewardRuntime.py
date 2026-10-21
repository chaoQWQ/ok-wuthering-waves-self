import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import cv2

from config import config
from ok import OK
from src.task.DailyTask import DailyTask, ADDITIONAL_TASKS, TELEPORT_AND_FARM_4C_ECHO
from src.utils.DailyAccountState import DailyAccountState
from src.utils.DailyEmail import build_daily_report


class TestDailyRewardRuntime(unittest.TestCase):
    def test_real_activity_and_other_game_screens_at_supported_resolutions(self):
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
                completed = cv2.imread(str(Path(__file__).parent / 'images' / 'daily_rewards_claimed_140.png'))
                if completed is None:
                    raise FileNotFoundError('活跃度奖励领取完成截图不存在')
                for width, height in ((1280, 720), (1600, 900), (1920, 1080), (2560, 1440)):
                    with self.subTest(completed_resolution=(width, height)):
                        frame = cv2.resize(completed, (width, height))
                        result = task.inspect_daily_rewards(frame=frame)
                        self.assertEqual(result['daily_points'], 140)
                        self.assertTrue(result['daily_rewards_claimed'])
                        self.assertEqual(result['daily_reward_status'], '已全部领取')
                for account in (1, 2, 3):
                    name = f'daily_activity_zero_{account}.png'
                    original = cv2.imread(str(Path(__file__).parent / 'images' / name))
                    if original is None:
                        raise FileNotFoundError(name)
                    for width, height in ((1280, 720), (1600, 900), (1920, 1080), (2560, 1440)):
                        with self.subTest(account=account, zero_resolution=(width, height)):
                            frame = cv2.resize(original, (width, height))
                            self.assertEqual(task.get_total_daily_points(frame=frame), 0)
                            result = task.inspect_daily_rewards(frame=frame)
                            self.assertEqual(result['daily_points'], 0)
                            self.assertEqual(result['daily_reward_status'], '未全部领取')
                            self.assertFalse(task.daily_reward_claimed(frame=frame))
                for name in ('account_logged_out_title.png', 'quest_map_detail.png'):
                    original = cv2.imread(str(Path(__file__).parent / 'images' / name))
                    if original is None:
                        raise FileNotFoundError(name)
                    for width, height in ((1920, 1080), (1280, 720)):
                        with self.subTest(image=name, resolution=(width, height)):
                            frame = cv2.resize(original, (width, height))
                            self.assertEqual(task.get_total_daily_points(frame=frame), 0)
                            result = task.inspect_daily_rewards(frame=frame)
                            self.assertIsNone(result['daily_points'])
                            self.assertIsNone(result['daily_rewards_claimed'])
                            self.assertEqual(result['daily_reward_status'], '未完成检测')
                task.config[ADDITIONAL_TASKS] = [TELEPORT_AND_FARM_4C_ECHO]
                with self.assertRaisesRegex(Exception, 'Teleport|传送'):
                    task.run()
                self.assertEqual(task.daily_reward_result['daily_reward_status'], '未检测')
            finally:
                runtime.quit()
                runtime.task_executor.destroy()
                runtime.device_manager.close()

    def test_reward_report_and_account_state_are_independent(self):
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'daily.sqlite3'
            state = DailyAccountState(path)
            day = state.day()
            for status, state_status in (('成功', 'success'), ('失败', 'failed')):
                for reward_status, points, claimed in (
                        ('已全部领取', 140, True), ('未全部领取', 0, False),
                        ('未完成检测', None, None), ('检测异常', None, None)):
                    with self.subTest(status=status, reward=reward_status):
                        account = f'{status}账号：{reward_status}'
                        result = {
                            'account': account, 'status': status, 'daily_points': points,
                            'daily_rewards_claimed': claimed, 'daily_reward_status': reward_status,
                        }
                        message = build_daily_report([result], now, now, '完成', '', '')
                        succeeded = 1 if status == '成功' else 0
                        self.assertIn(f'成功 {succeeded}/1', message['Subject'])
                        self.assertIn(f'{account}：{status}', message.get_content())
                        self.assertIn(f'活跃度奖励：{reward_status}', message.get_content())
                        state.record(day, [account], state_status)
                        reopened = DailyAccountState(path)
                        self.assertEqual(reopened.statuses(day)[account], state_status)
                        self.assertEqual(account in reopened.completed(day), status == '成功')

    def test_report_lists_unchecked_rewards_for_login_timeout(self):
        now = datetime.now(timezone.utc)
        message = build_daily_report([{
            'account': '账号 3', 'status': '登录超时',
        }], now, now, '完成（存在失败）', '', '')
        self.assertIn('成功 0/1', message['Subject'])
        self.assertIn('账号 3：登录超时', message.get_content())
        self.assertIn('活跃度奖励：未检测', message.get_content())


if __name__ == '__main__':
    unittest.main()
