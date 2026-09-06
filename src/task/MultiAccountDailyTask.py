import re

from ok import Box, TaskDisabledException
from src.task.DailyTask import DailyTask, ADDITIONAL_TASKS
from src.task.WWOneTimeTask import WWOneTimeTask
from src.task.BaseCombatTask import BaseCombatTask
from src.task.BaseWWTask import LOGIN_TEXTS
from src.task.MouseResetTask import MouseResetTask

account_pattern = re.compile(r'\*\*\*\*')

# Keys in DailyTask that MultiAccountDailyTask can override per-account
DAILY_TASK_OVERRIDABLE_KEYS = [
    'Which to Farm',
    'Which Tacet Suppression to Farm',
    'Which Forgery Challenge to Farm',
    'Material Selection',
    'Farm Nightmare Nest for Daily Echo',
    ADDITIONAL_TASKS,
]

SKIP_ACCOUNTS = 'Skip Accounts'
ACCOUNT_CONFIGS = 'Account DailyTask Configs'


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
        self.default_config = {
            SKIP_ACCOUNTS: [],
            ACCOUNT_CONFIGS: [],
        }
        self.config_description = {
            SKIP_ACCOUNTS: (
                'Accounts to skip. Enter partial or full account names '
                '(e.g. aa****01@example.com). Matching is case-insensitive substring.'
            ),
            ACCOUNT_CONFIGS: (
                'Per-account DailyTask overrides. Each entry format:\n'
                '  account_keyword::key=value,key=value\n'
                'Example:\n'
                '  aa****01::Which to Farm=Forgery Challenge,Which Forgery Challenge to Farm=2\n'
                'Supported keys: Which to Farm / Which Tacet Suppression to Farm / '
                'Which Forgery Challenge to Farm / Material Selection / '
                'Farm Nightmare Nest for Daily Echo / '
                'Additional Tasks to Run After Daily Task'
            ),
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

    def _parse_account_config_entry(self, entry):
        """Parse 'account_keyword::key=value,key=value' -> (keyword, {key: value}).

        Booleans and integers are coerced automatically.
        """
        if '::' not in entry:
            return None, {}
        keyword, kv_part = entry.split('::', 1)
        keyword = keyword.strip()
        overrides = {}
        for pair in kv_part.split(','):
            pair = pair.strip()
            if '=' not in pair:
                continue
            k, v = pair.split('=', 1)
            k, v = k.strip(), v.strip()
            if v.lower() == 'true':
                v = True
            elif v.lower() == 'false':
                v = False
            else:
                try:
                    v = int(v)
                except ValueError:
                    pass
            overrides[k] = v
        return keyword, overrides

    def _get_account_overrides(self, account):
        """Return the first matching per-account config overrides dict, or {}."""
        normalized = normalize_account_name(account)
        if not normalized:
            return {}
        entries = self.config.get(ACCOUNT_CONFIGS) or []
        for entry in entries:
            keyword, overrides = self._parse_account_config_entry(entry)
            if keyword and normalize_account_name(keyword) in normalized:
                self.log_info(
                    self.tr('Applying account config override for {account}: {overrides}').format(
                        account=account, overrides=overrides
                    )
                )
                return overrides
        return {}

    def _apply_daily_overrides(self, daily_task, overrides):
        """Patch DailyTask config with per-account overrides; return saved originals."""
        originals = {}
        for key, value in overrides.items():
            if key in DAILY_TASK_OVERRIDABLE_KEYS:
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

        - Applies Account DailyTask Configs overrides before running.
        - Catches non-fatal exceptions so a single failure does not abort
          the whole multi-account run (fixes issue #7).
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
                    self.tr('ensure_main failed after DailyTask error for account {account}, _switch_to_login will attempt recovery').format(
                        account=account or '(current)'
                    ),
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
