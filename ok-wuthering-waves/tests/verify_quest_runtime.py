from copy import deepcopy
import argparse
from pathlib import Path
import sys
import threading
import json
import re
import time

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

report_path = Path(__file__).resolve().parent.parent / "cache/quest-runtime-report.txt"
report_path.parent.mkdir(exist_ok=True)
sys.stdout = report_path.open("w", encoding="utf-8", buffering=1)
sys.stderr = sys.stdout
sys.argv[0] = str(Path(__file__).resolve().parent.parent / "main.py")

from config import config
from ok import OK
from ok.util.process import is_admin
from src.task.QuestStoryTask import QuestStoryTask
from src.task.WWOneTimeTask import WWOneTimeTask
from src.task.MultiAccountDailyTask import MultiAccountDailyTask, normalize_profile_code
from src.task.NightmareNestTask import NightmareNestTask, NestTarget, TRAVEL_FEATURES
from src.utils.QuestAreaSearch import minimap_box


parser = argparse.ArgumentParser()
parser.add_argument("--expect", choices=("area", "movement", "jump", "combat", "rotation"), default="area")
parser.add_argument("--seconds", type=int, default=90)
parser.add_argument("--keep-front", action="store_true")
parser.add_argument("--nest", type=int, choices=(1, 2, 3))
parser.add_argument("--profile-suffix")
parser.add_argument("--resume", action="store_true")
parser.add_argument("--heal", action="store_true")
parser.add_argument("--approach", action="store_true")
parser.add_argument("--boss", action="store_true")
parser.add_argument("--boss-name", choices=("无冠者", "无归的谬误", "飞廉之猩", "时序命定之争"), default="无冠者")
arguments = parser.parse_args()
if (arguments.nest or arguments.boss) and not arguments.profile_suffix:
    parser.error("传送验证需要指定 --profile-suffix")
if arguments.nest and arguments.boss:
    parser.error("一次验证只能选择一个战斗地点")
if not is_admin():
    raise PermissionError("实际游戏运行验证需要使用管理员权限启动")

class ResumeQuestTask(QuestStoryTask):
    def run(self):
        WWOneTimeTask.run(self)
        self.ensure_main(time_out=30)
        if arguments.heal:
            self.heal_team()
        if arguments.approach:
            self.load_chars()
            self.seek_combat()
        super().run()

    def seek_combat(self):
        for _ in range(8):
            self.middle_click(after_sleep=.15)
            self.next_frame()
            if self.has_target() or self.has_health_bar():
                return
            self._apply_camera_turn(450)
            self.sleep(.15)
        self.run_until(lambda: self.has_target() or self.has_health_bar(), "w", time_out=3,
                       target=True, raise_if_not_found=True)

    def heal_team(self):
        self.send_key("m", after_sleep=2)
        controls = self.ocr(.6, .7, 1, 1, match="切换地图")
        if len(controls) != 1:
            raise RuntimeError("地图没有显示切换地图按钮")
        self.click(controls[0], after_sleep=2)
        regions = self.ocr(.4, 0, .75, 1, match=re.compile("今州"))
        if len(regions) != 1:
            raise RuntimeError("地图列表没有唯一的今州入口")
        self.click(regions[0], after_sleep=2)
        districts = self.ocr(.7, .1, 1, .9, match="云陵谷")
        if len(districts) != 1:
            raise RuntimeError("今州地图没有唯一的云陵谷入口")
        self.click(districts[0], after_sleep=2)
        self.click(.4, .5, after_sleep=1)
        landmarks = self.ocr(.4, .35, .5, .45, match="夜明坡")
        if len(landmarks) != 1:
            raise RuntimeError("今州地图没有显示已确认的夜明坡治疗位置")
        self.click(.485, .315, after_sleep=1)
        travel = self.wait_feature("gray_teleport", time_out=5, raise_if_not_found=True)
        self.click(travel, after_sleep=2)
        self.wait_in_team_and_world(time_out=120, raise_if_not_found=True)

