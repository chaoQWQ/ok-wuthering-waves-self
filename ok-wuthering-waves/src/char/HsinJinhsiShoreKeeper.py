import time
from dataclasses import dataclass
from enum import Enum, auto

from src.Labels import Labels
from src.char.BaseChar import BaseChar, SwitchPriority


class Action(Enum):
    A1 = auto()
    A2 = auto()
    A3 = auto()
    A4 = auto()
    E = auto()
    E2 = auto()
    E3 = auto()
    E4 = auto()
    Z = auto()
    Z1 = auto()
    Z2 = auto()
    R = auto()
    R1 = auto()
    R2 = auto()
    DODGE = auto()
    JUMP = auto()
    EXECUTE = auto()


@dataclass(frozen=True)
class RotationStep:
    character: str
    actions: tuple[Action, ...]
    intro: bool = False


A = Action
HSIN = Labels.char_hsin
JINHSI = Labels.char_jinhsi
SHOREKEEPER = Labels.char_shorekeeper

OPENING = (
    RotationStep(SHOREKEEPER, (A.E,)),
    RotationStep(JINHSI, (A.A2, A.A3, A.A4, A.E2, A.R, A.A1, A.A2, A.A3, A.E3)),
    RotationStep(SHOREKEEPER, (A.A1, A.A2, A.A3, A.JUMP, A.A1)),
    RotationStep(JINHSI, (A.A4, A.E4, A.Z)),
    RotationStep(HSIN, (A.A3, A.A4, A.Z1, A.R1, A.A1, A.E), intro=True),
    RotationStep(SHOREKEEPER, (A.Z, A.R), intro=True),
    RotationStep(JINHSI, (A.E2, A.DODGE, A.A1, A.A2, A.E3), intro=True),
    RotationStep(HSIN, (A.A1, A.A2, A.DODGE, A.A1, A.A2, A.A3, A.Z2, A.R2, A.E), intro=True),
)

CYCLE = (
    RotationStep(JINHSI, (A.A3, A.EXECUTE, A.A4, A.E4, A.Z)),
    RotationStep(HSIN, (A.A4, A.A1)),
    RotationStep(SHOREKEEPER, (A.A1, A.A2, A.E)),
    RotationStep(JINHSI, (A.A1, A.A2, A.E2)),
    RotationStep(HSIN, (A.A1, A.A2, A.A3)),
    RotationStep(SHOREKEEPER, (A.A1,)),
    RotationStep(JINHSI, (A.A1, A.A2, A.E3)),
    RotationStep(HSIN, (A.A4,)),
    RotationStep(SHOREKEEPER, (A.A1, A.A2, A.A3, A.Z)),
    RotationStep(JINHSI, (A.A3, A.R, A.A4, A.E4, A.Z)),
    RotationStep(HSIN, (A.Z1, A.R1, A.A1, A.E)),
    RotationStep(SHOREKEEPER, (A.R,), intro=True),
    RotationStep(JINHSI, (A.E2, A.DODGE, A.A1, A.A2, A.E3), intro=True),
    RotationStep(HSIN, (A.A1, A.A2, A.DODGE, A.A1, A.A2, A.A3, A.Z2, A.R2, A.E), intro=True),
)

# 每段普攻的输入窗口；第四段回路另行通过图像确认。
NORMAL_WINDOWS = {
    HSIN: {A.A1: 0.25, A.A2: 0.22, A.A3: 0.65, A.A4: 0.7},
    JINHSI: {A.A1: 0.3, A.A2: 0.35, A.A3: 0.45, A.A4: 0.65},
    SHOREKEEPER: {A.A1: 0.3, A.A2: 0.35, A.A3: 0.55},
}


def get_team_rotation(character):
    chars = getattr(character.task, 'chars', ())
    if len(chars) != 3 or {char.char_name for char in chars if char} != {HSIN, JINHSI, SHOREKEEPER}:
        return None
    hsin = next(char for char in chars if char.char_name == HSIN)
    rotation = getattr(hsin, '_jinhsi_team_rotation', None)
    if rotation is None:
        rotation = HsinJinhsiShoreKeeperRotation()
        hsin._jinhsi_team_rotation = rotation
    return rotation


