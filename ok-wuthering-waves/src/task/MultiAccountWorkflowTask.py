import json
import re
import time
import hashlib
from pathlib import Path

from src.task.GameplayLearningTask import GameplayLearningTask
from src.task.MultiAccountDailyTask import MultiAccountDailyTask
from src.task.QuestStoryTask import QuestStoryTask
from src.task.BaseWWTask import BaseWWTask
from src.utils.QuestVision import detect_dialog_advance_indicator, detect_letterbox
from src.workflow.accounts import AccountSessionController
from src.workflow.combat import CombatSceneChanged, CombatSession
from src.workflow.input import WorkflowStopped, coordinated_task
from src.workflow.models import Account
from src.workflow.navigation import RouteFollower
from src.workflow.observer import SceneObserver, protect_observation_frame
from src.workflow.runner import WorkflowRunner
from src.workflow.validator import WorkflowValidator


class MultiAccountWorkflowTask(QuestStoryTask):
    owns_switch_healer_config = False
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = 'Learned Quest and Event Workflows'
        self.description = 'Run learned workflows with verified account progress and ok-ww combat'
        self.default_config = {
            'Workflow revisions': [],
            'Account profiles': '[]',
            'Camera Sensitivity': 1.0,
            'Auto Skip Dialog': True,
        }
        self.config_description = {
            'Workflow revisions': 'Workflow versions selected from the learning page.',
            'Account profiles': 'Account identities imported from Multi Account Daily Task. Empty runs the current account.',
        }
        self.config_type = {'Account profiles': {'type': 'text_edit'}}
        self.support_schedule_task = True
        self._observer = None
        self._coordinator = None
        self._workflow_observation = None
        self._guard_at = 0.0
        self._observing = False
        self._combat_session = False
        self._combat_deadline = None
        self._current_step = None
        self._workflow_frame = None
        self.sleep_check_interval = .1

    def _get_observer(self):
        if self._observer is None:
            configuration = self.executor.config
            self._observer = SceneObserver(Path(__file__).resolve().parents[2],
                                           configuration['template_matching'],
                                           dict(configuration['ocr'].get('default', configuration['ocr']).get('params', {})))
        return self._observer

    def observe_workflow(self):
        if self._observing:
            raise RuntimeError('流程观察出现重复进入')
        self._observing = True
        try:
            frame = self.next_frame()
            if frame is None:
                raise WorkflowStopped('无法取得新的游戏画面')
            observation = self._get_observer().observe(frame, time.monotonic(), self.is_game_window_active())
            self._workflow_frame = frame.copy()
            self._workflow_observation = observation
            return observation
        finally:
            self._observing = False

    def check_workflow_input(self):
        if self._coordinator is None:
            raise WorkflowStopped('流程输入控制尚未初始化')
        if self._coordinator.owner == 'account':
            return
        if self._observing:
            return
        if time.monotonic() - self._guard_at < .15:
            return
        self._guard_at = time.monotonic()
        frame = self.next_frame()
        if frame is None:
            raise WorkflowStopped('输入前无法确认游戏画面')
        if self._combat_session:
            if time.monotonic() >= self._combat_deadline:
                raise WorkflowStopped('战斗没有在规定时间内完成')
            if detect_dialog_advance_indicator(frame).found or detect_letterbox(frame).is_letterbox:
                raise CombatSceneChanged()
        if self._workflow_observation is None or time.monotonic() - self._workflow_observation.timestamp > 2:
            observation = self.observe_workflow()
            self._coordinator.observe(observation)
            if observation.scene in ('unknown', 'login'):
                raise WorkflowStopped('输入前无法确认当前场景')
            if self._combat_session and (observation.scene in ('dialog', 'cutscene', 'result')
                                         or self._current_step.completion.matches(observation)):
                raise CombatSceneChanged()

    def sleep_check(self):
        if self._coordinator is not None and self._coordinator.owner is not None and not self._observing:
            self._coordinator.check()
        if self._combat_session:
            BaseWWTask.sleep_check(self)

    def execute_workflow_step(self, step, observation, performed, deadline):
        self._current_step = step
        if step.action == 'navigate':
            if step.preconditions.objective and step.preconditions.objective != observation.objective:
                raise WorkflowStopped('导航目标已经变化，停止当前路线')
            self._route.advance(step, observation)
        elif step.action in ('interact', 'mechanism'):
            if not performed:
                if observation.interaction != step.target:
                    raise WorkflowStopped('当前交互对象与录制目标不符')
                if step.action == 'mechanism' and not re.search(r'启动|开启|激活|调查|放置|拾取|旋转|activate|open|place|pick up|rotate', step.target, re.I):
                    raise WorkflowStopped('当前机关没有受支持的明确操作')
                self.send_key('f', down_time=.1)
        elif step.action in ('select', 'claim'):
            if not performed:
                if re.search(r'购买|充值|唤取|兑换|分解|丢弃|purchase|buy|exchange|discard|delete', step.target, re.I):
                    raise WorkflowStopped('当前按钮不属于允许的剧情或活动操作')
                controls = [control for control in observation.controls if control.text == step.target and control.confidence >= .8]
                if len(controls) != 1:
                    raise WorkflowStopped('当前按钮无法唯一确定')
                self.click(controls[0].x, controls[0].y)
        elif step.action == 'dialog':
            self._advance_workflow_dialog(observation)
        elif step.action == 'combat':
            result = CombatSession(self).run(observation, deadline)
            self.info_set('Combat state', result)
        elif step.action != 'wait':
            raise ValueError('未知的流程动作')

    def _advance_workflow_dialog(self, observation):
        if observation.scene == 'dialog':
            completion = self._current_step.completion
            may_skip = completion.scene in ('world', 'combat', 'result') or (
                completion.objective and completion.objective != observation.objective)
            if may_skip and self.config.get('Auto Skip Dialog') and self.check_skip():
                return
            if detect_dialog_advance_indicator(self.frame).found:
                self.click(.5, .8)

    def import_daily_accounts(self):
        daily = self.get_task_by_class(MultiAccountDailyTask)
        profiles = []
        for number in range(1, 6):
            if not daily.config.get(f'Account {number} Daily Task Override'):
                continue
            profiles.append(Account(login_name=daily.config.get(f'Account {number} Keyword').strip(),
                                    region='current', profile_code=daily.config.get(f'Account {number} In-game Profile Code').strip()))
        self.config['Account profiles'] = json.dumps([item.model_dump() for item in profiles], ensure_ascii=False, indent=2)
        return len(profiles)

    def run(self):
        learning = self.get_task_by_class(GameplayLearningTask)
        if learning is None:
            raise RuntimeError('自动学习任务没有注册')
        store = learning.get_store()
        revisions = self.config.get('Workflow revisions')
        if not revisions:
            raise ValueError('请在剧情与活动学习页面选择需要执行的内容')
        workflows = [store.workflow(revision) for revision in revisions]
        game_version = learning._version(self.hwnd)
        for workflow in workflows:
            if workflow.game_version != game_version or workflow.language != str(self.executor.locale):
                raise ValueError('流程版本或语言与当前游戏环境不符，需要重新学习对应内容')
            WorkflowValidator(store).check(workflow)
        profiles = [Account.model_validate(item) for item in json.loads(self.config.get('Account profiles'))]
        if len({profile.identity for profile in profiles}) != len(profiles):
            raise ValueError('账号配置存在重复身份')
        session = AccountSessionController(self.executor, self._app, profiles, store)
        session.config = self.get_task_by_class(MultiAccountDailyTask).config
        with coordinated_task(self) as coordinator:
            self._coordinator = coordinator
            try:
                with coordinator.control('account'):
                    self.ensure_in_front()
                    if not self.is_game_window_active():
                        raise WorkflowStopped('Windows 未能激活游戏窗口，无法开始执行')
                if profiles:
                    while True:
                        with coordinator.control('account'):
                            profile = session.open_next()
                        if profile is None:
                            break
                        store.save_account(profile)
                        self._execute_workflows(store, profile.identity, workflows)
                        with coordinator.control('account'):
                            session.finish(profile)
                else:
                    with coordinator.control('account'):
                        code = session.identify()
                    profile = Account(login_name='current', region='current', profile_code=code)
                    store.save_account(profile)
                    self._execute_workflows(store, profile.identity, workflows)
            finally:
                self._coordinator = None

    def _execute_workflows(self, store, account, workflows):
        self.chars = [None, None, None]
        self._in_combat = False
        self.scene.reset()
        for workflow in workflows:
            if workflow.quest_title:
                current = self.observe_workflow()
                known = any(step.preconditions.matches(current) or step.completion.matches(current) for step in workflow.steps)
                if not known:
                    with self._coordinator.control('quest_selection'):
                        self._select_workflow_quest(workflow.quest_title)
            self._route = RouteFollower(self, store, workflow)
            WorkflowRunner(store, self, self._coordinator).run(account, workflow)

    def _select_workflow_quest(self, title):
        self.send_key('j', after_sleep=.5)
        def find_title():
            boxes = [box for box in self.ocr(match=re.compile('^' + re.escape(title) + '$')) if box.confidence >= .8]
            if len(boxes) > 1:
                raise WorkflowStopped('任务列表中存在多个同名目标')
            return boxes[0] if boxes else None
        target = self.wait_until(find_title, time_out=8, raise_if_not_found=True)
        self.click_box(target, after_sleep=.3)
        self.next_frame()
        buttons = self.ocr(match=re.compile(r'^(?:追踪|追踪任务|Track|Track Quest)$', re.I))
        cancel = self.ocr(match=re.compile(r'^(?:取消追踪|Stop Tracking|Untrack)$', re.I))
        if len(buttons) == 1:
            self.click_box(buttons[0], after_sleep=.3)
        elif len(cancel) != 1:
            raise WorkflowStopped('当前任务无法确认追踪状态')
        self.send_key('esc', after_sleep=.3)
        self.wait_in_team_and_world(time_out=10, raise_if_not_found=True)

    def save_workflow_evidence(self, store):
        observation = self._workflow_observation
        if observation is None or self._workflow_frame is None:
            return None
        import cv2
        directory = store.root / 'execution'
        directory.mkdir(exist_ok=True)
        relative = f'execution/{observation.frame_id}.png'
        success, encoded = cv2.imencode('.png', protect_observation_frame(self._workflow_frame, observation))
        if not success:
            raise RuntimeError('执行结果画面编码失败')
        data = encoded.tobytes()
        store.resolve(relative).write_bytes(data)
        return observation.model_copy(update={'screenshot': relative, 'image_sha256': hashlib.sha256(data).hexdigest()})
