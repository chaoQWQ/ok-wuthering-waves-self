from copy import deepcopy
from pathlib import Path
import sys
import time

import cv2

sys.argv[0] = str(Path(__file__).resolve().parent.parent / "main.py")

from config import config
from ok import Box, OK
from src.task.QuestStoryTask import QuestStoryTask
from src.utils.QuestAreaSearch import minimap_visible
from src.utils.QuestVision import detect_top_left_skip_button


class QuestSkipVerificationTask(QuestStoryTask):
    def run(self):
        self.ensure_in_front()
        deadline = time.monotonic() + 60
        confirmed = False
        directory = Path("working_images/quest-skip-live")
        directory.mkdir(parents=True, exist_ok=True)
        while time.monotonic() < deadline:
            frame = self.frame
            button = self._find_story_skip_confirm(frame)
            if button is not None:
                cv2.imwrite(str(directory / "confirm-before.png"), frame)
                if self._handle_story_skip_confirm():
                    confirmed = True
                    cv2.imwrite(str(directory / "confirm-after.png"), self.frame)
            elif confirmed and minimap_visible(frame):
                self.info_set("验证完成", True)
                cv2.imwrite(str(directory / "world-after.png"), frame)
                return
            elif self.find_skip() is not None:
                self.try_click_skip()
            else:
                result = detect_top_left_skip_button(frame)
                if result.found:
                    cv2.imwrite(str(directory / "entry-before.png"), frame)
                    self._click_story_button(Box(result.x, result.y, result.width, result.height,
                                                 name="top_left_skip_dialog"))
            self.sleep(.2)
            self.next_frame()
        raise AssertionError("限时验证没有确认跳过剧情后返回大世界")


def main():
    runtime_config = deepcopy(config)
    runtime_config.update({
        "check_mutex": False,
        "gui": None,
        "onetime_tasks": [["__main__", "QuestSkipVerificationTask"]],
        "trigger_tasks": [],
        "custom_tasks": False,
    })
    runtime = OK(runtime_config)
    try:
        task = runtime.get_onetime_task(QuestSkipVerificationTask)
        runtime.run_onetime_task(task)
        if task.info.get("Error") or not task.info.get("验证完成"):
            raise AssertionError(str(task.info))
        print("实际剧情跳过验证通过", flush=True)
    finally:
        runtime.quit()


if __name__ == "__main__":
    main()
