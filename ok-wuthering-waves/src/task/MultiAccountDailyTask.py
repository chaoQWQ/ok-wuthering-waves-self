import ctypes
import re
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

import win32con
import win32gui
import win32process

from ok import Box, TaskDisabledException
from src.task.DailyTask import (
    DailyTask, ADDITIONAL_TASKS, CHECK_WEEKLY_GARDEN,
    AUTO_FARM_NIGHTMARE_NEST, AUTO_FARM_RESIDUAL_NEST,
    MERGE_ECHO_IF_DISCARDED_OVER_1000,
    TELEPORT_AND_FARM_4C_ECHO,
)
from src.task.WWOneTimeTask import WWOneTimeTask
from src.task.BaseCombatTask import BaseCombatTask
from src.task.BaseWWTask import LOGIN_TEXTS, BaseWWTask, LoginTimeoutError
from src.task.MouseResetTask import MouseResetTask
from src.task.NightmareNestTask import NightmareNestTask
from src.utils.wgc_compat import enable_windows_graphics_capture
from src.utils.DailyEmail import (
    send_daily_report, EMAIL_ENABLED, EMAIL_SENDER, EMAIL_AUTH, EMAIL_TO,
)
from src.utils.DailyAccountState import DailyAccountState

enable_windows_graphics_capture()

account_pattern = re.compile(r'\*\*\*\*')
_ACCOUNT_LIST_EXHAUSTED = object()

CB_GETCOUNT, CB_GETCURSEL, CB_GETLBTEXT, CB_GETLBTEXTLEN, CB_SETCURSEL = 0x146, 0x147, 0x148, 0x149, 0x14E
CBN_SELCHANGE = 1
_SendMessageW = ctypes.windll.user32.SendMessageW
_SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_SendMessageW.restype = ctypes.c_ssize_t


def find_login_combo(game_hwnd):
    if not game_hwnd or not win32gui.IsWindow(game_hwnd):
        return None
    _, game_pid = win32process.GetWindowThreadProcessId(game_hwnd)
    combos = []

    def on_child(child, _):
        if (win32gui.GetClassName(child) == 'ComboBox' and win32gui.IsWindowVisible(child)
                and _SendMessageW(child, CB_GETCOUNT, 0, 0) > 0):
            combos.append(child)

    def on_top(hwnd, _):
        if (win32gui.GetClassName(hwnd) == '#32770' and win32gui.IsWindowVisible(hwnd)
                and win32process.GetWindowThreadProcessId(hwnd)[1] == game_pid):
            win32gui.EnumChildWindows(hwnd, on_child, None)

    win32gui.EnumWindows(on_top, None)
    return combos[0] if combos else None


def combo_items(combo):
    items = []
    for i in range(_SendMessageW(combo, CB_GETCOUNT, 0, 0)):
        buf = ctypes.create_unicode_buffer(max(_SendMessageW(combo, CB_GETLBTEXTLEN, i, 0), 0) + 1)
        _SendMessageW(combo, CB_GETLBTEXT, i, ctypes.addressof(buf))
        items.append(buf.value)
    return items


def combo_selected_item(combo):
    index = _SendMessageW(combo, CB_GETCURSEL, 0, 0)
    items = combo_items(combo)
    return items[index] if 0 <= index < len(items) else None


def select_combo_item(combo, index):
    # 通过标准窗口消息选择账号并通知登录窗口。
    _SendMessageW(combo, CB_SETCURSEL, index, 0)
    parent = win32gui.GetParent(combo)
    _SendMessageW(parent, win32con.WM_COMMAND, (CBN_SELCHANGE << 16) | win32gui.GetDlgCtrlID(combo), combo)
    return _SendMessageW(combo, CB_GETCURSEL, 0, 0) == index


class AccountConfigNotDetected(Exception):
    """Stop stamina spending when an account-specific config cannot be resolved."""


class AccountAlreadyCompleted(Exception):
    pass

# Number of independent per-account DailyTask override slots shown in the UI
NUM_ACCOUNT_SLOTS = 5

SKIP_ACCOUNTS = 'Skip Accounts'

_SUPPORT_TASKS = ["Tacet Suppression", "Forgery Challenge", "Simulation Challenge"]
_MATERIAL_OPTIONS = ['Resonator EXP', 'Weapon EXP', 'Shell Credit']
_ADDITIONAL_TASK_OPTIONS = [
    CHECK_WEEKLY_GARDEN, AUTO_FARM_RESIDUAL_NEST,
    MERGE_ECHO_IF_DISCARDED_OVER_1000, TELEPORT_AND_FARM_4C_ECHO,
]