class PrepareQuestNestTask(ResumeQuestTask):
    def run(self):
        WWOneTimeTask.run(self)
        self.ensure_main(time_out=30)
        accounts = self.executor.get_task_by_class(MultiAccountDailyTask)
        profile = accounts._detect_account_from_world_profile()
        if not profile or not normalize_profile_code(profile).endswith(arguments.profile_suffix):
            raise RuntimeError("游戏特征码没有匹配指定账号")
        print("账号特征码核对通过", flush=True)
        if arguments.heal:
            self.heal_team()
        self.load_chars()
        if {type(char).__name__ for char in self.chars if char is not None} != {"Qingxiao", "Sanhua", "Verina"}:
            raise RuntimeError("入场前角色识别没有匹配指定三人队伍")
        print("入场前队伍核对通过", [(type(char).__name__, type(char).__module__) for char in self.chars], flush=True)
        nest = self.executor.get_task_by_class(NightmareNestTask)
        nest.openF2Book("gray_book_boss")
        nest.go_nest()
        rows = sorted(nest.ocr(.35, .13, 1, .96, match=nest.count_re), key=lambda box: box.y)
        if len(rows) < arguments.nest:
            raise RuntimeError("残像聚落列表没有显示指定行")
        if not cv2.imwrite(str(report_path.parent / f"quest-nest-{arguments.nest}-book.png"), nest.frame):
            raise RuntimeError("保存残像聚落列表画面失败")
        target = deepcopy(rows[arguments.nest - 1])
        target.x = nest.width_of_screen(.9)
        target.y -= target.height * .9
        target.width = target.height = 1
        nest.click(target, after_sleep=2)
        feature = nest.wait_feature(TRAVEL_FEATURES + ["team_start_challenge"], time_out=10,
                                    settle_time=.5, raise_if_not_found=True)
        if feature.name == "team_start_challenge":
            nest.click_team_challenge()
            nest.wait_in_team_and_world(time_out=120)
        elif not nest._travel_to_nest_or_skip(NestTarget(target, f"verification:{arguments.nest}")):
            raise RuntimeError("指定残像聚落传送没有完成")
        print(f"已传送至第 {arguments.nest} 个残像聚落", flush=True)
        for _ in range(3):
            if not nest.find_f_with_text():
                break
            nest.send_key("f", after_sleep=1)
            nest.wait_in_team_and_world(time_out=40, raise_if_not_found=True)
        self.seek_combat()
        QuestStoryTask.run(self)

class PrepareQuestBossTask(ResumeQuestTask):
    def run(self):
        WWOneTimeTask.run(self)
        self.ensure_main(time_out=30)
        accounts = self.executor.get_task_by_class(MultiAccountDailyTask)
        profile = accounts._detect_account_from_world_profile()
        if not profile or not normalize_profile_code(profile).endswith(arguments.profile_suffix):
            raise RuntimeError("游戏特征码没有匹配指定账号")
        if arguments.heal:
            self.heal_team()
        self.load_chars()
        if {type(char).__name__ for char in self.chars if char is not None} != {"Qingxiao", "Sanhua", "Verina"}:
            raise RuntimeError("入场前角色识别没有匹配指定三人队伍")
        self.openF2Book("gray_book_boss")
        weekly = arguments.boss_name == "时序命定之争"
        self.open_boss_book("zhange" if weekly else "qiangdi")
        for _ in range(40):
            names = self.ocr(.36, .1, .8, .95, match=re.compile(re.escape(arguments.boss_name)))
            if names:
                if names[0].y > self.height_of_screen(.7):
                    self.scroll_relative(.8, .6, -3)
                    self.sleep(.3)
                    continue
                break
            self.scroll_relative(.8, .6, -8)
            self.sleep(.3)
        else:
            raise RuntimeError(f"敌人列表没有显示{arguments.boss_name}")
        if not cv2.imwrite(str(report_path.parent / "quest-boss-book.png"), self.frame):
            raise RuntimeError("保存敌人列表画面失败")
        buttons = self.find_feature("boss_proceed", box=self.box_of_screen(.84, .1, 1, .95), threshold=.8)
        if not buttons:
            raise RuntimeError("敌人列表没有前往按钮")
        button = min(buttons, key=lambda box: abs(box.y - names[0].y))
        if abs(button.y - names[0].y) > self.height_of_screen(.1):
            raise RuntimeError(f"{arguments.boss_name}与前往按钮没有匹配同一行")
        self.click(button, after_sleep=1)
        if weekly:
            levels = self.wait_ocr(.03, .113, .323, .66, match=re.compile("60"),
                                   time_out=10, raise_if_not_found=True)
            self.click(levels[0], after_sleep=1)
            self.click(.88, .911, after_sleep=2)
        feature = self.wait_feature(TRAVEL_FEATURES + ["team_start_challenge"], time_out=10,
                                    raise_if_not_found=True)
        if feature.name == "team_start_challenge":
            self.click_team_challenge()
        else:
            self.wait_click_travel()
        self.wait_in_team_and_world(time_out=120, raise_if_not_found=True)
        self.run_until(lambda: self.has_target() or self.has_health_bar(), "w", time_out=10,
                       target=True, raise_if_not_found=True)
        QuestStoryTask.run(self)


