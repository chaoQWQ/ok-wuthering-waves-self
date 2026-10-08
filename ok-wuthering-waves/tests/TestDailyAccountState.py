import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.utils.DailyAccountState import DailyAccountState


class TestDailyAccountState(unittest.TestCase):
    def test_success_survives_reopening_and_other_accounts_remain_retryable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'configs' / 'daily.sqlite3'
            state = DailyAccountState(path)
            day = '2026-10-08'
            state.record(day, ['185****6758', 'profile:108627353'], 'success')
            state.record(day, ['134****1892'], 'login_timeout')
            state.record(day, ['185****o362'], 'interrupted')
            reopened = DailyAccountState(path)
            self.assertEqual(reopened.completed(day), {'185****6758', 'profile:108627353'})
            self.assertEqual(reopened.statuses(day)['134****1892'], 'login_timeout')
            reopened.record(day, ['134****1892'], 'running')
            reopened.record(day, ['134****1892'], 'success')
            self.assertIn('134****1892', reopened.completed(day))
            self.assertEqual(reopened.completed('2026-10-09'), set())

    def test_success_is_preserved_and_database_connections_are_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'daily.sqlite3'
            state = DailyAccountState(path)
            state.record('2026-10-08', ['185****6758'], 'success')
            for status in ['running', 'failed', 'interrupted', 'login_timeout']:
                state.record('2026-10-08', ['185****6758'], status)
            self.assertEqual(state.statuses('2026-10-08'), {'185****6758': 'success'})
            path.rename(path.with_suffix('.backup'))

    def test_daily_reset_at_four_in_china_timezone(self):
        local = timezone(timedelta(hours=8))
        before = datetime(2026, 10, 8, 3, 59, 59, tzinfo=local)
        after = datetime(2026, 10, 8, 4, 0, 0, tzinfo=local)
        self.assertEqual(DailyAccountState.day(before), '2026-10-07')
        self.assertEqual(DailyAccountState.day(after), '2026-10-08')
        self.assertEqual(DailyAccountState.day(after.astimezone(timezone.utc)), '2026-10-08')
        with self.assertRaises(ValueError):
            DailyAccountState.day(datetime(2026, 10, 8))

    def test_invalid_status_and_damaged_database_raise_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'daily.sqlite3'
            state = DailyAccountState(path)
            with self.assertRaises(ValueError):
                state.record('2026-10-08', ['185****6758'], 'unknown')
            path.write_bytes(b'invalid sqlite database')
            with self.assertRaises(sqlite3.DatabaseError):
                DailyAccountState(path)


if __name__ == '__main__':
    unittest.main()