class HsinJinhsiShoreKeeperRotation:
    def __init__(self):
        self.opening = True
        self.step_index = 0
        self.action_index = 0
        self.switch_pending = False

    @property
    def steps(self):
        return OPENING if self.opening else CYCLE

    @property
    def step(self):
        return self.steps[self.step_index]

    @property
    def next_step(self):
        if self.step_index + 1 < len(self.steps):
            return self.steps[self.step_index + 1]
        return CYCLE[0]

    @property
    def target(self):
        return self.next_step.character if self.switch_pending else self.step.character

    def priority(self, character):
        return SwitchPriority.MUST if character.char_name == self.target else SwitchPriority.NO

    def advance(self, switch_to, has_intro):
        if switch_to.char_name != self.target:
            raise RuntimeError(f'Unexpected rotation target: {switch_to.char_name}, expected {self.target}')
        if not self.switch_pending:
            return
        if self.next_step.intro and not has_intro:
            raise RuntimeError(f'Rotation intro missing: {switch_to.char_name}')
        self.step_index += 1
        if self.step_index == len(self.steps):
            self.opening = False
            self.step_index = 0
        self.action_index = 0
        self.switch_pending = False

    def perform(self, character):
        if character.char_name != self.step.character or self.switch_pending:
            self.switch(character)
            return
        character.logger.info(
            f'Team rotation: opening={self.opening}, step={self.step_index}, actions={self.step.actions}')
        if self.action_index == 0:
            if self.step.intro and not character.has_intro:
                raise RuntimeError(f'Rotation intro missing: {character.char_name}')
            if character.has_intro:
                self.wait(character, character.task.in_team_and_world, 4, 'intro animation', check_combat=False)
        while self.action_index < len(self.step.actions):
            action = self.step.actions[self.action_index]
            self.perform_action(character, action)
            self.action_index += 1
        if self.next_step.intro:
            self.wait(character, character.is_con_full, 2, 'concerto')
        self.switch_pending = True
        self.switch(character)

    def switch(self, character):
        # 复用协奏检查、切换确认和变奏状态记录，阶段在确认切换后推进。
        if character.char_name == SHOREKEEPER and character.is_con_full():
            character.outrotime = time.time()
            character.dodge_count = 5
        BaseChar.switch_next_char(character, post_action=self.advance)

    def wait(self, character, condition, timeout, description, check_combat=True, post_action=None):
        start = time.time()
        while not condition():
            if time.time() - start >= timeout:
                raise RuntimeError(f'{character.char_name}: timed out waiting for {description}')
            if check_combat:
                character.check_combat()
            if post_action:
                post_action()
            character.sleep(0.05, check_combat=check_combat)
            character.task.next_frame()

    def normal(self, character, action):
        start = time.time()
        duration = NORMAL_WINDOWS[character.char_name][action]
        while character.time_elapsed_accounting_for_freeze(start) < duration:
            character.click()
            character.sleep(0.08)
            character.task.next_frame()
        if character.char_name == HSIN and action == A.A4:
            self.wait(character, character.heavy_available, 2, 'fourth normal attack forte',
                      post_action=character.click_with_interval)

    def resonance(self, character, action):
        self.wait(character, character.resonance_available, 3, action.name)
        # 每个阶段发送一次技能，保留下一段强化技能给指定的入场阶段。
        character.send_resonance_key()
        character.record_resonance_use()
        character.sleep(0.15, check_combat=False)
        if character.char_name == JINHSI and action == A.E4:
            self.wait(character, lambda: not character.task.in_team()[0], 1, 'resonance animation',
                      check_combat=False)
            start = time.time()
            character.task.in_liberation = True
            try:
                self.wait(character, lambda: character.task.in_team()[0], 7, 'resonance animation end',
                          check_combat=False)
            finally:
                character.task.in_liberation = False
            character.add_freeze_duration(start, time.time() - start)
        else:
            self.wait(character, lambda: not character.resonance_available(), 2, 'resonance consumption')

    def heavy(self, character, action):
        if character.char_name == HSIN:
            label = Labels.hsin_h1 if action == A.Z1 else Labels.hsin_h2
            if action == A.Z:
                raise ValueError('Hsin heavy attack requires Z1 or Z2')
            self.wait(character, lambda: bool(character.task.find_one(label, threshold=0.7)), 2, action.name)
            if not character.heavy_wait_highlight_down(1.2):
                raise RuntimeError(f'Hsin: heavy attack not consumed: {action.name}')
        else:
            character.check_combat()
            character.task.mouse_down()
            try:
                character.sleep(0.6)
            finally:
                character.task.mouse_up()
            character.sleep(0.05)

    def liberation(self, character, action):
        if character.char_name == HSIN:
            label = Labels.hsin_lib1 if action == A.R1 else Labels.hsin_lib2
            self.wait(character, lambda: bool(character.task.find_one(label, threshold=0.7)), 2, action.name)
        self.wait(character, character.liberation_available, 3, action.name)
        if not character.click_liberation(wait_if_cd_ready=0, send_click=False, click_f=False):
            raise RuntimeError(f'{character.char_name}: liberation failed: {action.name}')
        if character.char_name == HSIN:
            character.lib2_cast_this_turn = action == A.R2

    def perform_action(self, character, action):
        if action in (A.A1, A.A2, A.A3, A.A4):
            self.normal(character, action)
        elif action in (A.E, A.E2, A.E3, A.E4):
            self.resonance(character, action)
        elif action in (A.Z, A.Z1, A.Z2):
            self.heavy(character, action)
        elif action in (A.R, A.R1, A.R2):
            self.liberation(character, action)
        elif action == A.DODGE:
            character.click(key='right')
            character.sleep(0.15)
        elif action == A.JUMP:
            character.task.jump()
            character.sleep(0.15)
        elif action == A.EXECUTE:
            character.f_break()
        else:
            raise ValueError(f'Unsupported rotation action: {action}')
