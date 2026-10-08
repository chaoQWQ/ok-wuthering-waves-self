import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


class DailyAccountState:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute('''
                CREATE TABLE IF NOT EXISTS daily_accounts (
                    day TEXT NOT NULL,
                    account TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (day, account)
                )
            ''')

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def day(now=None):
        server_timezone = timezone(timedelta(hours=8))
        now = now or datetime.now(server_timezone)
        if now.tzinfo is None:
            raise ValueError('账号每日状态的时间必须包含时区')
        return (now.astimezone(server_timezone) - timedelta(hours=4)).date().isoformat()

    def record(self, day, accounts, status):
        if status not in {'running', 'success', 'failed', 'login_timeout', 'interrupted'}:
            raise ValueError(f'未知的账号每日状态：{status}')
        updated_at = datetime.now().astimezone().isoformat()
        with self._connect() as connection:
            connection.executemany('''
                INSERT INTO daily_accounts (day, account, status, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(day, account) DO UPDATE SET
                    status = excluded.status, updated_at = excluded.updated_at
                WHERE daily_accounts.status != 'success'
            ''', [(day, account, status, updated_at) for account in set(accounts) if account])

    def statuses(self, day):
        with self._connect() as connection:
            return dict(connection.execute(
                'SELECT account, status FROM daily_accounts WHERE day = ?', (day,)
            ))

    def completed(self, day):
        return {account for account, status in self.statuses(day).items() if status == 'success'}
