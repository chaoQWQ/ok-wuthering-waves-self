from copy import deepcopy
import argparse
from pathlib import Path
import json
import math
import sys
import threading
import time

import cv2


root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
sys.argv[0] = str(root / "main.py")
sys.stdout.reconfigure(encoding="utf-8")
report = root / "screenshots/quest-movement-runtime-report.txt"
report.parent.mkdir(parents=True, exist_ok=True)
sys.stdout = report.open("w", encoding="utf-8", buffering=1)
sys.stderr = sys.stdout
parser = argparse.ArgumentParser()
parser.add_argument("--camera-only", action="store_true")
parser.add_argument("--timing-only", action="store_true")
arguments = parser.parse_args()

from config import config
from ok import OK
from ok.util.process import is_admin
from src.task.QuestStoryTask import QuestStoryTask
from src.utils.QuestVision import detect_quest_beacon


class MovementVerificationTask(QuestStoryTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.input_events = []
        self.camera_events = []
        self.verified = False

    def send_key_down(self, key, after_sleep=0):
        super().send_key_down(key, after_sleep=after_sleep)
        self.input_events.append(("down", key, time.monotonic()))

    def send_key_up(self, key, after_sleep=0):
        super().send_key_up(key, after_sleep=after_sleep)
        self.input_events.append(("up", key, time.monotonic()))

    def _apply_camera_turn(self, delta_x):
        super()._apply_camera_turn(delta_x)
        self.camera_events.append((delta_x, time.monotonic()))

    def verify_camera(self):
        import win32api

        center = self.executor.interaction.capture.get_abs_cords(
            self.width_of_screen(.5), self.height_of_screen(.5))
        win32api.SetCursorPos(center)
        self.sleep(.2)
        self.next_frame()
        current = detect_quest_beacon(self.frame)
        distance = self._extract_quest_distance(self.frame, current)
        if distance is not None and distance <= 5:
            self._apply_movement(["s"], .8)
            self.next_frame()
        output = root / "screenshots/quest-camera-verification"
        output.mkdir(parents=True, exist_ok=True)
        beacon = detect_quest_beacon(self.frame)
        if not beacon.found:
            raise AssertionError("当前游戏画面没有可用于镜头转向验证的任务信标")
        width = self.frame.shape[1]
        if abs(beacon.x + beacon.width / 2 - width / 2) <= width * .02:
            self._apply_camera_turn(240)
            self.sleep(.2)
            self.next_frame()
            beacon = detect_quest_beacon(self.frame)
            if not beacon.found:
                raise AssertionError("转向验证前无法确认实际任务信标")
        cv2.imwrite(str(output / "before.png"), self.frame)
        initial_error = abs(beacon.x + beacon.width / 2 - width / 2)
        self.camera_events.clear()
        start = time.monotonic()
        self._align_quest_beacon(beacon, float(self.config.get("Camera Sensitivity", 1.0)), .02)
        elapsed = time.monotonic() - start
        result = detect_quest_beacon(self.frame)
        cv2.imwrite(str(output / "after.png"), self.frame)
        if not result.found:
            raise AssertionError("实际转向后无法确认任务信标")
        final_error = abs(result.x + result.width / 2 - width / 2)
        evidence = {"initial_error_pixels": initial_error, "final_error_pixels": final_error,
                    "seconds": elapsed, "turns": self.camera_events}
        (output / "camera-events.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print("实际镜头转向检查", evidence, flush=True)
        if not self.camera_events or final_error > width * .02 or final_error >= initial_error:
            raise AssertionError("实际镜头调整没有让任务信标进入居中范围")
        self.verified = True

    def verify_timing(self):
        self._read_quest_goal(self.frame, force=True)
        if self.goal_candidate:
            self._read_quest_goal(self.frame, force=True)
        if not self.guidance_text or self.goal_candidate:
            raise AssertionError("实际游戏任务提示尚未确认")
        self._reset_navigation_timing()
        self.sleep(6.1)
        self.next_frame()
        self._read_quest_goal(self.frame, force=True)
        if self.goal_candidate:
            self._read_quest_goal(self.frame, force=True)
        self._read_scene_coordinates(self.frame, force=True)
        self.quest_scene.distance = self._extract_quest_distance(self.frame, detect_quest_beacon(self.frame))
        if self._navigation_stalled(self.quest_scene.distance) or self.movement_timeout.started:
            raise AssertionError("第一次移动之前已经开始计算无变化超时")
        self._apply_movement(["w"], .2)
        if not self.movement_timeout.started or self.movement_timeout.movement_seconds <= 0:
            raise AssertionError("实际任务第一次移动没有开始有效移动计时")
        movement_seconds = self.movement_timeout.movement_seconds
        self._apply_camera_turn(40)
        self.sleep(.2)
        self._apply_camera_turn(-40)
        self.sleep(.2)
        if self.movement_timeout.movement_seconds != movement_seconds:
            raise AssertionError("实际镜头转向时间进入了无变化超时计时")
        resume = threading.Timer(.5, self.unpause)
        resume.start()
        try:
            self.pause()
        finally:
            resume.cancel()
            resume.join()
        if self.movement_timeout.started or self.movement_timeout.movement_seconds != 0:
            raise AssertionError("实际任务暂停后没有清除移动计时")
        self.verified = True
        print("实际任务首次移动计时、转向排除与暂停重置检查通过", movement_seconds, flush=True)

    def run(self):
        try:
            self.ensure_in_front()
            self.sleep(.2)
            self.next_frame()
            self.wait_until(self._can_continue_quest_input, time_out=60, raise_if_not_found=True)
            self._stop_all_movement()
            if arguments.timing_only:
                self.verify_timing()
                return
            if arguments.camera_only:
                self.verify_camera()
                return
            output = root / "screenshots/quest-movement-verification"
            output.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(output / "before.png"), self.frame)
            self.navigation_progress.begin_recovery()
            while self.navigation_progress.recovery_step is not None:
                self._continue_navigation_recovery()
            cv2.imwrite(str(output / "after-right-recovery.png"), self.frame)
            self._read_scene_coordinates(self.frame)
            initial_coordinate = self.traversal.coordinate
            self.input_events.clear()

            # 两段实际前进之间保持 w，第二段执行自动跳跃。
            for index in range(2):
                self._apply_movement(["w"], .4, continuous=True)
                cv2.imwrite(str(output / f"after-{index + 1}.png"), self.frame)
                print("实际移动观察", index, self.last_motion, flush=True)
                if self.held_movement_keys != {"w"}:
                    raise AssertionError("连续前进期间方向键没有保持按下")
                self._read_scene_coordinates(self.frame)
            events = list(self.input_events)
            continuous_events = list(events)
            start = next(index for index, event in enumerate(events) if event[:2] == ("down", "w"))
            events = events[start:]
            if [(action, key) for action, key, _ in events] != [
                ("down", "w"), ("down", "space"), ("up", "space")
            ]:
                raise AssertionError(f"实际连续前进与跳跃的按键顺序不符合要求：{events}")
            if events[1][2] - events[0][2] < .18 or events[2][2] - events[1][2] < .075:
                raise AssertionError("实际前进准备时间或跳跃按键持续时间不足")
            coordinate = self.traversal.coordinate
            if initial_coordinate is None or coordinate is None or math.dist(initial_coordinate, coordinate) < 1:
                raise AssertionError("实际游戏坐标没有确认连续前进的位移")
            self._stop_all_movement()
            if self.held_movement_keys:
                raise AssertionError("停止移动后仍然保留方向键")

            self.wait_until(self._can_continue_quest_input, time_out=60, raise_if_not_found=True)
            self._read_scene_coordinates(self.frame)
            self.input_events.clear()
            initial_coordinate = self.traversal.coordinate
            if initial_coordinate is None:
                raise AssertionError("实际跳跃验证前无法读取游戏坐标")
            self._apply_movement(["w"], .45, jump=True)
            self._read_scene_coordinates(self.frame)
            events = list(self.input_events)
            start = next(index for index, event in enumerate(events) if event[:2] == ("down", "w"))
            sequence = events[start:]
            if [(action, key) for action, key, _ in sequence] != [
                ("down", "w"), ("down", "space"), ("up", "space"), ("up", "w")
            ]:
                raise AssertionError(f"实际跳跃的按键顺序不符合要求：{sequence}")
            if sequence[1][2] - sequence[0][2] < .18 or sequence[3][2] - sequence[2][2] < .65:
                raise AssertionError("实际跳跃前后持续前进的时间不足")
            coordinate = self.traversal.coordinate
            if coordinate is None or math.dist(initial_coordinate, coordinate) < 1:
                raise AssertionError("实际游戏坐标没有确认跳跃期间的位移")
            cv2.imwrite(str(output / "after-explicit-jump.png"), self.frame)
            (output / "input-events.json").write_text(json.dumps({
                "continuous": self.quest_scene.movement_actions,
                "continuous_events": continuous_events,
                "explicit_jump": sequence,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            self.verified = True
            print("实际游戏连续前进、两步一跳和跳跃期间保持前进检查通过", flush=True)
        finally:
            self._stop_all_movement()
            frame = self.executor.nullable_frame()
            if frame is not None:
                cv2.imwrite(str(root / "screenshots/quest-verification-final.png"), frame)


if not is_admin():
    raise PermissionError("实际游戏移动验证需要管理员权限")

runtime_config = deepcopy(config)
runtime_config.update({
    "check_mutex": False,
    "gui": None,
    "onetime_tasks": [["__main__", "MovementVerificationTask"]],
    "trigger_tasks": [],
    "log_file": "logs/quest-movement-verification.log",
})
runtime = OK(runtime_config)
try:
    task = runtime.get_onetime_task(MovementVerificationTask)
    runtime.run_onetime_task(task)
    if not task.verified:
        raise AssertionError(f"实际移动验证没有完成：{task.info}")
finally:
    runtime.quit()
