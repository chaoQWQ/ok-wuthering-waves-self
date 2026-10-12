import time

from src.Labels import Labels
from src.char.BaseChar import BaseChar, SwitchPriority


class TeamRotation:
    ORDER = (Labels.char_verina, Labels.char_sanhua, Labels.char_qingxiao)
    LOOP_ORDER = (Labels.char_sanhua, Labels.char_verina, Labels.char_sanhua, Labels.char_qingxiao)
    FIELD_LIMIT = {Labels.char_verina: 20, Labels.char_sanhua: 18, Labels.char_qingxiao: 30}
    LIBERATION_CD = {Labels.char_verina: 25, Labels.char_sanhua: 16, Labels.char_qingxiao: 25}
    RESONANCE_CD = {Labels.char_verina: 12, Labels.char_sanhua: 10}

    def __init__(self):
        self.step = 0
        self.round = 0
        self.switch_pending = False

    @staticmethod
    def identity(character):
        if character.char_name in (Labels.char_sanhua, Labels.char_sanhua2):
            return Labels.char_sanhua
        return character.char_name

    @property
    def order(self):
        return self.LOOP_ORDER if self.round else self.ORDER

    @property
    def quick_sanhua(self):
        return self.round > 0 and self.step == 0

    @property
    def actor(self):
        return self.order[self.step]

    @property
    def target(self):
        if self.switch_pending and self.step == len(self.order) - 1:
            return self.LOOP_ORDER[0]
        return self.order[(self.step + int(self.switch_pending)) % len(self.order)]

    def priority(self, character):
        return SwitchPriority.MUST if self.identity(character) == self.target else SwitchPriority.NO

    def advance(self, target, has_intro):
        if self.identity(target) != self.target:
            raise RuntimeError(f'Team rotation target {target.char_name}, expected {self.target}')
        if not self.switch_pending:
            return
        if not has_intro and not self.quick_sanhua:
            raise RuntimeError(f'Team rotation requires intro for {target.char_name}')
        self.step = (self.step + 1) % len(self.order)
        self.round += int(self.step == 0)
        self.switch_pending = False

    def fail(self, character, description):
        character.task.screenshot('Qingxiao_Sanhua_Verina_failure')
        raise RuntimeError(f'Team rotation round={self.round} actor={character.char_name}: {description}')

    def wait(self, character, condition, timeout, description):
        start = time.time()
        while not condition():
            character.check_combat()
            if time.time() - start >= timeout:
                self.fail(character, description)
            character.sleep(0.05)
            character.task.next_frame()

    def deadline(self, character, start):
        limit = self.FIELD_LIMIT[self.identity(character)]
        character.check_combat()
        if character.time_elapsed_accounting_for_freeze(start) >= limit or time.time() - start >= limit + 25:
            self.fail(character, f'resource timeout, concerto={character.get_current_con():.3f}')

    def full(self, character):
        character.current_con = character.task.get_current_con()
        if character.current_con != 1:
            return False
        character.sleep(0.15)
        character.task.next_frame()
        character.current_con = character.task.get_current_con()
        return character.current_con == 1

    def normal(self, character, duration=0.45):
        character.continues_normal_attack(duration, until_con_full=True)
        character.task.next_frame()

    def resonance(self, character, enhanced=False):
        cooldown = self.RESONANCE_CD.get(self.identity(character), 0)
        if character.last_res >= 0 and character.time_elapsed_accounting_for_freeze(character.last_res) < cooldown:
            return False
        if not character.resonance_available():
            return False
        if not character.click_resonance(send_click=False, time_out=1.5,
                                         has_animation=enhanced, animation_min_duration=0.5,
                                         click_f=False)[0]:
            self.fail(character, 'resonance input failed')
        character.sleep(0.35)
        character.task.next_frame()
        self.wait(character, lambda: self.resonance_completed(character, enhanced), 1.5,
                  'resonance state unchanged')
        character.logger.info(f'Team rotation {character.char_name}: resonance confirmed')
        return True

    def resonance_completed(self, character, enhanced=False):
        if self.identity(character) == Labels.char_qingxiao and enhanced:
            return not character.enhanced_resonance_available()
        return not character.resonance_available()

    def liberation_ready(self, character):
        cooldown = self.LIBERATION_CD[self.identity(character)]
        if (character.last_liberation >= 0
                and character.time_elapsed_accounting_for_freeze(character.last_liberation) < cooldown):
            return False
        return character.task.use_liberation and character.liberation_available()

    def liberation(self, character):
        if not self.liberation_ready(character):
            return False
        if not character.click_liberation(wait_if_cd_ready=0, send_click=False, click_f=False):
            self.fail(character, 'liberation animation missing')
        character.task.next_frame()
        character.logger.info(f'Team rotation {character.char_name}: liberation confirmed')
        return True

    def echo(self, character, before_switch=False):
        if character.echo_available():
            if not character.click_echo(time_out=1.5):
                self.fail(character, 'echo state unchanged')
            if not before_switch:
                character.sleep(0.35)
            character.task.next_frame()
            self.wait(character, lambda: not character.echo_available(), 1.5, 'echo cooldown missing')

    def perform(self, character):
        if self.identity(character) != self.actor or self.switch_pending:
            BaseChar.switch_next_char(character, post_action=self.advance)
            return
        start = time.time()
        character.logger.info(f'Team rotation round={self.round}: {self.actor} start')
        if self.round and self.actor == Labels.char_verina:
            entry_start = time.time()
            while not character.is_mouse_forte_full() and not self.full(character):
                self.deadline(character, start)
                if time.time() - entry_start >= 2.5:
                    self.fail(character, 'Verina entry photosynthesis energy missing')
                self.normal(character, 0.25)
            character.logger.info(
                f'Team rotation Verina: entry resource confirmed after {time.time() - entry_start:.3f}s')
        elif self.actor == Labels.char_qingxiao:
            character.wait_down(click=True)
        else:
            character.wait_down(click=False)
        self.wait(character, lambda: character.task.in_team()[0], 3, 'team HUD unavailable')
        if self.actor == Labels.char_verina:
            self.verina(character, start)
        elif self.actor == Labels.char_sanhua:
            self.sanhua(character, start)
        else:
            self.qingxiao(character, start)
        if not self.quick_sanhua and not self.full(character):
            self.fail(character, 'concerto not full before switch')
        if not self.quick_sanhua:
            self.wait(character, lambda: character.task.in_team()[0], 3, 'team HUD unavailable before switch')
        self.switch_pending = True
        character.logger.info(f'Team rotation {self.actor}: switch to {self.target}, quick={self.quick_sanhua}')
        check_f_on_switch = character.check_f_on_switch
        if self.quick_sanhua:
            character.check_f_on_switch = False
        try:
            BaseChar.switch_next_char(character, post_action=self.advance)
        finally:
            character.check_f_on_switch = check_f_on_switch

    def verina(self, character, start):
        if self.round and self.full(character):
            return
        self.liberation(character)
        self.resonance(character)
        self.echo(character)
        while not self.full(character):
            self.deadline(character, start)
            self.liberation(character)
            if character.is_mouse_forte_full():
                before = character.get_current_con()
                character.task.mouse_down()
                try:
                    character.sleep(0.7)
                finally:
                    character.task.mouse_up()
                self.normal(character, 0.65)
                self.wait(character, lambda: character.get_current_con() > before + 0.03 or character.is_con_full(),
                          1.5, 'photosynthesis concerto missing')
            elif not self.resonance(character):
                self.normal(character)

    def sanhua(self, character, start):
        if self.round and not self.quick_sanhua:
            if not self.full(character):
                self.resonance(character)
            while not self.full(character):
                self.deadline(character, start)
                self.normal(character)
            self.echo(character, before_switch=True)
            return
        if self.quick_sanhua or not self.full(character):
            ice_created = self.resonance(character)
            character.task.next_frame()
            ready = self.liberation_ready(character)
            character.logger.info(
                f'Team rotation Sanhua: liberation after resonance ready={ready}, '
                f'elapsed={character.time_elapsed_accounting_for_freeze(character.last_liberation):.3f}, '
                f'cooldown={character.task.get_cd("liberation"):.3f}, '
                f'highlighted={character.task.box_highlighted("liberation")}')
            if not ready and character.task.use_liberation:
                character.task.screenshot('Sanhua_liberation_after_resonance')
            ice_created = self.liberation(character) or ice_created
            if ice_created:
                before = character.get_current_con()
                character.task.mouse_down()
                try:
                    character.sleep(0.85)
                finally:
                    character.task.mouse_up()
                if self.quick_sanhua:
                    character.logger.info('Team rotation Sanhua: heavy released, switch immediately')
                    return
                character.sleep(0.8)
                character.task.next_frame()
                self.wait(character, lambda: character.get_current_con() > before + 0.05 or character.is_con_full(),
                          1.5, 'ice burst concerto missing')
                character.logger.info('Team rotation Sanhua: combined ice burst concerto confirmed')
            elif self.quick_sanhua:
                self.fail(character, 'Sanhua ice skills unavailable before quick switch')
        while not self.full(character):
            self.deadline(character, start)
            self.normal(character)
        self.echo(character)

    def qingxiao(self, character, start):
        if not character.has_intro or character.check_outro() not in (Labels.char_sanhua, Labels.char_sanhua2):
            self.fail(character, 'Sanhua outro missing')
        while True:
            self.deadline(character, start)
            character.cycle_start()
            heavy = character.handle_heavy()
            if heavy:
                if heavy == Labels.qingxiao_h2:
                    break
                character.f_break()
            elif character.enhanced_resonance_available():
                self.resonance(character, enhanced=True)
            else:
                character.click()
            character.cycle_sleep()
        self.wait(character, lambda: character.task.in_team()[0], 3, 'second heavy animation unfinished')
        energy_start = time.time()
        while not self.liberation_ready(character) and time.time() - energy_start < 1.5:
            character.check_combat()
            character.sleep(0.05)
            character.task.next_frame()
        self.liberation(character)
        while not self.full(character):
            self.deadline(character, start)
            if not self.liberation(character):
                self.normal(character)
        self.echo(character, before_switch=True)