# DailyTask config keys that each per-account slot can override
SLOT_OVERRIDABLE = [
    'Which to Farm',
    'Which Tacet Suppression to Farm',
    'Which Forgery Challenge to Farm',
    'Material Selection',
    'Farm Nightmare Nest for Daily Echo',
    ADDITIONAL_TASKS,
]


def _slot_enable(n: int) -> str:
    return f'Account {n} Daily Task Override'


def _slot_keyword(n: int) -> str:
    return f'Account {n} Keyword'


def _slot_profile_code(n: int) -> str:
    return f'Account {n} In-game Profile Code'


def _slot_key(n: int, key: str) -> str:
    return f'Account {n}: {key}'


def normalize_account_name(account):
    if not account:
        return account
    return account.lower().replace('0', 'o').replace('.con', '.com')


def normalize_profile_code(value):
    """Keep OCR digits only, accepting the common O/0 substitution."""
    if not value:
        return ''
    return re.sub(r'\D', '', str(value).upper().replace('O', '0'))


class MultiAccountDailyTask(WWOneTimeTask, BaseCombatTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "👥 Multi Account Daily Task"
        self.description = "Automatically switch accounts and run Daily Task for each account"
        self.done_set = set()
        self.failed_set = set()
        self.all_accounts = set()
        self.support_schedule_task = True

        # ---- base config (skip list) ----
        self.default_config = {
            SKIP_ACCOUNTS: [], EMAIL_ENABLED: False,
            EMAIL_SENDER: '', EMAIL_AUTH: '', EMAIL_TO: '',
        }
        self.config_description = {
            SKIP_ACCOUNTS: (
                'Accounts to skip. Enter partial or full account names '
                '(e.g. aa****01@example.com). Matching is case-insensitive substring.'
            ),
        }
        self.config_type = {
            EMAIL_ENABLED: {'sub_configs': {True: [EMAIL_SENDER, EMAIL_AUTH, EMAIL_TO]}},
        }

        # Shared description strings (reused across slots for compact i18n)
        desc_enable = 'Enable independent Daily Task configuration for this account slot.'
        desc_keyword = 'Account name substring to match (case-insensitive, e.g. aa****01).'
        desc_farm = (
            'Tacet: set "Which Tacet Suppression to Farm"; '
            'Forgery: set "Which Forgery Challenge to Farm"; '
            'Simulation: set "Material Selection".'
        )
        desc_tacet = 'The Tacet Suppression number in the F2 list.'
        desc_forgery = 'The Forgery Challenge number in the F2 list.'
        desc_material = 'Resonator EXP / Weapon EXP / Shell Credit'
        desc_nm = 'Farm 1 Echo from a residual settlement to complete Daily Task when needed.'
        desc_tasks = (
            'Select optional tasks. Stamina farming runs first; residual settlements run afterward when needed, '
            'and the other tasks run last.'
        )

        # ---- per-account override slots ----
        for n in range(1, NUM_ACCOUNT_SLOTS + 1):
            enable_key = _slot_enable(n)
            keyword_key = _slot_keyword(n)
            profile_code_key = _slot_profile_code(n)
            farm_key = _slot_key(n, 'Which to Farm')
            tacet_key = _slot_key(n, 'Which Tacet Suppression to Farm')
            forgery_key = _slot_key(n, 'Which Forgery Challenge to Farm')
            material_key = _slot_key(n, 'Material Selection')
            nm_key = _slot_key(n, 'Farm Nightmare Nest for Daily Echo')
            tasks_key = _slot_key(n, ADDITIONAL_TASKS)

            # Defaults — mirror DailyTask defaults
            self.default_config[enable_key] = False
            self.default_config[keyword_key] = ''
            self.default_config[profile_code_key] = ''
            self.default_config[farm_key] = _SUPPORT_TASKS[0]
            self.default_config[tacet_key] = 1
            self.default_config[forgery_key] = 1
            self.default_config[material_key] = 'Shell Credit'
            self.default_config[nm_key] = True
            self.default_config[tasks_key] = [CHECK_WEEKLY_GARDEN]

            # Config descriptions
            self.config_description[enable_key] = desc_enable
            self.config_description[keyword_key] = desc_keyword
            self.config_description[profile_code_key] = (
                'Enter at least the last 4 digits of the Profile Code shown on '
                'the in-game ESC screen. Longer values are safer.'
            )
            self.config_description[farm_key] = desc_farm
            self.config_description[tacet_key] = desc_tacet
            self.config_description[forgery_key] = desc_forgery
            self.config_description[material_key] = desc_material
            self.config_description[nm_key] = desc_nm
            self.config_description[tasks_key] = desc_tasks

            # Enable switch — shows keyword + Which to Farm + NM + Additional Tasks
            self.config_type[enable_key] = {
                'sub_configs': {
                    True: [keyword_key, profile_code_key, farm_key, nm_key, tasks_key],
                }
            }

            # Which to Farm dropdown — sub_configs cascade recursively
            self.config_type[farm_key] = {
                'type': 'drop_down',
                'options': _SUPPORT_TASKS,
                'sub_configs': {
                    'Tacet Suppression': [tacet_key],
                    'Forgery Challenge': [forgery_key],
                    'Simulation Challenge': [material_key],
                }
            }

            self.config_type[material_key] = {
                'type': 'drop_down',
                'options': _MATERIAL_OPTIONS,
            }
            self.config_type[tasks_key] = {
                'type': 'multi_selection',
                'options': _ADDITIONAL_TASK_OPTIONS,
            }

        # Register after building the config dictionaries so the exit option survives.
        self.add_exit_after_config()

    # ------------------------------------------------------------------
    # Account state helpers
    # ------------------------------------------------------------------

    def _mark_done(self, account):
        normalized = normalize_account_name(account)
        if normalized:
            self.done_set.add(normalized)
            self._account_state.record(self._daily_date, [normalized], 'success')
            self._update_daily_status_info()

    def _mark_failed(self, account):
        normalized = normalize_account_name(account)
        if normalized:
            self.failed_set.add(normalized)
            if normalized not in self._account_state.statuses(self._daily_date):
                self._account_state.record(self._daily_date, [normalized], 'failed')
            self._update_daily_status_info()

    def _update_daily_status_info(self):
        status_names = {
            'running': '执行中', 'success': '成功', 'failed': '失败',
            'login_timeout': '登录超时', 'interrupted': '中断',
        }
        self.info_set('账号每日状态', {
            account: status_names[status]
            for account, status in self._account_state.statuses(self._daily_date).items()
            if not account.startswith('profile:')
        })

    def _is_done(self, account):
        normalized = normalize_account_name(account)
        if not normalized:
            return False
        if normalized in self.done_set or normalized in self.failed_set:
            return True
        return self._is_skipped(account)

    def _is_skipped(self, account):
        """Return True if the account matches any entry in the Skip Accounts list."""
        normalized = normalize_account_name(account)
        if not normalized:
            return False
        skip_list = self.config.get(SKIP_ACCOUNTS) or []
        for entry in skip_list:
            entry_norm = normalize_account_name(entry)
            if entry_norm and (entry_norm in normalized or normalized in entry_norm):
                self.log_info(
                    self.tr('Skipping account {account} (matches skip rule: {rule})').format(
                        account=account, rule=entry
                    )
                )
                return True
        return False

    def _same_account(self, left, right):
        return normalize_account_name(left) == normalize_account_name(right)

    # ------------------------------------------------------------------
    # Per-account DailyTask config override helpers
    # ------------------------------------------------------------------

    def _get_account_overrides(self, account):
        """Return the first matching per-account slot config overrides dict, or {}."""
        normalized = normalize_account_name(account)
        if not normalized:
            return {}
        for n in range(1, NUM_ACCOUNT_SLOTS + 1):
            if not self.config.get(_slot_enable(n)):
                continue
            identifiers = [
                (self.config.get(_slot_keyword(n)) or '').strip(),
            ]
            identifiers = [normalize_account_name(value) for value in identifiers if value]
            profile_code = normalize_profile_code(self.config.get(_slot_profile_code(n)))
            profile_digits = normalize_profile_code(account)
            matched_profile = (
                len(profile_code) >= 4 and profile_code in profile_digits
            )
            if not identifiers and not profile_code:
                continue
            if matched_profile or any(value in normalized or normalized in value for value in identifiers):
                overrides = {}
                for daily_key in SLOT_OVERRIDABLE:
                    val = self.config.get(_slot_key(n, daily_key))
                    if val is not None:
                        if daily_key == ADDITIONAL_TASKS and val:
                            # Migrate old saved slots that used the ambiguous
                            # full-Nightmare label.  The multi-account runner
                            # has always constrained this path to settlements.
                            val = [
                                AUTO_FARM_RESIDUAL_NEST
                                if item == AUTO_FARM_NIGHTMARE_NEST else item
                                for item in val
                            ]
                        overrides[daily_key] = val
                self.log_info(
                    self.tr('Applying account config override for {account}: slot {n}').format(
                        account=account, n=n
                    )
                )
                return overrides
        return {}

    def _account_overrides_required(self):
        """Return whether this run uses account-specific DailyTask settings."""
        return any(
            self.config.get(_slot_enable(n))
            for n in range(1, NUM_ACCOUNT_SLOTS + 1)
        )

    def _open_account_login_for_reselection(self):
        """Reach the in-game account dropdown through a bounded world transition."""
        if self._login_combo() or self.do_find_account_drop_down():
            return
        # The title-screen Switch Account button opens a separate native login
        # window which BitBlt cannot reliably capture. Finish the normal Connect
        # transition first, then use the in-game logout flow whose account list
        # is visible to the configured game-window capture.
        if not self.in_team_and_world():
            self.ensure_main(time_out=180)
        self._switch_to_login()

    def _apply_daily_overrides(self, daily_task, overrides):
        """Patch DailyTask config with per-account overrides; return saved originals."""
        originals = {}
        for key, value in overrides.items():
            if key in SLOT_OVERRIDABLE:
                originals[key] = daily_task.config.get(key)
                daily_task.config[key] = value
        return originals

    def _restore_daily_overrides(self, daily_task, originals):
        """Restore DailyTask config keys that were patched by _apply_daily_overrides."""
        for key, value in originals.items():
            if value is None:
                daily_task.config.pop(key, None)
            else:
                daily_task.config[key] = value

    # ------------------------------------------------------------------
    # DailyTask runner with per-account config + error isolation
    # ------------------------------------------------------------------

    def _run_daily_for_account(self, account):
        result = {'account': account or '当前账号（未识别）', 'status': '中断'}
        self._current_email_result = result
        identities = {normalize_account_name(account)} if account else set()
        state_status = 'interrupted'
        self._account_state.record(self._daily_date, identities, 'running')
        self._update_daily_status_info()
        try:
            succeeded = self._execute_daily_for_account(account)
            result['status'] = '成功' if succeeded else '失败'
            state_status = 'success' if succeeded else 'failed'
            return succeeded
        except AccountAlreadyCompleted:
            result['status'] = '当天已完成，跳过'
            self.log_info('当前账号当天已完成日常任务，跳过执行')
            state_status = 'success'
            return True
        except LoginTimeoutError:
            result['status'] = '登录超时'
            state_status = 'login_timeout'
            return False
        except AccountConfigNotDetected:
            result['status'] = '未匹配账号配置'
            state_status = 'failed'
            raise
        finally:
            if result.get('profile_code'):
                identities.add(f"profile:{result['profile_code']}")
            self._account_state.record(self._daily_date, identities, state_status)
            self._update_daily_status_info()
            if hasattr(self, '_email_results'):
                self._email_results.append(result)
            self._current_email_result = None

    def _execute_daily_for_account(self, account):
        """Run DailyTask for *account* with optional per-account config override.

        - Applies slot overrides only after the in-game ESC Profile Code is read.
        - Catches non-fatal exceptions so a single failure does not abort the
          whole multi-account run.
        - Re-raises TaskDisabledException so the executor can stop cleanly.
        - Returns True on success, False on failure.
        """
        daily_task = self.get_task_by_class(DailyTask)
        resolved_account = account
        originals = {}
        overrides_applied = False
        profile_checked = False

        def apply_account_overrides(detected_account):
            nonlocal resolved_account, overrides_applied
            if overrides_applied or not detected_account:
                return
            resolved_account = detected_account
            if getattr(self, '_current_email_result', None) is not None:
                self._current_email_result['account'] = account or detected_account
            overrides = self._get_account_overrides(detected_account)
            if self._account_overrides_required() and not overrides:
                raise AccountConfigNotDetected(
                    self.tr('No account-specific Daily Task config matched {account}').format(
                        account=detected_account
                    )
                )
            originals.update(self._apply_daily_overrides(daily_task, overrides))
            overrides_applied = True

        original_is_main = getattr(daily_task, 'is_main', None)
        is_main_was_overridden = 'is_main' in daily_task.__dict__

        def is_main_with_account_profile_code(*args, **kwargs):
            nonlocal profile_checked
            result = original_is_main(*args, **kwargs)
            if result and not profile_checked:
                profile_checked = True
                detected_account = self._detect_account_from_world_profile()
                if detected_account:
                    profile_code = normalize_profile_code(detected_account)
                    self._current_email_result['profile_code'] = profile_code
                    if f'profile:{profile_code}' in self._account_state.completed(self._daily_date):
                        raise AccountAlreadyCompleted()
                apply_account_overrides(detected_account)
                if not overrides_applied and self._account_overrides_required():
                    raise AccountConfigNotDetected(
                        self.tr('Could not identify account config after entering the game world')
                    )
            return result

        if not overrides_applied and original_is_main is not None:
            daily_task.is_main = is_main_with_account_profile_code
        focus_was_allowed = getattr(daily_task, '_allow_bring_to_front', False)
        # DailyTask owns the initial login wait.  Physical login controls must
        # be clicked with the game in front, just like later account switches.
        daily_task._allow_bring_to_front = True
        # DailyTask uses this marker to make legacy global configs explicit in
        # the log and to suppress its full Nightmare Purification branch.
        residual_only_was_set = '_multi_account_residual_only' in daily_task.__dict__
        previous_residual_only = getattr(daily_task, '_multi_account_residual_only', None)
        daily_task._multi_account_residual_only = True
        # Both daily echo capture and full farming share this task. Restrict
        # only this account run, without rewriting the standalone task config.
        nest_task = self.get_task_by_class(NightmareNestTask)
        previous_targets = getattr(nest_task, 'farm_targets_override', None)
        targets_were_overridden = 'farm_targets_override' in nest_task.__dict__
        nest_task.farm_targets_override = ['Tacet Discord Nest']
        try:
            self.run_task_by_class(DailyTask)
            return True
        except AccountAlreadyCompleted:
            raise
        except LoginTimeoutError:
            raise
        except AccountConfigNotDetected:
            raise
        except TaskDisabledException:
            raise
        except Exception as e:
            self.log_error(
                self.tr('DailyTask failed for account {account}, continuing to next account').format(
                    account=resolved_account or '(current)'
                ),
                e,
            )
            self.screenshot('daily_task_failed')
            return False
        finally:
            if targets_were_overridden:
                nest_task.farm_targets_override = previous_targets
            else:
                nest_task.__dict__.pop('farm_targets_override', None)
            daily_task._allow_bring_to_front = focus_was_allowed
            if residual_only_was_set:
                daily_task._multi_account_residual_only = previous_residual_only
            else:
                daily_task.__dict__.pop('_multi_account_residual_only', None)
            if is_main_was_overridden:
                daily_task.is_main = original_is_main
            else:
                daily_task.__dict__.pop('is_main', None)
            self._restore_daily_overrides(daily_task, originals)

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    def run(self):
        started = datetime.now().astimezone()
        self._email_results = []
        status = '异常中断'
        try:
            self._run_accounts()
            status = '完成'
            if any(result['status'] not in {'成功', '当天已完成，跳过'} for result in self._email_results):
                status = '完成（存在失败或未完成的尝试）'
        except TaskDisabledException:
            status = '已停止'
            raise
        finally:
            try:
                if send_daily_report(self._email_results, started, datetime.now().astimezone(), status,
                                     config=self.config):
                    self.log_info('多账号日常汇总邮件已发送')
            except Exception:
                # SMTP errors may contain credentials or addresses. Never log them.
                self.log_warning('日常汇总邮件发送失败，请检查多账号任务的邮箱配置、SMTP 服务和网络')

    def _load_daily_state(self):
        self._account_state = DailyAccountState(
            Path(self.config.config_file).parent / 'multi_account_daily_state.sqlite3'
        )
        self._daily_date = self._account_state.day()
        for account, status in self._account_state.statuses(self._daily_date).items():
            if status == 'running':
                self._account_state.record(self._daily_date, [account], 'interrupted')
        self.done_set.clear()
        self.done_set.update(
            account for account in self._account_state.completed(self._daily_date)
            if not account.startswith('profile:')
        )
        self.failed_set.clear()
        self.all_accounts.clear()
        self._update_daily_status_info()

    def _run_accounts(self):
        WWOneTimeTask.run(self)
        self._load_daily_state()
        # Keep the selected login-list account for completion bookkeeping.  It
        # is deliberately not used to choose DailyTask settings; those are
        # resolved from the in-game ESC Profile Code below.
        initial_account = None
        if self._login_combo() or self.do_find_account_drop_down():
            initial_account = self._select_and_login_account()
            if initial_account is None:
                return
            self.log_info(
                self.tr('Detected initial account before DailyTask: {account}').format(
                    account=initial_account or '(unknown)'
                )
            )

        daily_succeeded = False
        needs_reselection = False
        try:
            daily_succeeded = self._run_daily_for_account(initial_account)
        except AccountConfigNotDetected as error:
            self.log_warning(
                self.tr('Account config was not recognized; returning to login and reselecting account'),
                error,
            )
            needs_reselection = True

        # Run the reselection path outside the exception handler so an
        # unrelated later DailyTask failure does not retain the account-detect
        # exception as misleading traceback context.
        if needs_reselection:
            self._open_account_login_for_reselection()
            initial_account = self._select_and_login_account()
            if initial_account is None:
                self.log_info(self.tr('All configured accounts have been processed'))
                return
            # A second miss is terminal by design: never spend stamina with a
            # default config when account-specific overrides are enabled.
            daily_succeeded = self._run_daily_for_account(initial_account)
        self._return_to_account_list(restart=not daily_succeeded)
        # OCR here is only for login-list progress bookkeeping.  DailyTask
        # settings have already been selected from the in-game ESC Profile
        # Code and never depend on this short-lived login-screen text.
        detected = self._detect_current_account_from_login()
        processed_account = initial_account or detected
        if initial_account is None and processed_account and self._email_results:
            result = self._email_results[-1]
            result['account'] = processed_account
            if result['status'] == '登录超时':
                self._account_state.record(
                    self._daily_date, [normalize_account_name(processed_account)], 'login_timeout'
                )
        if daily_succeeded:
            self._mark_done(processed_account)
        else:
            self._mark_failed(processed_account)

        self.info_set('Completed', self.done_set)
        self.info_set('Failed', self.failed_set)

        while next_account := self._select_and_login_account():
            self.info_set('Completed', self.done_set)
            self.info_set('Failed', self.failed_set)
            daily_succeeded = self._run_daily_for_account(next_account)
            if daily_succeeded:
                self._mark_done(next_account)
            else:
                self._mark_failed(next_account)
            self._return_to_account_list(restart=not daily_succeeded)

    def _return_to_account_list(self, restart=False):
        if self._login_combo() or self.do_find_account_drop_down():
            return
        if not restart:
            self.ensure_main(time_out=100)
            self._switch_to_login()
            return
        self.log_warning('当前账号无法完成登录或日常任务，重新启动游戏并返回账号列表')
        self.executor.check_enabled()
        device_manager = self.executor.device_manager
        device_manager.stop_hwnd()
        deadline = time.monotonic() + 30
        while device_manager.hwnd_window.hwnd and win32gui.IsWindow(device_manager.hwnd_window.hwnd):
            self.executor.check_enabled()
            if time.monotonic() >= deadline:
                raise TimeoutError('关闭游戏超过 30 秒，无法切换账号')
            if self.executor.exit_event.wait(0.2):
                raise TaskDisabledException()
        device_manager.do_refresh(True)
        if not self._app.start_controller.start_device():
            raise RuntimeError('重新启动游戏失败，无法返回账号列表')
        self.executor.check_enabled()
        self.logged_in = False
        self.executor.reset_scene()
        self.next_frame()
        self._allow_bring_to_front = True
        try:
            def open_account_list():
                if self._login_combo() or self.do_find_account_drop_down():
                    return True
                if switch_account := self.find_one('switch_account', vertical_variance=0.1, threshold=0.7):
                    self._click_direct(switch_account, after_sleep=1)
                    confirmation = self.wait_feature(
                        ['confirm_btn_hcenter_vcenter', 'confirm_btn_highlight_hcenter_vcenter'],
                        time_out=10, threshold=0.6, raise_if_not_found=True,
                    )
                    self._click_direct(confirmation, after_sleep=1)
                return False

            self.wait_until(open_account_list, time_out=180, raise_if_not_found=True)
        finally:
            self._allow_bring_to_front = False


    def _click_center_offset(self, offset_x, offset_y, after_sleep=0.5):
        h, w = self.frame.shape[:2]
        rel_x = 0.5 + offset_x / w
        rel_y = 0.5 + offset_y / h
        self.click_relative(rel_x, rel_y, after_sleep=after_sleep)

    def _switch_to_login(self):
        self.log_info(self.tr('Switching back to login screen'))
        self.send_key('esc', after_sleep=1.5)
        self.wait_feature('esc_setting')
        self.click_relative(0.04, 0.96, after_sleep=1)
        self.click_confirm(timeout=10)
        self.wait_until(lambda: self._login_combo() or self.do_find_account_drop_down(),
                        time_out=60, settle_time=2, raise_if_not_found=True)
        self.log_info(self.tr('Back at login screen'))

    def _login_combo(self):
        return find_login_combo(self.hwnd.hwnd if self.hwnd else 0)

    def _select_account_by_combo(self, combo):
        accounts = combo_items(combo)
        for name in accounts:
            self.all_accounts.add(normalize_account_name(name))
        self.info_set('All Accounts', self.all_accounts)
        for index, name in enumerate(accounts):
            if self._is_done(name):
                continue
            if not select_combo_item(combo, index):
                raise Exception(self.tr('Failed to switch account'))
            self.log_info(self.tr('Confirmed selected account: {account}').format(account=name))
            return name
        return None

    def _detect_current_account_from_login(self):
        if combo := self._login_combo():
            if account := combo_selected_item(combo):
                self.log_info(self.tr('Current account: {account}').format(account=account))
                return account
        texts = self.ocr(match=account_pattern)
        if texts:
            self.log_info(self.tr('Current account: {account}').format(account=texts[0]))
            return texts[0].name
        return None

    def _detect_account_from_world_profile(self):
        """Open the ESC profile page and match a configured profile code."""
        configured_codes = []
        for n in range(1, NUM_ACCOUNT_SLOTS + 1):
            if not self.config.get(_slot_enable(n)):
                continue
            code = normalize_profile_code(self.config.get(_slot_profile_code(n)))
            if len(code) >= 4:
                configured_codes.append(code)
        if not self.in_team_and_world():
            return None

        opened = False
        try:
            self.send_key('esc', after_sleep=1.5)
            opened = bool(self.wait_feature('esc_setting', time_out=10, raise_if_not_found=False))
            if not opened:
                self.log_warning(self.tr('Could not open the ESC profile screen for Profile Code detection'))
                return None
            # The stable copy is on the left profile card.  Restricting OCR to
            # this area avoids matching unrelated levels and notification
            # counts elsewhere on the ESC screen.
            for text in self.ocr(0.18, 0.32, 0.38, 0.45):
                digits = normalize_profile_code(text.name)
                if (configured_codes and any(code in digits for code in configured_codes)) or (
                        not configured_codes and len(digits) >= 7):
                    self.log_info(
                        self.tr('Detected account from in-game Profile Code: {account}').format(
                            account=text.name
                        )
                    )
                    return text.name
            return None
        finally:
            if opened:
                self.send_key('esc', after_sleep=1)
                self.wait_in_team_and_world(time_out=10, raise_if_not_found=False)

    _click_direct = BaseWWTask.click_direct

    def _click_account_in_list(self):
        accounts = self.ocr(match=account_pattern)
        if not accounts:
            return None
        next_account = None
        # self.screenshot('_click_account_in_list')
        for account in accounts:
            self.all_accounts.add(normalize_account_name(account.name))
            self.info_set('All Accounts', self.all_accounts)
            if next_account is None and not self._is_done(account.name):
                next_account = account.name
                self._click_direct(account, after_sleep=2)
        self.log_info(self.tr('Click next account: {account}').format(account=next_account))
        # A loaded account list with no eligible entry is a successful terminal
        # state, not a reason to keep waiting until wait_until raises a timeout.
        return next_account if next_account is not None else _ACCOUNT_LIST_EXHAUSTED

    def _select_and_login_account(self):
        while True:
            self._login_attempt_account = None
            try:
                return self._select_and_login_account_once()
            except TaskDisabledException:
                if self._login_attempt_account:
                    self._account_state.record(
                        self._daily_date, [normalize_account_name(self._login_attempt_account)], 'interrupted'
                    )
                    self._update_daily_status_info()
                    self._email_results.append({'account': self._login_attempt_account, 'status': '中断'})
                raise
            except LoginTimeoutError as error:
                account = self._login_attempt_account
                if not account:
                    raise
                self.log_warning(f'账号 {account} 登录超过 3 分钟，继续处理下一个账号：{error}')
                self._mark_failed(account)
                self._account_state.record(
                    self._daily_date, [normalize_account_name(account)], 'login_timeout'
                )
                self._update_daily_status_info()
                self._email_results.append({'account': account, 'status': '登录超时'})
            self._return_to_account_list(restart=True)

    def _select_and_login_account_once(self):
        current_account = None
        mouse_reset_task = self.executor.get_task_by_class(MouseResetTask)
        mouse_reset_was_enabled = mouse_reset_task.enabled if mouse_reset_task else False
        if mouse_reset_was_enabled:
            mouse_reset_task.disable()
        # Enable foreground-focus acquisition only for the login/account-switch
        # interaction; restored unconditionally in the finally block so the game
        # window never steals focus during normal task execution.
        self._allow_bring_to_front = True
        try:
            combo = self._login_combo()
            if combo:
                current_account = self._select_account_by_combo(combo)
                if not current_account:
                    self.log_info(self.tr('All configured accounts have been processed'))
                    return None
            max_retries = 0 if combo else 5
            for attempt in range(1, max_retries + 1):
                self.ensure_in_front()
                self.sleep(1)
                drop_down = self.find_account_drop_down()
                if drop_down:
                    self._click_direct(drop_down, after_sleep=2)
                if self.do_find_account_drop_down():
                    self.log_error('click drop down no effect')
                    self.screenshot('multi')
                    continue
                account = self.wait_until(
                    lambda: self._click_account_in_list(),
                    time_out=10, raise_if_not_found=True
                )
                if account is _ACCOUNT_LIST_EXHAUSTED:
                    self.log_info(self.tr('All configured accounts have been processed'))
                    return None
                self.sleep(1)
                current_account = self._detect_current_account_from_login()
                self.log_info(self.tr('Selected account: {selected}, displayed account: {displayed}').format(
                    selected=account, displayed=current_account))
                if self._same_account(account, current_account):
                    self.log_info(self.tr('Confirmed selected account: {account}').format(account=account))
                    break
                if attempt < max_retries:
                    self.log_info(self.tr('Account display does not match, retrying ({attempt}/{max_retries})').format(
                        attempt=attempt, max_retries=max_retries))
                else:
                    self.log_error(self.tr(
                        'Account selection failed after {max_retries} retries; {account} is still not displayed. Continuing login attempt'
                    ).format(max_retries=max_retries, account=account))
                    raise Exception(self.tr('Failed to switch account'))
            self._login_attempt_account = current_account
            self._account_state.record(
                self._daily_date, [normalize_account_name(current_account)], 'running'
            )
            self._update_daily_status_info()
            self.sleep(4)
            texts = self.ocr()
            login_btn = self.find_boxes(texts, boundary=self.box_of_screen(0.3, 0.3, 0.7, 0.8),
                                        match=LOGIN_TEXTS)
            if login_btn:
                self._click_direct(login_btn, after_sleep=3)
            else:
                self._click_direct((int(self.width * 0.5), int(self.height * 0.568)), after_sleep=3)
            self.logged_in = False
            self.ensure_main(time_out=180)
            self.log_info(self.tr('Login successful'))
            return current_account
        finally:
            self._allow_bring_to_front = False
            if mouse_reset_was_enabled:
                mouse_reset_task.enable()


    def find_account_drop_down(self):
        return self.wait_until(self.do_find_account_drop_down, time_out=60, settle_time=2, raise_if_not_found=True)

    def do_find_account_drop_down(self) -> Box | None:
        texts = self.ocr()
        account_boxes = self.find_boxes(texts, account_pattern)
        login_boxes = self.find_boxes(texts, LOGIN_TEXTS)
        if len(account_boxes) == 1 and login_boxes:
            return account_boxes[0]
        return None


from ok import run_task
from config import config

if __name__ == "__main__":
    run_task(config, task=MultiAccountDailyTask, debug=True)
