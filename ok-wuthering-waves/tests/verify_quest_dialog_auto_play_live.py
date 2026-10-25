from copy import deepcopy
from pathlib import Path
import argparse
import sys
import time

import cv2

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.argv[0] = str(ROOT / "main.py")

from config import config
from ok import OK
from ok.util.process import is_admin
from src.task.QuestStoryTask import QuestStoryTask
from src.utils.QuestDialogAutoPlay import detect_dialog_auto_play


class QuestDialogAutoPlayVerificationTask(QuestStoryTask):
    def run(self):
        self.ensure_in_front()
        deadline = time.monotonic() + self.verify_seconds
        directory = Path("working_images/quest-dialog-auto-play-live")
        directory.mkdir(parents=True, exist_ok=True)
        enabled_since = None
        observations = 0
        while time.monotonic() < deadline:
            frame = self.next_frame()
            result = detect_dialog_auto_play(frame)
            if not result.found:
                self._reveal_hidden_ui()
                self.sleep(0.2)
                continue
            if enabled_since is not None and not result.enabled:
                raise AssertionError("确认开启后自动对话档位发生变化")
            if enabled_since is None:
                cv2.imwrite(str(directory / "before.png"), frame)
            self._handle_dialog_auto_play(frame)
            after = detect_dialog_auto_play(self.frame)
            if after.found and after.enabled:
                if enabled_since is None:
                    enabled_since = time.monotonic()
                    cv2.imwrite(str(directory / "enabled.png"), self.frame)
                observations += 1
                if observations >= 5 and time.monotonic() - enabled_since >= 2:
                    cv2.imwrite(str(directory / "maintained.png"), self.frame)
                    self.info_set("自动对话验证完成", True)
                    return
            elif enabled_since is not None:
                raise AssertionError("确认开启后自动对话档位发生变化")
            self.sleep(0.25)
        raise AssertionError("限时验证没有确认剧情完整自动档位持续开启")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=60)
    arguments = parser.parse_args()
    if not is_admin():
        raise PermissionError("实际游戏自动对话验证需要管理员权限")
    runtime_config = deepcopy(config)
    runtime_config.update({
        "check_mutex": False,
        "gui": None,
        "onetime_tasks": [["__main__", "QuestDialogAutoPlayVerificationTask"]],
        "trigger_tasks": [],
        "custom_tasks": False,
        "log_file": "logs/quest-dialog-auto-play-verification.log",
    })
    runtime = OK(runtime_config)
    try:
        task = runtime.get_onetime_task(QuestDialogAutoPlayVerificationTask)
        task.verify_seconds = arguments.seconds
        runtime.run_onetime_task(task)
        if task.info.get("Error") or not task.info.get("自动对话验证完成"):
            raise AssertionError(str(task.info))
        print("实际游戏完整自动对话状态验证通过", flush=True)
    finally:
        runtime.quit()


if __name__ == "__main__":
    main()