class Qingxiao(BaseChar):
    HEAVY_TIMEOUT = 4
    HEAVY_CONFIRM = 0.25

    def reset_state(self):
        super().reset_state()
        self._team_rotation = None

    def team_rotation(self):
        if len(self.task.chars) != 3 or {TeamRotation.identity(char) for char in self.task.chars if char} != set(
                TeamRotation.ORDER):
            raise RuntimeError('Qingxiao team rotation requires Qingxiao, Sanhua, Verina')
        rotation = getattr(self, '_team_rotation', None)
        if not isinstance(rotation, TeamRotation):
            self._team_rotation = TeamRotation()
            if rotation is not None:
                self._team_rotation.step = rotation.step
                self._team_rotation.round = rotation.round
                self._team_rotation.switch_pending = rotation.switch_pending
                if rotation.round and tuple(getattr(rotation, 'order', rotation.ORDER)) != self._team_rotation.order:
                    self._team_rotation.step = {
                        Labels.char_verina: 1,
                        Labels.char_sanhua: 2,
                        Labels.char_qingxiao: 3,
                    }[rotation.actor]
        return self._team_rotation

    def do_perform(self):
        self.team_rotation().perform(self)

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        return self.team_rotation().priority(self)

    def enhanced_resonance_available(self):
        return bool(self.task.find_one(Labels.qingxiao_e, threshold=0.7))

    def heavy_available(self):
        return self.task.find_one(Labels.qingxiao_h1, threshold=0.7) or self.task.find_one(
            Labels.qingxiao_h2, threshold=0.7)

    def handle_heavy(self):
        heavy = self.heavy_available()
        if not heavy:
            return False
        label = heavy.name
        start = time.time()
        dark_since = None
        confirmed = False
        self.task.mouse_down()
        try:
            while self.time_elapsed_accounting_for_freeze(start) < self.HEAVY_TIMEOUT and time.time() - start < 6:
                self.check_combat()
                if not self.task.in_team()[0] or self.task.find_one(label, threshold=0.7):
                    dark_since = None
                else:
                    if dark_since is None:
                        dark_since = time.time()
                    if time.time() - dark_since >= self.HEAVY_CONFIRM:
                        confirmed = True
                        break
                self.sleep(0.05)
                self.task.next_frame()
        finally:
            self.task.mouse_up()
        release_start = time.time()
        while not confirmed and time.time() - release_start < 1.5:
            self.check_combat()
            if self.task.in_team()[0] and not self.task.find_one(label, threshold=0.7):
                if dark_since is None:
                    dark_since = time.time()
                confirmed = time.time() - dark_since >= self.HEAVY_CONFIRM
            else:
                dark_since = None
            self.sleep(0.05)
            self.task.next_frame()
        if not confirmed:
            self.team_rotation().fail(self, f'{label} state unchanged')
        self.logger.info(f'Team rotation Qingxiao: {label} confirmed after {time.time() - start:.3f}s')
        return label
