import unittest
from unittest import mock
from unittest.mock import Mock

from ok import TaskDisabledException
from src.task.BaseWWTask import LOGIN_TEXTS, BaseWWTask
from src.task.DailyTask import AUTO_FARM_NIGHTMARE_NEST, AUTO_FARM_RESIDUAL_NEST
from src.task.MultiAccountDailyTask import (
    AccountConfigNotDetected,
    MultiAccountDailyTask,
    _ACCOUNT_LIST_EXHAUSTED,
    account_pattern,
    normalize_account_name,
    normalize_profile_code,
)
from src.task.WWOneTimeTask import WWOneTimeTask


class TestMultiAccountDailyTask(unittest.TestCase):

    def test_exit_option_survives_account_config_initialization(self):
        task = MultiAccountDailyTask(executor=Mock(), app=Mock())
        self.assertIs(task.default_config['Exit After Task'], False)
        self.assertEqual(
            task.config_description['Exit After Task'],
            'Exit the Game and the App after Successfully Executing the Task',
        )

    def test_disconnected_game_window_stops_task_input(self):
        class FakeExecutor:
            stopped = False

            def connected(self):
                return False

            def stop_current_task(self):
                self.stopped = True

        class FakeTask:
            executor = FakeExecutor()

            def log_error(self, message):
                self.message = message

        task = FakeTask()
        with self.assertRaises(TaskDisabledException):
            BaseWWTask._raise_if_game_window_disconnected(task)
        self.assertIn('disconnected', task.message)
        self.assertTrue(task.executor.stopped)

    def test_team_challenge_allows_slow_teleport_loading(self):
        class FakeTask:
            def __init__(self):
                self.kwargs = None
                self.skip_checked = False

            def wait_click_feature(self, feature, **kwargs):
                self.feature = feature
                self.kwargs = kwargs

            def wait_click_skip_dialog_confirm(self):
                self.skip_checked = True

        task = FakeTask()
        BaseWWTask.click_team_challenge(task)

        self.assertEqual(task.feature, 'team_start_challenge')
        self.assertEqual(task.kwargs['time_out'], 30)
        self.assertTrue(task.skip_checked)

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

    def test_profile_code_normalization_accepts_label_and_ocr_zero_variant(self):
        self.assertEqual(normalize_profile_code('特征码：1O8627353'), '108627353')

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
                self.failed_set = set()
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
                self.failed_set = set()
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

    def test_failed_account_is_not_reported_completed_but_is_not_retried(self):
        class FakeTask:
            def __init__(self):
                self.done_set = set()
                self.failed_set = set()
                self.config = {}
                self._daily_date = 'test-day'
                self._account_state = Mock()
                self._account_state.statuses.return_value = {}

            _mark_failed = MultiAccountDailyTask._mark_failed
            _update_daily_status_info = MultiAccountDailyTask._update_daily_status_info
            _is_done = MultiAccountDailyTask._is_done
            _is_skipped = MultiAccountDailyTask._is_skipped

            def log_info(self, *args):
                pass

            def info_set(self, *args):
                pass

            def tr(self, message):
                return message

        task = FakeTask()
        task._mark_failed('185****6758')

        self.assertTrue(task._is_done('185****6758'))
        self.assertNotIn(normalize_account_name('185****6758'), task.done_set)
        self.assertIn(normalize_account_name('185****6758'), task.failed_set)

    def test_daily_runner_allows_foreground_login_and_restores_flag(self):
        class FakeDailyTask:
            def __init__(self):
                self.config = {}
                self._allow_bring_to_front = False

            def wait_login(self):
                return False

        daily = FakeDailyTask()

        class FakeTask:
            config = {}
            _get_account_overrides = MultiAccountDailyTask._get_account_overrides
            _account_overrides_required = MultiAccountDailyTask._account_overrides_required
            _apply_daily_overrides = MultiAccountDailyTask._apply_daily_overrides
            _restore_daily_overrides = MultiAccountDailyTask._restore_daily_overrides

            def get_task_by_class(self, task_class):
                return daily

            def run_task_by_class(self, task_class):
                self.assert_foreground_enabled()

            def assert_foreground_enabled(self):
                if not daily._allow_bring_to_front:
                    raise AssertionError('foreground login was not enabled')
                if daily.farm_targets_override != ['Tacet Discord Nest']:
                    raise AssertionError('multi-account farming must only visit settlements')

        self.assertTrue(MultiAccountDailyTask._execute_daily_for_account(FakeTask(), None))
        self.assertFalse(daily._allow_bring_to_front)
        self.assertNotIn('farm_targets_override', daily.__dict__)

    def test_daily_runner_applies_override_from_world_profile(self):
        class FakeDailyTask:
            def __init__(self):
                self.config = {'Which Tacet Suppression to Farm': 1}
                self._allow_bring_to_front = False

            def is_main(self):
                return True

        daily = FakeDailyTask()
        observed = []

        class FakeTask:
            config = {
                'Account 1 Daily Task Override': True,
                'Account 1 In-game Profile Code': '7353',
                'Account 1: Which Tacet Suppression to Farm': 6,
            }
            _get_account_overrides = MultiAccountDailyTask._get_account_overrides
            _account_overrides_required = MultiAccountDailyTask._account_overrides_required
            _apply_daily_overrides = MultiAccountDailyTask._apply_daily_overrides
            _restore_daily_overrides = MultiAccountDailyTask._restore_daily_overrides

            def __init__(self):
                self._current_email_result = {'account': '185****0362'}
                self._daily_date = 'test-day'
                self._account_state = Mock()
                self._account_state.completed.return_value = set()

            def get_task_by_class(self, task_class):
                return daily

            def run_task_by_class(self, task_class):
                daily.is_main()
                observed.append(daily.config['Which Tacet Suppression to Farm'])

            def _detect_account_from_world_profile(self):
                return '特征码：108627353'

            def log_info(self, *args):
                pass

            def tr(self, message):
                return message

        # The login-list account is bookkeeping only; the ESC profile code
        # determines which slot's DailyTask settings are applied.
        self.assertTrue(
            MultiAccountDailyTask._execute_daily_for_account(FakeTask(), '185****0362')
        )
        self.assertEqual(observed, [6])
        self.assertEqual(daily.config['Which Tacet Suppression to Farm'], 1)

        self.assertNotIn('farm_targets_override', daily.__dict__)
        self.assertNotIn('is_main', daily.__dict__)

    def test_daily_runner_stops_when_enabled_account_config_does_not_match(self):
        class FakeDailyTask:
            def __init__(self):
                self.config = {'Which Tacet Suppression to Farm': 1}
                self._allow_bring_to_front = False

            def is_main(self):
                return True

        daily = FakeDailyTask()

        class FakeTask:
            config = {
                'Account 1 Daily Task Override': True,
                'Account 1 In-game Profile Code': '4946',
                'Account 1: Which Tacet Suppression to Farm': 3,
            }
            _get_account_overrides = MultiAccountDailyTask._get_account_overrides
            _account_overrides_required = MultiAccountDailyTask._account_overrides_required
            _apply_daily_overrides = MultiAccountDailyTask._apply_daily_overrides
            _restore_daily_overrides = MultiAccountDailyTask._restore_daily_overrides

            def __init__(self):
                self._current_email_result = {'account': 'test'}
                self._daily_date = 'test-day'
                self._account_state = Mock()
                self._account_state.completed.return_value = set()

            def log_error(self, *args):
                pass

            def get_task_by_class(self, task_class):
                return daily

            def run_task_by_class(self, task_class):
                daily.is_main()

            def _detect_account_from_world_profile(self):
                return '特征码：108627353'

            def log_info(self, *args):
                pass

            def tr(self, message):
                return message

        with self.assertRaises(AccountConfigNotDetected):
            MultiAccountDailyTask._execute_daily_for_account(FakeTask(), None)
        self.assertEqual(daily.config['Which Tacet Suppression to Farm'], 1)
        self.assertNotIn('farm_targets_override', daily.__dict__)

    def test_unknown_account_enters_world_then_uses_ingame_logout(self):
        class FakeTask:
            def __init__(self):
                self.in_world = False
                self.actions = []

            def _login_combo(self):
                return None

            def do_find_account_drop_down(self):
                return None

            def in_team_and_world(self):
                return self.in_world

            def ensure_main(self, time_out):
                self.actions.append(('ensure_main', time_out))
                self.in_world = True

            def _switch_to_login(self):
                self.actions.append(('_switch_to_login', None))

        task = FakeTask()
        MultiAccountDailyTask._open_account_login_for_reselection(task)

        self.assertEqual(task.actions, [('ensure_main', 180), ('_switch_to_login', None)])

    def test_reselection_skips_ensure_main_when_already_in_world(self):
        class FakeTask:
            def __init__(self):
                self.actions = []

            def _login_combo(self):
                return None

            def do_find_account_drop_down(self):
                return None

            def in_team_and_world(self):
                return True

            def ensure_main(self, time_out):
                self.actions.append(('ensure_main', time_out))

            def _switch_to_login(self):
                self.actions.append(('_switch_to_login', None))

        task = FakeTask()
        MultiAccountDailyTask._open_account_login_for_reselection(task)

        self.assertEqual(task.actions, [('_switch_to_login', None)])

    def test_run_accounts_switches_account_before_first_selection(self):
        calls = []

        class FakeTask:
            def _login_combo(self):
                return None

            def do_find_account_drop_down(self):
                return False

            def _open_account_login_for_reselection(self):
                calls.append('switch_first')

            def _select_and_login_account(self):
                calls.append('select')
                return None

            def _load_daily_state(self):
                calls.append('load_state')

            def log_info(self, *args):
                pass

            def tr(self, message):
                return message

        task = FakeTask()
        with mock.patch.object(WWOneTimeTask, 'run', lambda self: calls.append('ww_run')):
            MultiAccountDailyTask._run_accounts(task)

        self.assertEqual(calls, ['ww_run', 'load_state', 'switch_first', 'select'])

    def test_world_profile_code_matches_configured_suffix_and_closes_esc(self):
        class TextBox:
            name = '特征码：108627353'

        class FakeTask:
            config = {
                'Account 1 Daily Task Override': True,
                'Account 1 In-game Profile Code': '7353',
            }

            def __init__(self):
                self.keys = []

            def in_team_and_world(self):
                return True

            def send_key(self, key, after_sleep=0):
                self.keys.append(key)

            def wait_feature(self, *args, **kwargs):
                return object()

            def ocr(self, *args):
                self.ocr_region = args
                return [TextBox()]

            def wait_in_team_and_world(self, **kwargs):
                return True

            def log_info(self, *args):
                pass

            def log_warning(self, *args):
                pass

            def tr(self, message):
                return message

        task = FakeTask()
        detected = MultiAccountDailyTask._detect_account_from_world_profile(task)

        self.assertEqual(detected, TextBox.name)
        self.assertEqual(task.ocr_region, (0.18, 0.32, 0.38, 0.45))
        self.assertEqual(task.keys, ['esc', 'esc'])

    def test_account_override_matches_in_game_profile_code(self):
        class FakeTask:
            config = {
                'Account 1 Daily Task Override': True,
                'Account 1 Keyword': '362',
                'Account 1 In-game Profile Code': '7353',
                'Account 1: Which Tacet Suppression to Farm': 6,
            }

            def log_info(self, *args):
                pass

            def tr(self, message):
                return message

        overrides = MultiAccountDailyTask._get_account_overrides(
            FakeTask(), '特征码：108627353'
        )
        self.assertEqual(overrides['Which Tacet Suppression to Farm'], 6)

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

    def test_get_account_overrides_migrates_legacy_nightmare_label_to_settlements(self):
        class FakeTask:
            config = {
                'Account 1 Daily Task Override': True,
                'Account 1 Keyword': 'aa****01',
                'Account 1: Additional Tasks to Run After Daily Task': [
                    AUTO_FARM_NIGHTMARE_NEST,
                ],
            }

            _get_account_overrides = MultiAccountDailyTask._get_account_overrides

            def log_info(self, *args):
                pass

            def tr(self, message):
                return message

        overrides = FakeTask()._get_account_overrides('AA****01@domain.com')
        self.assertEqual(
            overrides['Additional Tasks to Run After Daily Task'],
            [AUTO_FARM_RESIDUAL_NEST],
        )

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
