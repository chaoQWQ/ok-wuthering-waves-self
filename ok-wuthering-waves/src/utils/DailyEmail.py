import os
import smtplib
import ssl
from email.message import EmailMessage


EMAIL_ENABLED = 'Email Notification'
EMAIL_SENDER = 'QQ Email'
EMAIL_AUTH = 'QQ SMTP Authorization Code'
EMAIL_TO = 'Receiver Email'


def send_daily_report(results, started, ended, status, config=None):
    """Return False when disabled. Raise on configuration or delivery failure."""
    if config is None:
        config = {
            EMAIL_ENABLED: os.environ.get('WW_DAILY_EMAIL_ENABLED', '').strip().lower() in ('1', 'true'),
            EMAIL_SENDER: os.environ.get('WW_DAILY_EMAIL_SENDER', ''),
            EMAIL_TO: os.environ.get('WW_DAILY_EMAIL_TO', ''),
            EMAIL_AUTH: os.environ.get('WW_DAILY_EMAIL_AUTH_CODE', ''),
        }
    if not config.get(EMAIL_ENABLED, False):
        return False
    sender = config.get(EMAIL_SENDER, '').strip()
    recipient = config.get(EMAIL_TO, '').strip() or sender
    password = config.get(EMAIL_AUTH, '').strip()
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
    return send_message(message, config)


def send_resolution_report(previous, current, detected, task_name, config):
    if not config.get(EMAIL_ENABLED, False):
        return False
    message = EmailMessage()
    message['Subject'] = f'鸣潮游戏窗口分辨率变化：{previous[0]}×{previous[1]} → {current[0]}×{current[1]}'
    message.set_content('\n'.join([
        f'检测时间：{detected:%Y-%m-%d %H:%M:%S %z}',
        f'变化前：{previous[0]}×{previous[1]}',
        f'变化后：{current[0]}×{current[1]}',
        f'当前任务：{task_name or "没有正在执行的任务"}',
        '记录的是游戏窗口内容区域的尺寸。',
    ]))
    return send_message(message, config)


def send_message(message, config):
    if not config.get(EMAIL_ENABLED, False):
        return False
    sender = config.get(EMAIL_SENDER, '').strip()
    recipient = config.get(EMAIL_TO, '').strip() or sender
    password = config.get(EMAIL_AUTH, '').strip()
    if not sender or not recipient or not password:
        raise ValueError('Missing email settings')
    if 'From' not in message:
        message['From'] = sender
        message['To'] = recipient
    with smtplib.SMTP_SSL('smtp.qq.com', 465, timeout=20,
                          context=ssl.create_default_context()) as smtp:
        smtp.login(sender, password)
        smtp.send_message(message)
    return True
