import re
import time
import cv2
from dataclasses import dataclass

from src.task.BaseCombatTask import BaseCombatTask, CharRevivedException
from src.task.WWOneTimeTask import WWOneTimeTask

TRAVEL_FEATURES = ['fast_travel_custom', 'gray_teleport', 'remove_custom']
CONFIRM_FEATURES = ['confirm_btn_hcenter_vcenter', 'confirm_btn_highlight_hcenter_vcenter']
NEST_ABSORB_TEXT = re.compile(r'(吸收|Absorb)', re.IGNORECASE)


@dataclass
class NestTarget:
    box: object
    cache_key: str


@dataclass
class NestPickupState:
    attempts: int = 0
    collected: bool = False
    complete: bool = False

    def begin_attempt(self):
        if self.attempts >= 3:
            return False
        self.attempts += 1
        self.complete = False
        return True


class NightmareNestTask(WWOneTimeTask, BaseCombatTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config = {'_enabled': True}
        self.trigger_interval = 0.1
        self.target_enemy_time_out = 10
        self.name = "🌙 Nightmare Nest Task"
        self.description = "Auto Farm all Nightmare Nest"
        self.support_schedule_task = True
        self.count_re = re.compile(r"(\d{1,2})/(\d{1,2})")
        self.queues = []
        self._capture_success = False
        self._capture_mode = False
        self._unreachable_nests = set()
        self._death_recovery_counts = {}
        self._echo_pickup_states = {}
        self._nest_tab_of_current_nest = 'go_nest'
        self.default_config.update({'Which to Farm': ['Nightmare Purification', 'Tacet Discord Nest']})
        self.config_type['Which to Farm'] = {'type': "multi_selection",
                                             'options': ['Nightmare Purification', 'Tacet Discord Nest']}

    def run(self):
        self._capture_mode = False
        self._capture_success = False
        self._unreachable_nests.clear()
        self._death_recovery_counts.clear()
        self._echo_pickup_states.clear()
        WWOneTimeTask.run(self)
        self.ensure_main(time_out=30)
        self._init_queue()
        self.log_info('opened gray_book_boss')
        while nest := self.get_nest_to_go():
            self.combat_nest(nest)
        self.ensure_main(time_out=30)

    def run_capture_mode(self):
        self._capture_mode = True
        self._capture_success = False
        self._unreachable_nests.clear()
        self._death_recovery_counts.clear()
        self._echo_pickup_states.clear()
        WWOneTimeTask.run(self)
        self.ensure_main(time_out=30)
        self._init_queue()
        self.log_info('opened gray_book_boss')
        while nest := self.get_nest_to_go():
            self.combat_nest(nest)
            if self._capture_success:
                break
        self.ensure_main(time_out=30)

    def on_combat_check(self):
        if self._capture_mode:
            self._pick_nest_echo()
            if self.has_echo_notification():
                return self.reset_to_false(reason='echo captured')
        return True

    def has_echo_notification(self):
        if self._echo_notification_visible():
            self._capture_success = True
        return self._capture_success

    def _echo_notification_visible(self):
        return bool(self.find_best_match_in_box(
            self.box_of_screen(0.078, 0.488, 0.094, 0.514),
            ['char_1_text', 'char_3_text'], 0.6,
            frame_processor=convert_image_to_negative,
        ))

    def combat_nest(self, nest):
        if not hasattr(self, '_death_recovery_counts'):
            self._death_recovery_counts = {}
        target_box = nest.box if isinstance(nest, NestTarget) else nest
        pickup = self._get_nest_pickup_state(nest)
        self.click(target_box, after_sleep=2)
        # ``team_close`` is a generic top-right icon that is also visible on
        # the guidebook itself.  The actionable team-start button is the
        # reliable signal that this target opened a team route.
        feature = self.wait_feature(TRAVEL_FEATURES + ['team_start_challenge'], time_out=10,
                                    settle_time=0.5, raise_if_not_found=True)
        is_team = feature.name == 'team_start_challenge'
        if is_team:
            self.click_team_challenge()
            self.wait_in_team_and_world(time_out=120)
        else:
            if not self._travel_to_nest_or_skip(nest):
                return
            self.sleep(1)
            while self.find_f_with_text():
                self.send_key('f', after_sleep=1)
                self.wait_in_team_and_world(time_out=40, raise_if_not_found=False)
            self.sleep(2)
            self.run_until(self.in_combat, 'w', time_out=10, running=False, target=True)
        wait_combat_time = 10
        while True:
            try:
                self.combat_once(wait_combat_time=wait_combat_time, target=True,
                                 raise_if_not_found=False)
            except CharRevivedException:
                cache_key = getattr(nest, 'cache_key', str(target_box))
                attempts = self._death_recovery_counts.get(cache_key, 0) + 1
                self._death_recovery_counts[cache_key] = attempts
                if attempts >= 3:
                    self._unreachable_nests.add(cache_key)
                    self.log_info(
                        'nightmare nest: exceeded death recovery retries (3), skip target',
                        notify=True,
                    )
                else:
                    self.log_info(
                        f'nightmare nest: death recovered ({attempts}/3), re-enter from F2 book'
                    )
                return
            captured_early = False
            if self._capture_mode:
                if self._capture_success or self.wait_until(self.has_echo_notification, time_out=3):
                    self.log_info("Captured echo during combat, skipping search.")
                    captured_early = True
            if not captured_early:
                self.sleep(3)
                while pickup.begin_attempt():
                    self.log_info(f'聚落声骸领取：第 {pickup.attempts}/3 次尝试')
                    if pickup.attempts > 1 and not is_team:
                        if not self._reenter_nest_for_pickup(nest):
                            continue
                    collected, cleared = self._collect_nest_echoes()
                    pickup.collected |= collected
                    self._capture_success |= collected
                    pickup.complete = cleared and pickup.collected
                    if pickup.complete or (self._capture_mode and self._capture_success):
                        break
                    if self.in_combat():
                        break
            if not self._should_continue_combat_after_pickup():
                break
            self.log_info('nightmare nest: combat detected after pickup')
            wait_combat_time = 1
            pickup.complete = False
        if pickup.attempts >= 3 and not pickup.complete:
            self.log_warning('聚落声骸领取已尝试 3 次，继续后续任务')
            self.screenshot('nest_echo_pickup_incomplete')
            self._unreachable_nests.add(getattr(nest, 'cache_key', str(target_box)))
        # 与刷全部一致：退本后再结束 combat_nest，避免还在巢穴内回 Daily/开书
        if is_team:
            self.esc_world_confirm()
        self._death_recovery_counts.pop(getattr(nest, 'cache_key', str(target_box)), None)
        self.sleep(1)

    def _get_nest_pickup_state(self, nest):
        cache_key = nest.cache_key if isinstance(nest, NestTarget) else str(nest)
        return self._echo_pickup_states.setdefault(cache_key, NestPickupState())

    def _reenter_nest_for_pickup(self, nest):
        self.ensure_main(time_out=30)
        if not self.openF2Book('gray_book_boss', raise_if_not_found=False):
            self.back(after_sleep=1)
            return False
        getattr(self, self._nest_tab_of_current_nest)()
        self.sleep(1)
        self.click(nest.box if isinstance(nest, NestTarget) else nest, after_sleep=2)
        if not self.wait_feature(TRAVEL_FEATURES, time_out=5, settle_time=0.5,
                                 raise_if_not_found=False):
            self.back(after_sleep=1)
            return False
        if not self._travel_to_nest_or_skip(nest):
            return False
        self.sleep(2)
        return True

    def _pick_nest_echo(self, before_pick=None):
        target_text = NEST_ABSORB_TEXT
        if not self.find_f_with_text(target_text=target_text):
            return False
        if before_pick is not None:
            before_pick()
        notification_visible = self._echo_notification_visible()
        self.send_key('f', after_sleep=0.3)
        if self.has_claim():
            self.back(after_sleep=0.5)
            return False
        confirmed = self.wait_until(
            lambda: (not notification_visible and self._echo_notification_visible())
            or not self.find_f_with_text(target_text=target_text),
            time_out=2, raise_if_not_found=False,
        )
        if confirmed:
            self._capture_success = True
            self.log_info('聚落声骸吸收已确认')
        return bool(confirmed)

    def _collect_nest_echoes(self, time_out=30):
        deadline = time.monotonic() + time_out
        collected = False
        for direction in ('w', 'a', 's', 'd'):
            if time.monotonic() >= deadline:
                return collected, False
            self.turn_direction(direction)
            while time.monotonic() < deadline:
                self.next_frame()
                if self._pick_nest_echo():
                    collected = True
                    if self._capture_mode:
                        return True, True
                    continue
                if self.in_combat():
                    return collected, False
                if not self.find_echos(threshold=0.5):
                    self.sleep(0.3)
                    self.next_frame()
                    if not self.find_echos(threshold=0.5):
                        break
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self.walk_to_yolo_echo(
                        time_out=min(8, remaining), pick_function=self._pick_nest_echo):
                    return collected, False
                collected = True
                if self._capture_mode:
                    return True, True
            else:
                return collected, False
        return collected, True

    def _should_continue_combat_after_pickup(self):
        return not self._capture_mode and self.wait_combat(
            target=True, time_out=3, raise_if_not_found=False)

    def _travel_to_nest_or_skip(self, nest):
        travel = self.wait_until(self._find_travel_button, raise_if_not_found=False, time_out=1)
        if travel:
            self.click(travel, after_sleep=1)
            if confirm := self._find_first_feature(CONFIRM_FEATURES, threshold=0.6):
                self.click(confirm, after_sleep=1)

        button_still_visible = travel and self.find_one(travel.name, threshold=0.7)
        if travel and not button_still_visible and self.wait_in_team_and_world(
                time_out=120, raise_if_not_found=False):
            return True

        if isinstance(nest, NestTarget):
            self._unreachable_nests.add(nest.cache_key)
            self.log_info(f'nightmare nest unreachable, skip this run: {nest.cache_key}')
        else:
            self.log_info('nightmare nest unreachable, skip this run')
        self.back(after_sleep=1)
        return False

    def _find_travel_button(self):
        return self._find_first_feature(TRAVEL_FEATURES, threshold=0.7)

    def _find_first_feature(self, feature_names, threshold):
        for feature_name in feature_names:
            if feature := self.find_one(feature_name, threshold=threshold):
                return feature

    def get_nest_to_go(self):
        self.openF2Book("gray_book_boss")

        while self.queues:
            self.queues[0]()
            if nest := self.find_nest():
                self._nest_tab_of_current_nest = self.queues[0].__name__
                return nest
            self.queues.pop(0)

    def _init_queue(self):
        quests = getattr(self, 'farm_targets_override', None)
        if quests is None:
            quests = self.config.get('Which to Farm') or ['Nightmare Purification', 'Tacet Discord Nest']
        actions = []
        if 'Tacet Discord Nest' in quests:
            actions.append(self.go_nest)
            actions.append(self.go_nest_scroll)
        if 'Nightmare Purification' in quests:
            actions.append(self.go_nightmare)
            actions.append(self.go_nightmare_scroll)
        self.queues = actions

    def go_nightmare(self):
        self.open_boss_book('mengyan')
        self.log_info('go nightmare')

    def go_nightmare_scroll(self):
        self.open_boss_book('mengyan')
        self.click(3737 / 3840, 0.54, after_sleep=1)
        self.log_info('go nightmare scroll')

    def go_nest(self):
        self.open_boss_book('canxiang')

    def go_nest_scroll(self):
        self.open_boss_book('canxiang')
        self.click(0.9730, 0.8806, after_sleep=1)

    def find_nest(self):
        counts = self.ocr(0.35, 0.25, 1, 0.96, match=self.count_re)
        for count_box in counts:
            for match in re.finditer(self.count_re, count_box.name):
                numerator = match.group(1)
                denominator = match.group(2)
                if numerator != denominator and denominator in ['24', '36', '48', '41'] and numerator == '0':
                    cache_key = self._make_nest_cache_key(count_box, denominator)
                    if cache_key in self._unreachable_nests:
                        self.log_info(f'skip cached unreachable nightmare nest: {cache_key}')
                        continue
                    self.log_info(f'{count_box} is not complete')
                    count_box.x = self.width_of_screen(0.9)
                    count_box.y -= count_box.height * 0.9
                    count_box.height = 1
                    count_box.width = 1
                    return NestTarget(count_box, cache_key)

    def _make_nest_cache_key(self, count_box, denominator):
        action_name = self.queues[0].__name__ if self.queues else 'unknown'
        screen_height = max(self.height_of_screen(1), 1)
        row_y = (count_box.y + count_box.height / 2) / screen_height
        row_slot = round(row_y / 0.02)
        # 使用粗粒度行槽位，避免 OCR 坐标轻微抖动导致同一目标被重复点击。
        return f'{action_name}:{denominator}:{row_slot}'


def convert_image_to_negative(img):
    to_gray = False
    _mat = img
    if len(_mat.shape) == 3:
        to_gray = True
        _mat = cv2.cvtColor(_mat, cv2.COLOR_BGR2GRAY)
    _, _mat = cv2.threshold(_mat, 80, 255, cv2.THRESH_BINARY)
    _mat = cv2.bitwise_not(_mat)
    if to_gray:
        _mat = cv2.cvtColor(_mat, cv2.COLOR_GRAY2BGR)
    return _mat


from ok import run_task
from config import config

if __name__ == "__main__":
    run_task(config, task=NightmareNestTask, debug=True)
