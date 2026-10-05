from copy import deepcopy
from pathlib import Path
import sys

import cv2


sys.stdout.reconfigure(encoding="utf-8")
root = Path(__file__).resolve().parent.parent
sys.argv[0] = str(root / "main.py")

from config import config
from ok import OK
from src.task.QuestStoryTask import QuestStoryTask
from src.utils.QuestOcrPrivacy import is_named_quest_interaction


runtime_config = deepcopy(config)
runtime_config.update({
    "check_mutex": False,
    "gui": None,
    "onetime_tasks": [["src.task.QuestStoryTask", "QuestStoryTask"]],
    "trigger_tasks": [],
    "log_file": "logs/quest-interaction-verification.log",
})
runtime = OK(runtime_config)
try:
    task = runtime.get_onetime_task(QuestStoryTask)
    if task.decision_session is None or task.config is None:
        raise RuntimeError("剧情任务初始化没有完成")
    # 截图检查启用 OCR 读取，任务线程保持未启动。
    runtime.task_executor.paused = False
    original = cv2.imread(str(root / "tests/images/quest_yangyang_interaction.png"))
    if original is None:
        raise FileNotFoundError("秧秧交互截图不存在")
    for width in (1024, 1280, 1817, 1920):
        frame = cv2.resize(original, (width, round(original.shape[0] * width / original.shape[1])))
        task._read_quest_goal(frame, force=True)
        has_f, interaction = task._read_interaction(frame)
        print(width, has_f, interaction, task.guidance_text, flush=True)
        if not has_f or interaction != "秧秧" or not is_named_quest_interaction(task.guidance_text, interaction):
            raise AssertionError("剧情任务没有完整识别实际任务与交互名称")
    print("剧情任务初始化与交互截图读取检查通过", flush=True)
finally:
    runtime.quit()
