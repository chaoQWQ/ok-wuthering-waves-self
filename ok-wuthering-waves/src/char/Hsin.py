import time

from src.Labels import Labels
from src.char.BaseChar import BaseChar
from src.char.HsinJinhsiShoreKeeper import get_team_rotation


class Hsin(BaseChar):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lib2_cast_this_turn = False
        self._jinhsi_team_rotation = None
        self.finisher_start: float | None = None
        self.finisher_e_sent = False
        self.h2_completed = False
        self.h2_guard_until = 0.0

    def reset_state(self):
        super().reset_state()
        self.lib2_cast_this_turn = False
        self._jinhsi_team_rotation = None
        self.finisher_start = None
        self.finisher_e_sent = False
        self.h2_completed = False
        self.h2_guard_until = 0.0

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        rotation = get_team_rotation(self)
        if rotation is not None:
            return rotation.priority(self)
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    def skip_combat_check(self):
        if self.h2_guard_until and time.time() >= self.h2_guard_until:
            self.logger.info('Hsin H2 protection expired; resume combat checks')
            self.h2_guard_until = 0.0
        return self.h2_guard_until > 0

    def do_perform(self):
        rotation = get_team_rotation(self)
        if rotation is not None:
            return rotation.perform(self)
        self.lib2_cast_this_turn = False
        self.finisher_start = None
        self.finisher_e_sent = False
        self.h2_completed = False
        self.h2_guard_until = 0.0
        self.logger.debug(f'Hsin perform start: has_intro={self.has_intro}')
        try:
            self.perform_everything()
        finally:
            self.h2_guard_until = 0.0
        self.logger.debug(f'Hsin switch_next_char start: lib2_cast={self.lib2_cast_this_turn}')
        self.switch_next_char()
        self.logger.debug('Hsin switch_next_char end')

    def perform_everything(self):
        start = time.time()
        duration = 12 if self.has_intro else 6
        last_e_ready = None
        self.logger.debug(f'Hsin rotation start: duration={duration}')
        while True:
            phase_start = self.finisher_start if self.finisher_start is not None else start
            phase_duration = 18 if self.finisher_start is not None else duration
            if self.time_elapsed_accounting_for_freeze(phase_start) >= phase_duration:
                self.logger.debug(f'Hsin rotation timeout: finisher={self.finisher_start is not None}, '
                                  f'enhanced_e={self.finisher_e_sent}, h2={self.h2_completed}')
                if not self.h2_completed:
                    self.logger.debug('Hsin heavy end: duration reached')
                self.logger.debug('Hsin rotation end: duration reached')
                self.logger.info(f'Hsin rotation end: timeout, finisher={self.finisher_start is not None}, '
                                 f'enhanced_e={self.finisher_e_sent}, h2={self.h2_completed}')
                return
            self.cycle_start()
            self.logger.debug(
                f'Hsin cycle: elapsed={self.time_elapsed_accounting_for_freeze(phase_start):.2f}, '
                f'wall_elapsed={time.time() - start:.2f}, finisher={self.finisher_start is not None}, '
                f'duration={phase_duration}')
            # H2 完成后继续等待 R2。
            if self.h2_completed:
                if self.lib(after_heavy=True):
                    self.logger.debug('Hsin rotation end: lib2 cast')
                    return
                self.logger.debug('Hsin cycle_sleep start')
                self.cycle_sleep()
                self.logger.debug('Hsin cycle_sleep end')
                continue
            if self.task.find_one(Labels.hsin_lib2, threshold=0.7):
                self.lib()
                if self.lib2_cast_this_turn:
                    self.logger.debug('Hsin rotation end: lib2 cast')
                    return
            heavy_type = self.heavy_available()
            if heavy_type == 2:
                remaining = phase_duration - self.time_elapsed_accounting_for_freeze(phase_start)
                confirmed = self.handle_heavy(heavy_type)
                self.logger.debug(f'Hsin heavy end: remaining_before_hold={remaining:.2f}')
                if confirmed and self.wait_for_lib2(after_heavy=True):
                    self.logger.debug('Hsin rotation end: lib2 cast')
                    return
                self.logger.debug('Hsin cycle_sleep start')
                self.cycle_sleep()
                self.logger.debug('Hsin cycle_sleep end')
                continue
            # 根据当前强化 E 提示重试技能。
            if self.finisher_start is not None:
                ready = self.enhanced_e_ready()
                if ready != last_e_ready:
                    self.logger.info(f'Hsin enhanced E prompt: ready={ready}, '
                                     f'previously_confirmed={self.finisher_e_sent}')
                    last_e_ready = ready
                if ready:
                    if self.finisher_e_sent:
                        self.logger.info('Hsin enhanced E prompt returned; retry E')
                    self.finisher_e_sent = False
                    self.cast_enhanced_e()
                else:
                    self.logger.debug(f'Hsin enhanced E skipped: consumed={self.finisher_e_sent}')
                    self.logger.debug('Hsin normal attack start')
                    self.click()
                    self.logger.debug('Hsin normal attack end')
                self.logger.debug('Hsin cycle_sleep start')
                self.cycle_sleep()
                self.logger.debug('Hsin cycle_sleep end')
                continue
            self.logger.debug('Hsin resonance start')
            resonance_start = time.time()
            if self.resonance_available():
                resonance = self.click_resonance(has_animation=False, send_click=True, time_out=0)
            else:
                resonance = (False, 0, False)
                self.logger.debug('Hsin resonance skipped: unavailable')
            self.logger.debug(f'Hsin resonance check/cast duration={time.time() - resonance_start:.3f}s')
            self.logger.debug(f'Hsin resonance end: result={resonance}')
            if resonance[0]:
                pass
            elif self.lib():
                if self.lib2_cast_this_turn:
                    self.logger.debug('Hsin rotation end: lib2 cast')
                    return
            elif heavy_type:
                remaining = phase_duration - self.time_elapsed_accounting_for_freeze(phase_start)
                self.handle_heavy(heavy_type)
                self.logger.debug(f'Hsin heavy end: remaining_before_hold={remaining:.2f}')
            elif self.finisher_start is not None:
                self.logger.debug('Hsin normal attack start')
                self.click()
                self.logger.debug('Hsin normal attack end')
            else:
                self.logger.debug('Hsin echo start')
                echoed = self.click_echo(time_out=0)
                self.logger.debug(f'Hsin echo end: cast={echoed}')
                if not echoed:
                    self.logger.debug('Hsin normal attack start')
                    self.click()
                    self.logger.debug('Hsin normal attack end')
            self.logger.debug('Hsin cycle_sleep start')
            self.cycle_sleep()
            self.logger.debug('Hsin cycle_sleep end')

    def enhanced_e_ready(self) -> bool:
        return bool(self.is_e_forte_full())

    def cast_enhanced_e(self) -> bool:
        self.logger.debug('Hsin enhanced E start')
        self.logger.info('Hsin enhanced E start: generic prompt ready')
        start = time.time()
        last_send = -1.0
        sends = 0
        gone_since = None
        while time.time() - start < 1.5:
            self.check_combat()
            now = time.time()
            if self.enhanced_e_ready():
                gone_since = None
                if now - last_send >= 0.25:
                    self.send_resonance_key()
                    last_send = now
                    sends += 1
                    self.logger.info(f'Hsin enhanced E key: send={sends}, elapsed={now - start:.3f}s')
            elif last_send >= 0:
                if gone_since is None:
                    gone_since = now
                elif now - gone_since >= 0.25:
                    self.finisher_e_sent = True
                    self.record_resonance_use()
                    self.logger.debug('Hsin enhanced E consumed; continue normal attacks')
                    self.logger.info(f'Hsin enhanced E result: confirmed=True, sends={sends}, '
                                     f'elapsed={time.time() - start:.3f}s')
                    return True
            self.task.next_frame()
        self.logger.debug('Hsin enhanced E not confirmed; retry when ready')
        self.logger.info(f'Hsin enhanced E result: confirmed=False, sends={sends}, '
                         f'elapsed={time.time() - start:.3f}s')
        return False

    def lib(self, after_heavy: bool = False) -> bool:
        self.logger.debug('Hsin liberation start')
        # H2 完成后允许使用通用 R 图标，技能助手继续确认动画。
        is_lib2 = after_heavy or bool(self.task.find_one(Labels.hsin_lib2, threshold=0.7))
        has_feature = is_lib2 or bool(self.task.find_one(Labels.hsin_lib1, threshold=0.7))
        available = self.liberation_available() if has_feature else None
        self.logger.debug(f'Hsin liberation indicator: feature={has_feature}, lib2={is_lib2}, available={available}')
        if not has_feature or not available:
            self.logger.debug('Hsin liberation end: feature missing or unavailable')
            return False
        liberated = bool(self.click_liberation(wait_if_cd_ready=0))
        self.logger.debug(f'Hsin liberation: lib2={is_lib2}, cast={liberated}')
        if liberated:
            if is_lib2:
                self.lib2_cast_this_turn = True
                self.h2_guard_until = 0.0
            elif self.finisher_start is None:
                # R1 动画结束后开始计算后续动作时限。
                self.finisher_start = time.time()
                self.logger.debug('Hsin finisher start: duration=18')
                self.logger.info('Hsin finisher start: R1 confirmed, duration=18')
        self.logger.debug(f'Hsin liberation end: cast={liberated}, lib2={is_lib2}')
        return liberated

    def heavy_available(self) -> int:
        if self.task.find_one(Labels.hsin_h2, threshold=0.7):
            return 2
        if self.task.find_one(Labels.hsin_h1, threshold=0.7):
            return 1
        return 0

    def heavy_wait_highlight_down(self, time_out: float, protect_animation: bool = False) -> bool:
        self.logger.debug(f'Hsin heavy hold start: timeout={time_out:.2f}')
        self.check_combat()
        self.task.mouse_down()
        if protect_animation:
            # H2 隐藏 HUD 时保护动画，等待 R2 衔接。
            self.h2_guard_until = time.time() + 4
            self.logger.info('Hsin H2 protection start: at most 4s through R2 transition')
        try:
            confirmed = bool(self.task.wait_until(
                lambda: not self.heavy_available(), time_out=time_out,
                settle_time=0.25, pre_action=self.check_combat))
        except BaseException:
            self.h2_guard_until = 0.0
            raise
        finally:
            self.task.mouse_up()
            self.logger.debug('Hsin heavy hold: mouse released')
        if not confirmed:
            self.h2_guard_until = 0.0
        self.sleep(0.01)
        self.logger.debug(f'Hsin heavy hold end: indicator_disappeared={confirmed}')
        return confirmed

    def handle_heavy(self, heavy_type: int = 0) -> bool:
        self.logger.debug('Hsin heavy check start')
        heavy_type = heavy_type or self.heavy_available()
        if not heavy_type:
            self.logger.debug('Hsin heavy end: unavailable')
            return False
        # 已经开始的重击使用完整的按住时长。
        confirmed = self.heavy_wait_highlight_down(1.2, protect_animation=heavy_type == 2)
        self.logger.debug(f'Hsin heavy: type={heavy_type}, confirmed={confirmed}')
        if confirmed and heavy_type == 2:
            self.h2_completed = True
        return confirmed

    def wait_for_lib2(self, after_heavy: bool = False) -> bool:
        self.logger.debug(f'Hsin lib2 wait start: after_heavy={after_heavy}')
        confirmed = bool(self.task.wait_until(
            lambda: (after_heavy or bool(self.task.find_one(Labels.hsin_lib2, threshold=0.7)))
                    and self.lib(after_heavy=after_heavy) and self.lib2_cast_this_turn,
            time_out=1.5, pre_action=self.check_combat))
        self.logger.debug(f'Hsin lib2 wait end: cast={confirmed}, pending={self.h2_completed and not confirmed}')
        return confirmed
