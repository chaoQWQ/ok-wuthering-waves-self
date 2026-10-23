import hashlib
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from src.utils.DailyAccountState import DailyAccountState


Scene = Literal['world', 'dialog', 'cutscene', 'combat', 'menu', 'result', 'loading', 'login', 'unknown']
Action = Literal['navigate', 'interact', 'select', 'wait', 'dialog', 'combat', 'mechanism', 'claim']


def cycle_period(cycle, period, now=None):
    if cycle == 'once':
        return 'once'
    if cycle == 'event':
        return f'event:{period}'
    day = DailyAccountState.day(now)
    if cycle == 'daily':
        return f'daily:{day}'
    if cycle == 'weekly':
        current = date.fromisoformat(day)
        return 'weekly:' + (current - timedelta(days=current.weekday())).isoformat()
    raise ValueError('未知的内容周期')


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, validate_assignment=True)


class Control(Record):
    text: str
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)


class Observation(Record):
    frame_id: str
    timestamp: float = Field(ge=0)
    scene: Scene
    objective: str = ''
    texts: list[str] = Field(default_factory=list)
    controls: list[Control] = Field(default_factory=list)
    interaction: str = ''
    combat: bool = False
    timer_visible: bool = False
    foreground: bool = False
    changed: bool = True
    screenshot: str = ''
    image_sha256: str = ''
    gap: bool = False
    redactions: list[list[float]] = Field(default_factory=list)


class Condition(Record):
    scene: Scene | None = None
    objective: str = ''
    texts: list[str] = Field(default_factory=list)
    interaction: str = ''

    @model_validator(mode='after')
    def require_identity(self):
        if not (self.objective or self.texts or self.interaction):
            raise ValueError('步骤条件必须包含可观察的目标或文字身份')
        if self.scene in ('unknown', 'login'):
            raise ValueError('未知场景和登录页面不能作为流程步骤条件')
        return self

    def matches(self, observation):
        return bool(
            not observation.gap and observation.scene not in ('unknown', 'login')
            and (self.scene is None or self.scene == observation.scene)
            and (not self.objective or self.objective == observation.objective)
            and (not self.interaction or self.interaction == observation.interaction)
            and all(text in observation.texts for text in self.texts)
        )


class Step(Record):
    step_id: str = Field(pattern=r'^[a-z][a-z0-9_]{0,63}$')
    action: Action
    preconditions: Condition
    completion: Condition
    target: str = ''
    next_step: str | None = None
    timeout_seconds: float = Field(default=300.0, gt=0, le=3600)
    evidence_before: str
    evidence_after: str
    route_frames: list[str] = Field(default_factory=list)
    resume_policy: Literal['observe_before_action'] = 'observe_before_action'

    @model_validator(mode='after')
    def check_action(self):
        if self.preconditions == self.completion:
            raise ValueError('步骤完成条件必须能够区分执行前后的状态')
        if self.action in ('interact', 'select', 'claim', 'mechanism') and not self.target:
            raise ValueError('交互步骤必须指定当前画面中的目标文字')
        if self.evidence_before == self.evidence_after:
            raise ValueError('执行前后必须具有不同的证据')
        return self


class Workflow(Record):
    schema_version: Literal[1] = 1
    title: str = Field(min_length=2, max_length=160)
    content_id: str = Field(pattern=r'^[a-z0-9_][a-z0-9_.-]{0,95}$')
    period: str = Field(min_length=1, max_length=80)
    cycle: Literal['once', 'daily', 'weekly', 'event'] = 'once'
    language: str = 'zh_CN'
    game_version: str
    recording_id: str
    quest_title: str = ''
    steps: list[Step] = Field(min_length=1, max_length=256)

    @model_validator(mode='after')
    def check_graph(self):
        if self.cycle == 'once' and self.period != 'once':
            raise ValueError('一次性内容的周期标识必须为 once')
        if self.cycle == 'event' and self.period == 'once':
            raise ValueError('活动内容必须具有明确的期次标识')
        ids = [step.step_id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError('步骤标识不能重复')
        for index, step in enumerate(self.steps):
            expected = ids[index + 1] if index + 1 < len(ids) else None
            if step.next_step != expected:
                raise ValueError('观察流程必须按证据顺序连接全部步骤')
        return self

    @property
    def revision(self):
        return hashlib.sha256(self.model_dump_json().encode('utf-8')).hexdigest()

    def execution_period(self, now=None):
        return cycle_period(self.cycle, self.period, now)


class Account(Record):
    login_name: str = Field(min_length=1)
    region: str = Field(min_length=1)
    profile_code: str = Field(pattern=r'^\d{7,16}$')

    @property
    def identity(self):
        return hashlib.sha256(f'{self.region}:{self.profile_code}'.encode()).hexdigest()


class CompilerResponse(Record):
    recording_name: str = Field(min_length=2, max_length=160)
    workflows: list[Workflow]
    unsupported_reasons: list[str]
    no_action_needed: bool = False