runtime_config = deepcopy(config)
runtime_config.update({
    "check_mutex": False,
    "gui": None,
    "onetime_tasks": [["src.task.MouseResetTask", "MouseResetTask"], ["src.task.QuestStoryTask", "QuestStoryTask"]],
    "trigger_tasks": [],
    "log_file": "logs/quest-verification.log",
})
if arguments.resume:
    runtime_config["onetime_tasks"].append(["__main__", "ResumeQuestTask"])
if arguments.nest or arguments.boss:
    runtime_config["onetime_tasks"] += [
        ["__main__", "PrepareQuestBossTask" if arguments.boss else "PrepareQuestNestTask"],
        ["src.task.MultiAccountDailyTask", "MultiAccountDailyTask"],
        ["src.task.NightmareNestTask", "NightmareNestTask"],
    ]
runtime = OK(runtime_config)
task_class = PrepareQuestBossTask if arguments.boss else PrepareQuestNestTask if arguments.nest else ResumeQuestTask if arguments.resume else QuestStoryTask
task = runtime.get_onetime_task(task_class)
if arguments.nest or arguments.boss or arguments.resume:
    task.config = runtime.get_onetime_task(QuestStoryTask).config
if task.decision_session is None or task.navigation_progress is None or task.config is None:
    raise RuntimeError("剧情任务初始化没有完成")
print("剧情任务及实际运行环境初始化完成", flush=True)
timer = threading.Timer(arguments.seconds, runtime.exit_event.set)
frame_path = report_path.parent / "quest-verification.png"
frame_path.parent.mkdir(exist_ok=True)
capture_directory = frame_path.parent / "quest-area-capture" / time.strftime("%Y%m%d-%H%M%S")
capture_directory.mkdir(parents=True, exist_ok=True)
observations = {"initial_distance": None, "closest_distance": None, "goal": None, "movement_actions": 0,
                "movement_observed": False, "jump_attempted": False, "combat_completed": False,
                "resumed_after_combat": False, "foreground_error": None, "rotation_round": 0,
                "rotation_states": []}
expected_rotation_states = [(0, step) for step in range(3)]
expected_rotation_states += [(round_index, step) for round_index in (1, 2) for step in range(4)]
expected_rotation_states.append((3, 0))


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
            observations["movement_actions"] = task.quest_scene.movement_actions
            if area is None and distance is not None:
                if observations["initial_distance"] is None or observations["goal"] != task.guidance_text:
                    observations["initial_distance"] = distance
                    observations["closest_distance"] = distance
                    observations["goal"] = task.guidance_text
                observations["closest_distance"] = distance if observations["closest_distance"] is None else min(distance, observations["closest_distance"])
                if observations["initial_distance"] - distance >= .5 and observations["movement_actions"] > 0:
                    observations["movement_observed"] = True
            observations["jump_attempted"] |= task.navigation_progress.jump_attempts > 0
            for character in task.chars:
                rotation = getattr(character, "_team_rotation", None)
                if rotation is not None:
                    observations["rotation_round"] = max(observations["rotation_round"], rotation.round)
                    state = (rotation.round, rotation.step)
                    states = observations["rotation_states"]
                    if not states or states[-1] != state:
                        states.append(state)
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
            elif arguments.expect == "rotation" and observations["rotation_states"] == expected_rotation_states:
                runtime.exit_event.set()
            elif arguments.expect == "rotation" and observations["resumed_after_combat"]:
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
    if "Error" in task.info:
        raise RuntimeError(task.info["Error"] or "剧情任务报告了未命名异常")
    if arguments.expect == "area":
        if task.area_search is None or task.area_search.index == 0:
            raise AssertionError("限时运行尚未到达任务区域中心")
    elif arguments.expect == "movement" and not observations["movement_observed"]:
        raise AssertionError("限时运行没有确认目标距离缩减")
    elif arguments.expect == "jump" and (not observations["movement_observed"] or not observations["jump_attempted"]):
        raise AssertionError("限时运行没有确认跳跃尝试及目标距离缩减")
    elif arguments.expect == "combat" and not observations["resumed_after_combat"]:
        raise AssertionError("限时运行尚未确认战斗结束后继续剧情或导航")
    elif arguments.expect == "rotation" and observations["rotation_states"] != expected_rotation_states:
        raise AssertionError("限时运行尚未确认启动轴和连续两次循环")
finally:
    timer.cancel()
    if runtime.task_executor._frame is not None:
        cv2.imwrite(str(frame_path), runtime.task_executor._frame)
    print("区域搜索状态", task.area_search.context() if task.area_search is not None else None, flush=True)
    print("实际移动观察", observations, flush=True)
    runtime.exit_event.set()
    recorder.join()
    runtime.quit()
