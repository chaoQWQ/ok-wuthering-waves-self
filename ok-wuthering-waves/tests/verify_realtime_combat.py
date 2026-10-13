from copy import deepcopy
from pathlib import Path
import argparse
import json
import sys
import threading
import time

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
directory = Path('cache') / f'realtime-rotation-{time.strftime("%Y%m%d-%H%M%S")}'
directory.mkdir(parents=True, exist_ok=True)
sys.stdout = (directory / 'report.txt').open('w', encoding='utf-8', buffering=1)
sys.stderr = sys.stdout
sys.argv[0] = str(Path(__file__).resolve().parent.parent / 'main.py')

from config import config
from ok import OK
from ok.util.process import is_admin
from src.task.AutoCombatTask import AutoCombatTask
from src.task.BaseCombatTask import BaseCombatTask
from src.task.WWOneTimeTask import WWOneTimeTask
from tests.combat_verification_support import BossVerificationPreparation

parser = argparse.ArgumentParser()
parser.add_argument('--profile-suffix', required=True)
parser.add_argument('--seconds', type=int, default=360)
parser.add_argument('--boss-name', default='无冠巨像之心')
arguments = parser.parse_args()
if not is_admin():
    raise PermissionError('实战验证需要管理员权限')


class ApproachCombatTask(BossVerificationPreparation, WWOneTimeTask, BaseCombatTask):
    def run(self):
        super().run()
        self.prepare_boss(arguments.profile_suffix, arguments.boss_name, directory / 'boss-book.png', heal=True)
        combat = self.executor.get_task_by_class(AutoCombatTask)
        self.run_until(lambda: combat.has_target() or combat.check_health_bar(), 'w',
                       time_out=15, target=True, raise_if_not_found=True)
        combat.enable()
        self.info_set('Combat prepared', True)


runtime_config = deepcopy(config)
runtime_config.update({
    'check_mutex': False,
    'gui': None,
    'onetime_tasks': [['src.task.MouseResetTask', 'MouseResetTask'], ['__main__', 'ApproachCombatTask'],
                     ['src.task.MultiAccountDailyTask', 'MultiAccountDailyTask']],
    'trigger_tasks': [['src.task.AutoCombatTask', 'AutoCombatTask']],
})
runtime = OK(runtime_config)
preparation = runtime.get_onetime_task(ApproachCombatTask)
combat = runtime.get_trigger_task(AutoCombatTask)
initial_enabled = combat.enabled
combat.disable()
states = []
expected = [(0, step) for step in range(3)]
expected += [(round_index, step) for round_index in (1, 2) for step in range(4)]
expected.append((3, 0))
observations = {'battle_seen': False, 'battle_ended': False}


def record_frames():
    index = 0
    while not runtime.exit_event.wait(.2):
        if 'Error' in preparation.info or 'Error' in combat.info:
            runtime.exit_event.set()
            break
        if runtime.task_executor.current_task not in (preparation, combat):
            continue
        frame = runtime.task_executor._frame
        if frame is not None and index % 20 == 0:
            if not cv2.imwrite(str(directory / f'scene-{index:04}.png'), frame.copy()):
                raise RuntimeError('保存实时战斗画面失败')
        index += 1
        if runtime.task_executor.current_task is not combat:
            continue
        observations['battle_seen'] |= combat._in_combat
        for character in combat.chars:
            rotation = getattr(character, '_team_rotation', None)
            if rotation is not None:
                state = (rotation.round, rotation.step)
                if not states or states[-1] != state:
                    states.append(state)
        if observations['battle_seen'] and not combat._in_combat:
            observations['battle_ended'] = True
            runtime.exit_event.set()
        elif states == expected:
            runtime.exit_event.set()


recorder = threading.Thread(target=record_frames)
timer = threading.Timer(arguments.seconds, runtime.exit_event.set)
recorder.start()
timer.start()
try:
    if not runtime.headless_app.start_controller.do_start(preparation, exit_after=False):
        raise RuntimeError('实时战斗验证启动失败')
    runtime.wait_task()
    for task in (preparation, combat):
        if 'Error' in task.info:
            raise RuntimeError(task.info['Error'] or '实际任务报告异常')
    if states != expected:
        raise AssertionError('实时战斗尚未确认启动轴和连续两轮循环')
    print('实时战斗启动轴和连续两轮循环确认通过', flush=True)
finally:
    timer.cancel()
    runtime.exit_event.set()
    recorder.join()
    (directory / 'observations.json').write_text(json.dumps(
        dict(observations, states=states), ensure_ascii=False), encoding='utf-8')
    print('实际实时战斗状态', observations, states, preparation.info, combat.info, flush=True)
    if initial_enabled:
        combat.enable()
    else:
        combat.disable()
    runtime.quit()
