
from ok import Logger, TaskDisabledException
from src.task.BaseCombatTask import (
    BaseCombatTask,
    CharRevivedException,
    NotInCombatException,
)
from src.task.WWOneTimeTask import WWOneTimeTask

logger = Logger.get_logger(__name__)


class TacetTask(WWOneTimeTask, BaseCombatTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.description = "Farms the selected Tacet Suppression, until no stamina. Must be able to teleport (F2)."
        self.name = "🌊 Tacet Suppression"
        self.support_schedule_task = True
        default_config = {
            'Which Tacet Suppression to Farm': 1,  # starts with 1
        }
        self.structure = [4, 5, 5, 7]
        self.total_number = sum(self.structure)
        self.target_enemy_time_out = 10
        default_config.update(self.default_config)
        self.config_description = {
            'Which Tacet Suppression to Farm': 'The Tacet Suppression number in the F2 list.',
        }
        self.default_config = default_config
        self.door_walk_method = {  # starts with 0
            0: [],
            1: [],
            2: [],
            3: [],
            4: [],
            5: [],
            6: [],
            7: [],
            8: [],
            9: [["a", 0.3]],
            10: [["d", 0.6]],
            11: [["a", 1.5], ["w", 3], ["a", 2.5]],
        }
        self.stamina_once = 60

    def _recover_after_round_state_error(self, error):
        """Return to a stable world state before retrying a Tacet round.

        ``combat_once`` deliberately treats a lost combat target as a normal
        combat end.  A transient team-portrait loss can therefore leave the
        task between the combat and reward screens.  Do not send movement or
        interact keys in that state; settle the game back to the world first.
        """
        self.log_warning(f'Tacet round state lost; recovering before retry: {error}')
        try:
            self.screenshot('tacet_round_state_error')
        except Exception as screenshot_error:
            self.log_warning('Tacet round recovery screenshot failed', screenshot_error)
        try:
            self.ensure_main(esc=True, time_out=60)
            return True
        except TaskDisabledException:
            raise
        except Exception as recovery_error:
            self.log_warning('Tacet round state recovery failed', recovery_error)
            return False

    def _walk_to_treasure_with_retry(self):
        """Wait for a stable team state and retry one missed reward scan."""
        try:
            self.walk_to_treasure()
            return
        except TaskDisabledException:
            raise
        except Exception as first_error:
            self.log_warning(
                f'Tacet treasure was not detected on the first scan; retrying: {first_error}'
            )

        if not self.wait_in_team_and_world(time_out=20, raise_if_not_found=False):
            raise NotInCombatException('team state unavailable before Tacet treasure retry')
        self.sleep(2)
        self.walk_to_treasure()

    def _handle_round_state_error(self, error, recovery_retries, max_recovery_retries, stage):
        """Recover a transient round error or raise after the retry budget."""
        recovery_retries += 1
        if recovery_retries >= max_recovery_retries:
            self.log_info(
                f'Tacet Suppression exceeded {stage} recovery retries ({max_recovery_retries}), stop farming',
                notify=True,
            )
            raise RuntimeError(
                f'Tacet {stage} state remained unstable after {max_recovery_retries} retries'
            ) from error
        if not self._recover_after_round_state_error(error):
            raise RuntimeError(f'Tacet {stage} state recovery failed') from error
        return recovery_retries

    def run(self):
        super().run()
        self.ensure_main(time_out=180)
        self.wait_in_team_and_world(esc=True)
        self.farm_tacet()

    def farm_tacet(self, daily=False, used_stamina=0, config=None):
        if config is None:
            config = self.config
        if daily:
            must_use = 180 - used_stamina
        else:
            must_use = 0
        recovery_retries = 0
        max_recovery_retries = 3
        self.info_incr('used stamina', 0)
        while True:
            self.sleep(1)
            self.openF2Book("gray_book_boss")
            current, back_up, total = self.get_stamina()
            if current == -1:
                self.click_relative(0.04, 0.4, after_sleep=1)
                current, back_up, total = self.get_stamina()
            if total < self.stamina_once:
                return self.not_enough_stamina()

            self.open_boss_book('wuyin')
            index = config.get('Which Tacet Suppression to Farm', 1) - 1
            self.log_info(
                f'Configured Tacet Suppression {index + 1} '
                f'(internal index {index})'
            )
            is_team = self.teleport_to_tacet(index)
            if is_team:
                self.click_team_challenge()
            recovered_from_round_error = False
            while True:
                self.wait_in_team_and_world(time_out=120)
                try:
                    self.combat_once(target=True)
                    # ``combat_once`` can return after a transient team
                    # portrait loss.  Confirm the world/team state before
                    # walking; otherwise a reward timeout causes a needless
                    # account-level failure.
                    if not self.wait_in_team_and_world(time_out=20, raise_if_not_found=False):
                        raise NotInCombatException('team state unavailable after Tacet combat')
                except CharRevivedException:
                    recovery_retries += 1
                    if recovery_retries >= max_recovery_retries:
                        self.log_info(
                            f'Tacet Suppression exceeded death recovery retries ({max_recovery_retries}), stop farming',
                            notify=True,
                        )
                        return None
                    self.log_info('Tacet Suppression death recovered; re-enter from F2 book')
                    recovered_from_round_error = True
                    break
                except NotInCombatException as error:
                    recovery_retries = self._handle_round_state_error(
                        error, recovery_retries, max_recovery_retries, 'round'
                    )
                    recovered_from_round_error = True
                    break
                try:
                    self._walk_to_treasure_with_retry()
                except NotInCombatException as error:
                    recovery_retries = self._handle_round_state_error(
                        error, recovery_retries, max_recovery_retries, 'treasure'
                    )
                    recovered_from_round_error = True
                    break
                self.pick_f(handle_claim=False)
                self.sleep(2)
                if not self.has_claim_stamina():
                    self.esc_cancel()
                    self.log_info('is not claim treasure, restart challenge')
                    continue
                can_continue, used = self.use_stamina(once=self.stamina_once, must_use=must_use)
                recovery_retries = 0
                self.info_incr('used stamina', used)
                self.sleep(4)
                if not can_continue:
                    self.click_relative(0.365, 0.853, hcenter=True)
                    self.wait_in_team_and_world(time_out=120)
                    return None
                else:
                    self.click_relative(0.640, 0.851, hcenter=True, after_sleep=0.2)
                    self.wait_click_skip_dialog_confirm()
                must_use -= used
            if recovered_from_round_error:
                continue

    def not_enough_stamina(self, back=True):
        self.log_info(f"used all stamina")
        if back:
            self.back(after_sleep=1)

    def teleport_to_tacet(self, index):
        # Configuration is one-based (matching the F2 list).  Keep the
        # internal index zero-based, but report the user-facing number so a
        # run can be verified directly from the task panel/log.
        self.info_set('Teleport to Tacet Suppression', index + 1)
        if index >= self.total_number:
            raise IndexError(f'Index out of range, max is {self.total_number}')
        return self.click_on_book_target(index + 1, self.total_number, self.structure)
