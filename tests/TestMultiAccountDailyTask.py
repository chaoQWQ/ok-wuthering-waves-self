import unittest

from src.task.BaseWWTask import LOGIN_TEXTS
from src.task.MultiAccountDailyTask import (
    MultiAccountDailyTask,
    _ACCOUNT_LIST_EXHAUSTED,
    account_pattern,
    normalize_account_name,
)


class TestMultiAccountDailyTask(unittest.TestCase):

    def test_account_dropdown_accepts_multiple_login_text_matches(self):
        account_box = object()

        class FakeTask:
            def ocr(self):
                return []

            def find_boxes(self, texts, match):
                if match == account_pattern:
                    return [account_box]
                if match == LOGIN_TEXTS:
                    return [object(), object()]
                return []

        self.assertIs(MultiAccountDailyTask.do_find_account_drop_down(FakeTask()), account_box)

    def test_account_name_normalization_groups_common_ocr_variants(self):
        self.assertEqual(
            normalize_account_name("cc****33@demo.com.hk"),
            normalize_account_name("cc****33@dem0.com.hk"),
        )
        self.assertEqual(
            normalize_account_name("bb****02@example.com"),
            normalize_account_name("bb****02@example.con"),
        )

    def test_click_account_list_selects_visible_third_account_after_first_two_are_done(self):
        class AccountBox:
            def __init__(self, name):
                self.name = name

        class FakeTask:
            def __init__(self):
                self.done_set = {
                    normalize_account_name("aa****01@example.com"),
                    normalize_account_name("bb****02@example.com"),
                }
                self.all_accounts = set()
                self.clicked = []
                self.config = {}  # empty skip list

            _is_done = MultiAccountDailyTask._is_done
            _is_skipped = MultiAccountDailyTask._is_skipped
            _click_direct = MultiAccountDailyTask._click_direct

            def ocr(self, match=None):
                return [
                    AccountBox("aa****01@example.com"),
                    AccountBox("aa****01@example.com"),
                    AccountBox("bb****02@example.com"),
                    AccountBox("cc****03@example.com.hk"),
                ]

            def info_set(self, *args):
                pass

            def click(self, account, after_sleep=0):
                self.clicked.append(account.name)

            def log_info(self, *args):
                pass

            def log_warning(self, *args):
                pass

            def tr(self, message):
                return message

        task = FakeTask()

        selected = MultiAccountDailyTask._click_account_in_list(task)

        self.assertEqual(selected, "cc****03@example.com.hk")
        self.assertEqual(task.clicked, ["cc****03@example.com.hk"])

    def test_loaded_account_list_with_no_remaining_account_is_terminal(self):
        class AccountBox:
            def __init__(self, name):
                self.name = name

        class FakeTask:
            def __init__(self):
                self.done_set = {normalize_account_name("aa****01@example.com")}
                self.all_accounts = set()
                self.config = {}

            _is_done = MultiAccountDailyTask._is_done
            _is_skipped = MultiAccountDailyTask._is_skipped

            def ocr(self, match=None):
                return [AccountBox("aa****01@example.com")]

            def info_set(self, *args):
                pass

            def log_info(self, *args):
                pass

            def tr(self, message):
                return message

        result = MultiAccountDailyTask._click_account_in_list(FakeTask())
        self.assertIs(result, _ACCOUNT_LIST_EXHAUSTED)

    def test_empty_account_list_keeps_waiting(self):
        class FakeTask:
            def ocr(self, match=None):
                return []

        self.assertIsNone(MultiAccountDailyTask._click_account_in_list(FakeTask()))

    def test_daily_runner_allows_foreground_login_and_restores_flag(self):
        class FakeDailyTask:
            def __init__(self):
                self.config = {}
                self._allow_bring_to_front = False

        daily = FakeDailyTask()

        class FakeTask:
            config = {}
            _get_account_overrides = MultiAccountDailyTask._get_account_overrides
            _apply_daily_overrides = MultiAccountDailyTask._apply_daily_overrides
            _restore_daily_overrides = MultiAccountDailyTask._restore_daily_overrides

            def get_task_by_class(self, task_class):
                return daily

            def run_task_by_class(self, task_class):
                self.assert_foreground_enabled()

            def assert_foreground_enabled(self):
                if not daily._allow_bring_to_front:
                    raise AssertionError('foreground login was not enabled')

        self.assertTrue(MultiAccountDailyTask._run_daily_for_account(FakeTask(), None))
        self.assertFalse(daily._allow_bring_to_front)

    def test_click_direct_falls_back_to_click_without_hwnd_window(self):
        class FakeTask:
            def __init__(self):
                self.clicked = []
                self.executor = None

            _click_direct = MultiAccountDailyTask._click_direct

            def click(self, target, after_sleep=0):
                self.clicked.append((target, after_sleep))
                return True

            def log_info(self, *args):
                pass

            def log_warning(self, *args):
                pass

        task = FakeTask()
        task._click_direct('dropdown_box', after_sleep=1)
        self.assertEqual(task.clicked, [('dropdown_box', 1)])

    def test_click_direct_calculates_screen_coords_with_hwnd_window(self):
        from unittest.mock import patch
        from ok import Box

        class FakeHwndWindow:
            def get_capture_origin(self):
                return (100, 200)

            def get_abs_cords(self, x, y):
                return (100 + x, 200 + y)

        class FakeDeviceManager:
            hwnd_window = FakeHwndWindow()

        class FakeExecutor:
            device_manager = FakeDeviceManager()

        class FakeTask:
            def __init__(self):
                self.executor = FakeExecutor()
                self.clicked = []

            _click_direct = MultiAccountDailyTask._click_direct

            def click(self, target, after_sleep=0):
                self.clicked.append(target)

            def sleep(self, duration):
                pass

            def ensure_in_front(self):
                pass

            def log_info(self, *args):
                pass

            def log_warning(self, *args):
                pass

        task = FakeTask()
        test_box = Box(50, 60, 20, 10, name="test_item")
        # Center of test_box is (60, 65). Origin is (100, 200). Screen coords: (160, 265).
        with patch('win32api.SetCursorPos') as mock_set_cursor, \
             patch('win32api.mouse_event') as mock_mouse_event:
            res = task._click_direct(test_box, after_sleep=0)
            self.assertTrue(res)
            mock_set_cursor.assert_called_once_with((160, 265))
            self.assertEqual(mock_mouse_event.call_count, 2)

    def test_is_skipped_matches_case_insensitive_substring(self):
        class FakeTask:
            def __init__(self, skip_list):
                self.config = {'Skip Accounts': skip_list}

            _is_skipped = MultiAccountDailyTask._is_skipped

            def log_info(self, *args):
                pass

            def tr(self, message):
                return message

        task = FakeTask(['aa****01', 'DEMO@example.com'])
        self.assertTrue(task._is_skipped('AA****01@test.com'))
        self.assertTrue(task._is_skipped('user_demo@example.com'))
        self.assertFalse(task._is_skipped('bb****02@other.com'))
        self.assertFalse(task._is_skipped(None))

    def test_get_account_overrides_matches_slot_and_extracts_config(self):
        class FakeTask:
            def __init__(self):
                self.config = {
                    'Account 1 Daily Task Override': True,
                    'Account 1 Keyword': 'aa****01',
                    'Account 1: Which to Farm': 'Forgery Challenge',
                    'Account 1: Which Forgery Challenge to Farm': 3,
                    'Account 1: Which Tacet Suppression to Farm': 1,
                    'Account 1: Material Selection': 'Shell Credit',
                    'Account 1: Farm Nightmare Nest for Daily Echo': False,
                    'Account 1: Additional Tasks to Run After Daily Task': [],
                    'Account 2 Daily Task Override': False,
                    'Account 2 Keyword': 'bb****02',
                }

            _get_account_overrides = MultiAccountDailyTask._get_account_overrides

            def log_info(self, *args):
                pass

            def tr(self, message):
                return message

        task = FakeTask()
        overrides = task._get_account_overrides('AA****01@domain.com')
        self.assertEqual(overrides.get('Which to Farm'), 'Forgery Challenge')
        self.assertEqual(overrides.get('Which Forgery Challenge to Farm'), 3)
        self.assertEqual(overrides.get('Farm Nightmare Nest for Daily Echo'), False)

        # Slot 2 is disabled
        self.assertEqual(task._get_account_overrides('bb****02@domain.com'), {})

        # Unmatched account returns empty
        self.assertEqual(task._get_account_overrides('cc****03@domain.com'), {})

    def test_apply_and_restore_daily_overrides(self):
        class FakeDailyTask:
            def __init__(self):
                self.config = {
                    'Which to Farm': 'Tacet Suppression',
                    'Which Tacet Suppression to Farm': 1,
                    'Farm Nightmare Nest for Daily Echo': True,
                }

        class FakeMultiTask:
            _apply_daily_overrides = MultiAccountDailyTask._apply_daily_overrides
            _restore_daily_overrides = MultiAccountDailyTask._restore_daily_overrides

        daily = FakeDailyTask()
        multi = FakeMultiTask()

        overrides = {
            'Which to Farm': 'Simulation Challenge',
            'Material Selection': 'Resonator EXP',
        }
        originals = multi._apply_daily_overrides(daily, overrides)

        self.assertEqual(daily.config['Which to Farm'], 'Simulation Challenge')
        self.assertEqual(daily.config['Material Selection'], 'Resonator EXP')
        self.assertEqual(originals['Which to Farm'], 'Tacet Suppression')
        self.assertIsNone(originals['Material Selection'])

        multi._restore_daily_overrides(daily, originals)
        self.assertEqual(daily.config['Which to Farm'], 'Tacet Suppression')
        self.assertNotIn('Material Selection', daily.config)

    def test_wait_login_uses_click_direct_for_login_button(self):
        from src.task.BaseWWTask import BaseWWTask
        from ok import Box

        login_box = Box(960, 613, 100, 40, name="登录")

        class FakeWWTask:
            def __init__(self):
                self.logged_in = False
                self.debug = False
                self.direct_clicked = []
                self.width = 1920
                self.height = 1080

            wait_login = BaseWWTask.wait_login
            find_boxes = BaseWWTask.find_boxes

            def box_of_screen(self, *args, **kwargs):
                return Box(0, 0, 1920, 1080)

            def in_team_and_world(self):
                return False

            def out_of_ratio(self):
                return False

            def handle_monthly_card(self):
                return False

            def find_one(self, *args, **kwargs):
                return None

            def ocr(self, *args, **kwargs):
                return [login_box]

            def sleep(self, *args):
                pass

            def click_direct(self, target, after_sleep=0):
                self.direct_clicked.append(target)
                return True

            def log_info(self, *args):
                pass

            def log_debug(self, *args):
                pass

        task = FakeWWTask()
        task.wait_login()
        self.assertEqual(len(task.direct_clicked), 1)
        self.assertEqual(task.direct_clicked[0][0].name, "登录")


if __name__ == "__main__":
    unittest.main()
