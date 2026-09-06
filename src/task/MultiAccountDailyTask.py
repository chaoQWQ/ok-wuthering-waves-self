import re

from ok import Box, TaskDisabledException
from src.task.DailyTask import (
    DailyTask, ADDITIONAL_TASKS, CHECK_WEEKLY_GARDEN,
    AUTO_FARM_NIGHTMARE_NEST, MERGE_ECHO_IF_DISCARDED_OVER_1000,
    TELEPORT_AND_FARM_4C_ECHO,
)
from src.task.WWOneTimeTask import WWOneTimeTask
from src.task.BaseCombatTask import BaseCombatTask
from src.task.BaseWWTask import LOGIN_TEXTS
from src.task.MouseResetTask import MouseResetTask

account_pattern = re.compile(r'\*\*\*\*')

# Number of independent per-account DailyTask override slots shown in the UI
NUM_ACCOUNT_SLOTS = 5

SKIP_ACCOUNTS = 'Skip Accounts'

_SUPPORT_TASKS = ["Tacet Suppression", "Forgery Challenge", "Simulation Challenge"]
_MATERIAL_OPTIONS = ['Resonator EXP', 'Weapon EXP', 'Shell Credit']
_ADDITIONAL_TASK_OPTIONS = [
    CHECK_WEEKLY_GARDEN, AUTO_FARM_NIGHTMARE_NEST,
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


def _slot_key(n: int, key: str) -> str:
    return f'Account {n}: {key}'


def normalize_account_name(account):
    if not account:
        return account
    return account.lower().replace('0', 'o').replace('.con', '.com')


class MultiAccountDailyTask(WWOneTimeTask, BaseCombatTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "👥 Multi Account Daily Task"
        self.description = "Automatically switch accounts and run Daily Task for each account"
        self.add_exit_after_config()
        self.done_set = set()
        self.all_accounts = set()
        self.support_schedule_task = True

        # ---- base config (skip list) ----
        self.default_config = {SKIP_ACCOUNTS: []}
        self.config_description = {
            SKIP_ACCOUNTS: (
                'Accounts to skip. Enter partial or full account names '
                '(e.g. aa****01@example.com). Matching is case-insensitive substring.'
            ),
        }
        self.config_type = {}

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
        desc_nm = 'Farm 1 Echo from Nightmare Nest to complete Daily Task when needed.'
        desc_tasks = (
            'Select optional tasks. Nightmare Nest runs before stamina farming; '
            'the other tasks run afterward.'
        )

        # ---- per-account override slots ----
        for n in range(1, NUM_ACCOUNT_SLOTS + 1):
            enable_key = _slot_enable(n)
            keyword_key = _slot_keyword(n)
            farm_key = _slot_key(n, 'Which to Farm')
            tacet_key = _slot_key(n, 'Which Tacet Suppression to Farm')
            forgery_key = _slot_key(n, 'Which Forgery Challenge to Farm')
            material_key = _slot_key(n, 'Material Selection')
            nm_key = _slot_key(n, 'Farm Nightmare Nest for Daily Echo')
            tasks_key = _slot_key(n, ADDITIONAL_TASKS)

            # Defaults — mirror DailyTask defaults
            self.default_config[enable_key] = False
            self.default_config[keyword_key] = ''
            self.default_config[farm_key] = _SUPPORT_TASKS[0]
            self.default_config[tacet_key] = 1
            self.default_config[forgery_key] = 1
            self.default_config[material_key] = 'Shell Credit'
            self.default_config[nm_key] = True
            self.default_config[tasks_key] = [CHECK_WEEKLY_GARDEN]

            # Config descriptions
            self.config_description[enable_key] = desc_enable
            self.config_description[keyword_key] = desc_keyword
            self.config_description[farm_key] = desc_farm
            self.config_description[tacet_key] = desc_tacet
            self.config_description[forgery_key] = desc_forgery
            self.config_description[material_key] = desc_material
            self.config_description[nm_key] = desc_nm
            self.config_description[tasks_key] = desc_tasks

            # Enable switch — shows keyword + Which to Farm + NM + Additional Tasks
            self.config_type[enable_key] = {
                'sub_configs': {
                    True: [keyword_key, farm_key, nm_key, tasks_key],
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

    # ------------------------------------------------------------------
    # Account state helpers
    # ------------------------------------------------------------------

    def _mark_done(self, account):
        normalized = normalize_account_name(account)
        if normalized:
            self.done_set.add(normalized)

    def _is_done(self, account):
        normalized = normalize_account_name(account)
        if not normalized:
            return False
        if normalized in self.done_set:
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
            keyword = (self.config.get(_slot_keyword(n)) or '').strip()
            if not keyword:
                continue
            kw_norm = normalize_account_name(keyword)
            if kw_norm and (kw_norm in normalized or normalized in kw_norm):
                overrides = {}
                for daily_key in SLOT_OVERRIDABLE:
                    val = self.config.get(_slot_key(n, daily_key))
                    if val is not None:
                        overrides[daily_key] = val
                self.log_info(
                    self.tr('Applying account config override for {account}: slot {n}').format(
                        account=account, n=n
                    )
                )
                return overrides
        return {}

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
        """Run DailyTask for *account* with optional per-account config override.

        - Applies slot overrides from 'Account N Daily Task Override' config before running.
        - Catches non-fatal exceptions so a single failure does not abort the
          whole multi-account run.
        - Re-raises TaskDisabledException so the executor can stop cleanly.
        - Returns True on success, False on failure.
        """
        daily_task = self.get_task_by_class(DailyTask)
        overrides = self._get_account_overrides(account) if account else {}
        originals = self._apply_daily_overrides(daily_task, overrides)
        try:
            self.run_task_by_class(DailyTask)
            return True
        except TaskDisabledException:
            raise
        except Exception as e:
            self.log_error(
                self.tr('DailyTask failed for account {account}, continuing to next account').format(
                    account=account or '(current)'
                ),
                e,
            )
            self.screenshot('daily_task_failed')
            try:
                self.ensure_main(time_out=120)
            except Exception as recovery_err:
                self.log_warning(
                    self.tr(
                        'ensure_main failed after DailyTask error for account {account}, '
                        '_switch_to_login will attempt recovery'
                    ).format(account=account or '(current)'),
                    recovery_err,
                )
            return False
        finally:
            self._restore_daily_overrides(daily_task, originals)

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    def run(self):
        WWOneTimeTask.run(self)
        self.done_set.clear()
        self.all_accounts.clear()

        # Run DailyTask for the account that is currently logged in.
        # Account identity is detected after returning to the login screen.
        self._run_daily_for_account(None)
        self.ensure_main(time_out=100)
        self._switch_to_login()
        detected = self._detect_current_account_from_login()
        self._mark_done(detected)

        self.info_set('Completed', self.done_set)

        while next_account := self._select_and_login_account():
            self.info_set('Completed', self.done_set)
            self._run_daily_for_account(next_account)
            self._mark_done(next_account)
            self.ensure_main(time_out=100)
            self._switch_to_login()

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
        self.find_account_drop_down()
        self.log_info(self.tr('Back at login screen'))

    def _detect_current_account_from_login(self):
        texts = self.ocr(match=account_pattern)
        if texts:
            self.log_info(self.tr('Current account: {account}').format(account=texts[0]))
            return texts[0].name
        return None

    def _click_account_in_list(self):
        accounts = self.ocr(match=account_pattern)
        next_account = None
        # self.screenshot('_click_account_in_list')
        for account in accounts:
            self.all_accounts.add(normalize_account_name(account.name))
            self.info_set('All Accounts', self.all_accounts)
            if next_account is None and not self._is_done(account.name):
                next_account = account.name
                self.click(account, after_sleep=2)
        self.log_info(self.tr('Click next account: {account}').format(account=next_account))
        return next_account

    def _select_and_login_account(self):
        current_account = None
        mouse_reset_task = self.executor.get_task_by_class(MouseResetTask)
        mouse_reset_was_enabled = mouse_reset_task.enabled if mouse_reset_task else False
        if mouse_reset_was_enabled:
            mouse_reset_task.disable()
        try:
            max_retries = 5
            for attempt in range(1, max_retries + 1):
                # self.ensure_in_front()
                # self.update_capture({
                #     'windows': {
                #         'interaction': 'Pynput',
                #         'capture_method': 'ForegroundBitBlt',
                #     }
                # })
                self.sleep(1)
                drop_down = self.find_account_drop_down()
                if drop_down:
                    self.click(drop_down, after_sleep=2)
                if self.do_find_account_drop_down():
                    self.log_error('click drop down no effect')
                    self.screenshot('multi')
                    continue
                account = self.wait_until(
                    lambda: self._click_account_in_list(),
                    time_out=10, raise_if_not_found=True
                )
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
            self.sleep(4)
            texts = self.ocr()
            login_btn = self.find_boxes(texts, boundary=self.box_of_screen(0.3, 0.3, 0.7, 0.8),
                                        match=LOGIN_TEXTS)
            if login_btn:
                self.click(login_btn, after_sleep=3)
            else:
                self.click_relative(0.5, 0.568, hcenter=True, vcenter=True, after_sleep=3)
            self.logged_in = False
            # self.update_capture({
            #     'windows': {
            #         'interaction': 'PostMessage',
            #         'capture_method': ['WGC', 'BitBlt_RenderFull'],
            #     }
            # })
            self.ensure_main(time_out=180)
            self.log_info(self.tr('Login successful'))
            return current_account
        finally:
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
