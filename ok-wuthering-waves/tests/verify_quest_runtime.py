from copy import deepcopy
import argparse
from pathlib import Path
import sys
import threading
import json
import time

import cv2

report_path = Path(__file__).resolve().parent.parent / "screenshots/quest-runtime-report.txt"
report_path.parent.mkdir(exist_ok=True)
sys.stdout = report_path.open("w", encoding="utf-8", buffering=1)
sys.stderr = sys.stdout
sys.argv[0] = str(Path(__file__).resolve().parent.parent / "main.py")

from config import config
from ok import OK
from ok.util.process import is_admin
from src.task.QuestStoryTask import QuestStoryTask
from src.utils.QuestAreaSearch import minimap_box


parser = argparse.ArgumentParser()
parser.add_argument("--expect", choices=("area", "movement", "jump", "combat"), default="area")
parser.add_argument("--seconds", type=int, default=90)
parser.add_argument("--keep-front", action="store_true")
arguments = parser.parse_args()
if not is_admin():
    raise PermissionError("实际游戏运行验证需要使用管理员权限启动")

runtime_config = deepcopy(config)
runtime_config.update({
    "check_mutex": False,
    "gui": None,
    "onetime_tasks": [["src.task.MouseResetTask", "MouseResetTask"], ["src.task.QuestStoryTask", "QuestStoryTask"]],
    "trigger_tasks": [],
    "log_file": "logs/quest-verification.log",
})
runtime = OK(runtime_config)
task = runtime.get_onetime_task(QuestStoryTask)
if task.decision_session is None or task.navigation_progress is None or task.config is None:
    raise RuntimeError("剧情任务初始化没有完成")
print("剧情任务及实际运行环境初始化完成", flush=True)
timer = threading.Timer(arguments.seconds, runtime.exit_event.set)
frame_path = Path(__file__).resolve().parent.parent / "screenshots" / "quest-verification.png"
frame_path.parent.mkdir(exist_ok=True)
capture_directory = frame_path.parent / "quest-area-capture" / time.strftime("%Y%m%d-%H%M%S")
capture_directory.mkdir(parents=True, exist_ok=True)
observations = {"initial_distance": None, "closest_distance": None, "goal": None, "movement_actions": 0,
                "movement_observed": False, "jump_attempted": False, "combat_completed": False,
                "resumed_after_combat": False, "foreground_error": None}


def record_frames():
    index = 0
    while not runtime.exit_event.wait(.2):
        if runtime.task_executor.current_task is not task:
            continue
        if arguments.keep_front and not task.is_game_window_active():
            task.ensure_in_front()
            time.sleep(.08)
            if not task.is_game_window_active():
                observations["foreground_error"] = "限时验证无法将游戏窗口保持在前台"
                runtime.exit_event.set()
                break
        frame = runtime.task_executor._frame
        if frame is not None:
            cv2.imwrite(str(frame_path), frame.copy())
            if index == 0 or index % 20 == 0:
                cv2.imwrite(str(capture_directory / f"scene-{index:04}.png"), frame.copy())
            x, y, w, h = minimap_box(frame)
            sample_path = capture_directory / f"sample-{index:04}.png"
            cv2.imwrite(str(sample_path), frame[y:y + h, x:x + w].copy())
            area = task.area_search
            distance = task.navigation_progress.best_distance
            observations["movement_actions"] = task.scene.movement_actions
            if area is None and distance is not None:
                if observations["initial_distance"] is None or observations["goal"] != task.guidance_text:
                    observations["initial_distance"] = distance
                    observations["closest_distance"] = distance
                    observations["goal"] = task.guidance_text
                observations["closest_distance"] = distance if observations["closest_distance"] is None else min(distance, observations["closest_distance"])
                if observations["initial_distance"] - distance >= .5 and observations["movement_actions"] > 0:
                    observations["movement_observed"] = True
            observations["jump_attempted"] |= task.navigation_progress.jump_attempts > 0
            observations["combat_completed"] |= task.quest_combat_count > 0
            if observations["combat_completed"] and task.current_state in (task.STATE_NAVIGATE, task.STATE_DIALOG, task.STATE_LETTERBOX_CUTSCENE):
                observations["resumed_after_combat"] = True
            sample_path.with_suffix(".json").write_text(json.dumps({
                "time": time.time(),
                "context": area.context() if area is not None and area.observation is not None else None,
                "keys": area.pending_keys if area is not None else None,
                "navigation_distance": distance,
                "jump_attempts": task.navigation_progress.jump_attempts,
            }, ensure_ascii=False), encoding="utf-8")
            index += 1
            if arguments.expect == "movement" and observations["movement_observed"]:
                runtime.exit_event.set()
            elif arguments.expect == "jump" and observations["jump_attempted"] and observations["movement_observed"]:
                runtime.exit_event.set()
            elif arguments.expect == "combat" and observations["resumed_after_combat"]:
                runtime.exit_event.set()
    print("已记录运行画面数量", index, flush=True)


recorder = threading.Thread(target=record_frames)
recorder.start()
timer.start()
try:
    runtime.run_onetime_task(task)
    print("剧情模式运行检查结束", task.info, flush=True)
    if observations["foreground_error"]:
        raise RuntimeError(observations["foreground_error"])
    if task.info.get("Error"):
        raise RuntimeError(task.info["Error"])
    if arguments.expect == "area":
        if task.area_search is None or task.area_search.index == 0:
            raise AssertionError("限时运行尚未到达任务区域中心")
    elif arguments.expect == "movement" and not observations["movement_observed"]:
        raise AssertionError("限时运行没有确认目标距离缩减")
    elif arguments.expect == "jump" and (not observations["movement_observed"] or not observations["jump_attempted"]):
        raise AssertionError("限时运行没有确认跳跃尝试及目标距离缩减")
    elif arguments.expect == "combat" and not observations["resumed_after_combat"]:
        raise AssertionError("限时运行尚未确认战斗结束后继续剧情或导航")
finally:
    timer.cancel()
    if runtime.task_executor._frame is not None:
        cv2.imwrite(str(frame_path), runtime.task_executor._frame)
    print("区域搜索状态", task.area_search.context() if task.area_search is not None else None, flush=True)
    print("实际移动观察", observations, flush=True)
    runtime.exit_event.set()
    recorder.join()
    runtime.quit()
