from copy import deepcopy
from pathlib import Path
import sys
import threading

import cv2

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
sys.argv[0] = str(Path(__file__).resolve().parent.parent / "main.py")

from config import config
from ok import OK
from src.task.QuestStoryTask import QuestStoryTask


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
timer = threading.Timer(40, runtime.exit_event.set)
frame_path = Path(__file__).resolve().parent.parent / "screenshots" / "quest-verification.png"
frame_path.parent.mkdir(exist_ok=True)


def record_frames():
    index = 0
    while not runtime.exit_event.wait(.2):
        frame = runtime.task_executor._frame
        if frame is not None:
            cv2.imwrite(str(frame_path), frame.copy())
            index += 1
    print("已记录运行画面数量", index, flush=True)


recorder = threading.Thread(target=record_frames)
recorder.start()
timer.start()
try:
    runtime.run_onetime_task(task)
    print("剧情模式运行检查结束", task.info, flush=True)
    if task.info.get("Error"):
        raise RuntimeError(task.info["Error"])
    if task.area_search is None or task.area_search.index == 0:
        raise AssertionError("限时运行尚未到达任务区域中心")
finally:
    timer.cancel()
    if runtime.task_executor._frame is not None:
        cv2.imwrite(str(frame_path), runtime.task_executor._frame)
    print("区域搜索状态", task.area_search.context() if task.area_search is not None else None, flush=True)
    runtime.exit_event.set()
    recorder.join()
    runtime.quit()
