import time

from src.task.AutoCombatTask import AutoCombatTask
from src.task.BaseCombatTask import NotInCombatException
from src.workflow.input import WorkflowStopped


class CombatSceneChanged(Exception):
    pass


class CombatSession:
    def __init__(self, task):
        self.task = task

    def run(self, observation, deadline):
        task = self.task
        if task._recorded_dialogue.advance(task, task._current_step, observation):
            return 'story_interrupted'
        if observation.scene == 'dialog':
            task._coordinator.handoff('dialog')
            task._advance_workflow_dialog(observation)
            return 'story_interrupted'
        if observation.scene in ('cutscene', 'loading', 'result'):
            return 'story_interrupted'
        if observation.scene == 'menu':
            raise WorkflowStopped('战斗中出现菜单，录制材料无法确认对应选项')
        if not task.in_combat():
            return 'waiting_next_wave'
        task._combat_session = True
        task._combat_deadline = deadline
        try:
            settings = task.get_task_by_class(AutoCombatTask)
            if settings is not None:
                task.use_liberation = settings.config.get('Use Liberation') or not task.in_world()
            if not task.load_chars():
                raise WorkflowStopped('无法识别当前剧情战斗队伍')
            task.switch_healer()
            while task.in_combat():
                if time.monotonic() >= deadline:
                    raise WorkflowStopped('战斗没有在规定时间内完成')
                task.check_workflow_input()
                char = task.get_current_char()
                if char is None:
                    raise WorkflowStopped('当前战斗角色无法确定')
                char.perform()
            return 'waiting_next_wave'
        except CombatSceneChanged:
            return 'story_interrupted'
        except NotInCombatException as error:
            if type(error) is not NotInCombatException:
                raise
            return 'waiting_next_wave'
        finally:
            task._combat_session = False
            task._coordinator.release()
            task.combat_end()
            task._in_combat = False
            task.scene.reset()
