"""Optional QQ SMTP report; credentials stay outside task configs and logs."""

import os
import smtplib
import ssl
from email.message import EmailMessage


def send_daily_report(results, started, ended, status):
    """Return False when disabled. Raise on configuration or delivery failure."""
    if os.environ.get('WW_DAILY_EMAIL_ENABLED', '').strip().lower() not in ('1', 'true'):
        return False
    sender = os.environ.get('WW_DAILY_EMAIL_SENDER', '').strip()
    recipient = os.environ.get('WW_DAILY_EMAIL_TO', '').strip() or sender
    password = os.environ.get('WW_DAILY_EMAIL_AUTH_CODE', '').strip()
    if not sender or not recipient or not password:
        raise ValueError('Missing email settings')
    succeeded = sum(result['status'] == '成功' for result in results)
    message = EmailMessage()
    message['From'] = sender
    message['To'] = recipient
    message['Subject'] = f'鸣潮多账号日常：{status}（成功 {succeeded}/{len(results)}）'
    lines = [
        f'本轮状态：{status}',
        f'开始时间：{started:%Y-%m-%d %H:%M:%S %z}',
        f'结束时间：{ended:%Y-%m-%d %H:%M:%S %z}',
        f'耗时：{int((ended - started).total_seconds())} 秒',
        '',
    ]
    for index, result in enumerate(results, 1):
        lines.append(f"{index}. {result['account']}：{result['status']}")
    if not results:
        lines.append('尚无账号执行结果。')
    lines.extend(['', '仅列出本轮实际尝试的账号；跳过或未运行的账号不计入成功。',
                  '成功表示日常任务正常返回；失败详情请查看本机日志。'])
    message.set_content('\n'.join(lines))
    with smtplib.SMTP_SSL('smtp.qq.com', 465, timeout=20,
                          context=ssl.create_default_context()) as smtp:
        smtp.login(sender, password)
        smtp.send_message(message)
    return True
