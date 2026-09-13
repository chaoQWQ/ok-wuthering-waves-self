import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from src.utils.DailyEmail import send_daily_report
from src.task.MultiAccountDailyTask import MultiAccountDailyTask
from ok import TaskDisabledException


class TestDailyEmail(unittest.TestCase):
    @patch.dict(os.environ, {}, clear=True)
    @patch('src.utils.DailyEmail.smtplib.SMTP_SSL')
    def test_disabled_does_not_connect(self, smtp):
        self.assertFalse(send_daily_report([], None, None, '完成'))
        smtp.assert_not_called()

    @patch.dict(os.environ, {'WW_DAILY_EMAIL_ENABLED': '1',
                            'WW_DAILY_EMAIL_SENDER': 'sender@qq.com',
                            'WW_DAILY_EMAIL_AUTH_CODE': 'test-secret'}, clear=True)
    @patch('src.utils.DailyEmail.smtplib.SMTP_SSL')
    @patch('src.utils.DailyEmail.ssl.create_default_context')
    def test_report_uses_tls_and_excludes_secret(self, context, smtp):
        now = datetime.now(timezone.utc)
        self.assertTrue(send_daily_report([
            {'account': 'aa****01', 'status': '成功'},
            {'account': 'aa****02', 'status': '失败'},
        ], now, now, '完成（存在失败）'))
        self.assertEqual(smtp.call_args.args, ('smtp.qq.com', 465))
        context.assert_called_once_with()
        self.assertIs(smtp.call_args.kwargs['context'], context.return_value)
        client = smtp.return_value.__enter__.return_value
        client.login.assert_called_once_with('sender@qq.com', 'test-secret')
        message = client.send_message.call_args.args[0]
        self.assertEqual(message['To'], 'sender@qq.com')
        self.assertIn('成功 1/2', message['Subject'])
        self.assertIn('aa****02：失败', message.get_content())
        self.assertNotIn('test-secret', message.as_string())

    @patch('src.task.MultiAccountDailyTask.send_daily_report')
    def test_report_on_completion_error_and_stop(self, send):
        for error, expected in [(None, '完成'), (RuntimeError('local'), '异常中断'),
                                (TaskDisabledException(), '已停止')]:
            class FakeTask:
                def _run_accounts(self):
                    if error:
                        raise error

                def log_info(self, *_):
                    pass

                def log_warning(self, *_):
                    pass

            task = FakeTask()
            send.reset_mock()
            if error:
                with self.assertRaises(type(error)):
                    MultiAccountDailyTask.run(task)
            else:
                MultiAccountDailyTask.run(task)
            send.assert_called_once()
            self.assertEqual(send.call_args.args[-1], expected)
        send.side_effect = RuntimeError('SMTP unavailable')
        with self.assertRaises(TaskDisabledException):
            MultiAccountDailyTask.run(task)

    def test_records_each_attempt(self):
        class FakeTask:
            _email_results = []

            def _execute_daily_for_account(self, account):
                return account == 'success'

        task = FakeTask()
        MultiAccountDailyTask._run_daily_for_account(task, 'success')
        MultiAccountDailyTask._run_daily_for_account(task, 'failure')
        self.assertEqual([r['status'] for r in task._email_results], ['成功', '失败'])
