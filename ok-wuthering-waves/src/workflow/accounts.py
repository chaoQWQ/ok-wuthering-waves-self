import re

from src.task.MultiAccountDailyTask import MultiAccountDailyTask, normalize_account_name
from src.workflow.input import WorkflowStopped


class AccountSessionController(MultiAccountDailyTask):
    def __init__(self, executor, app, profiles, store):
        super().__init__(executor=executor, app=app)
        self.profiles = profiles
        self.store = store
        self.processed = set()

    def _is_done(self, name):
        normalized = normalize_account_name(name)
        matches = [profile for profile in self.profiles
                   if normalize_account_name(profile.login_name) in normalized]
        if len(matches) > 1:
            raise WorkflowStopped('登录列表名称匹配到多个账号配置')
        return not matches or matches[0].identity in self.processed

    def _record_login_attempt(self, account):
        self.store.record_login(account, 'running')

    def open_next(self):
        if not (self._login_combo() or self.do_find_account_drop_down()):
            self._open_account_login_for_reselection()
        selected = self._select_and_login_account_once()
        if selected is None:
            return None
        matches = [profile for profile in self.profiles
                   if normalize_account_name(profile.login_name) in normalize_account_name(selected)]
        if len(matches) != 1:
            raise WorkflowStopped('所选账号没有唯一的配置')
        profile = matches[0]
        detected = self.identify()
        if detected != profile.profile_code:
            self.store.record_login(selected, 'identity_mismatch')
            raise WorkflowStopped('游戏内完整账号标识与所选账号不符')
        self.store.record_login(selected, 'confirmed')
        return profile

    def identify(self):
        if not self.in_team_and_world():
            raise WorkflowStopped('确认账号身份时游戏没有处于世界界面')
        self.send_key('esc', after_sleep=1)
        if not self.wait_feature('esc_setting', time_out=10, raise_if_not_found=True):
            raise WorkflowStopped('无法打开账号资料页面')
        codes = set()
        for box in self.ocr(.18, .32, .38, .45):
            codes.update(re.findall(r'(?<!\d)\d{7,16}(?!\d)', box.name))
        if len(codes) != 1:
            raise WorkflowStopped('账号资料页面没有唯一的完整账号标识')
        self.send_key('esc', after_sleep=.5)
        if not self.wait_in_team_and_world(time_out=10, raise_if_not_found=True):
            raise WorkflowStopped('账号资料页面未能关闭')
        return codes.pop()

    def finish(self, profile):
        self.processed.add(profile.identity)
        self._return_to_account_list()
