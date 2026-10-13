from copy import deepcopy
import argparse
from pathlib import Path
import sys
import threading
import json
import re
import shutil
import time

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

report_path = Path(__file__).resolve().parent.parent / "cache/quest-runtime-report.txt"
report_path.parent.mkdir(exist_ok=True)
if report_path.exists():
    previous_report = report_path.with_name(f"quest-runtime-{report_path.stat().st_mtime_ns}.txt")
    shutil.copy2(report_path, previous_report)
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
from src.utils.QuestAreaSearch import minimap_box, minimap_visible
from tests.combat_verification_support import BossVerificationPreparation


parser = argparse.ArgumentParser()
parser.add_argument("--expect", choices=("area", "movement", "jump", "combat", "rotation", "equipment", "prepared"), default="area")
parser.add_argument("--seconds", type=int, default=90)
parser.add_argument("--keep-front", action="store_true")
parser.add_argument("--nest", type=int, choices=(1, 2, 3))
parser.add_argument("--profile-suffix")
parser.add_argument("--resume", action="store_true")
parser.add_argument("--heal", action="store_true")
parser.add_argument("--approach", action="store_true")
parser.add_argument("--boss", action="store_true")
parser.add_argument("--boss-name", choices=("无冠者", "无归的谬误", "飞廉之猩", "时序命定之争", "无序边境之火", "无冠巨像之心"), default="无冠者")
parser.add_argument("--prepare-only", action="store_true")
parser.add_argument("--equipment", action="store_true")
parser.add_argument("--weapon-action", choices=("inspect", "list", "preview", "weak", "restore", "state"), default="inspect")
parser.add_argument("--equipment-slot", type=int, choices=(1, 2, 3), default=2)
arguments = parser.parse_args()
if (arguments.nest or arguments.boss or arguments.equipment) and not arguments.profile_suffix:
    parser.error("传送验证需要指定 --profile-suffix")
if arguments.nest and arguments.boss:
    parser.error("一次验证只能选择一个战斗地点")
if not is_admin():
    raise PermissionError("实际游戏运行验证需要使用管理员权限启动")

class ResumeQuestTask(BossVerificationPreparation, QuestStoryTask):
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
        self.prepare_boss(arguments.profile_suffix, arguments.boss_name,
                          report_path.parent / 'quest-boss-book.png', heal=arguments.heal)
        if arguments.prepare_only:
            self.info_set("Battle prepared", True)
            return
        self.run_until(lambda: self.has_target() or self.has_health_bar(), "w", time_out=10,
                       target=True, raise_if_not_found=True)
        completed = False
        try:
            QuestStoryTask.run(self)
            completed = True
        finally:
            if not completed:
                self.skip_combat_check = True
                self.wait_until(lambda: self.in_team()[0], time_out=15, raise_if_not_found=False)
                self.send_key("esc", after_sleep=.3)


class PrepareEquipmentTask(ResumeQuestTask):
    def ensure_equipment_world(self):
        for _ in range(6):
            self.next_frame()
            if minimap_visible(self.frame) and self.in_team()[0]:
                return
            self.send_key("esc", after_sleep=.8)
        raise RuntimeError("装备操作前没有返回游戏世界")

    def run(self):
        WWOneTimeTask.run(self)
        self.ensure_equipment_world()
        self.ensure_main(time_out=30)
        profiles = self.ocr(.8, .94, 1, 1, match=re.compile(r'\d{7,}'))
        if len(profiles) != 1 or not normalize_profile_code(profiles[0].name).endswith(arguments.profile_suffix):
            raise RuntimeError("游戏特征码没有匹配指定账号")
        self.send_key(arguments.equipment_slot, after_sleep=.5)
        self.send_key("c", after_sleep=2)
        self.click(.04, .30, after_sleep=1)
        if arguments.weapon_action == "state":
            self.wait_ocr(0, 0, .2, .15, match="武器", time_out=5, raise_if_not_found=True)
            self.next_frame()
            print("当前装备页面", arguments.equipment_slot, self.ocr(0, 0, .4, 1), flush=True)
            self.info_set("Equipment inspected", True)
            return
        expected_weapon = "原初迅刀" if arguments.weapon_action == "restore" else "云琅"
        self.wait_ocr(0, 0, .7, 1, match=re.compile(re.escape(expected_weapon)), time_out=5, raise_if_not_found=True)
        if arguments.weapon_action in ("list", "preview", "weak", "restore"):
            replace = self.wait_ocr(0, .7, .35, 1, match="替换", time_out=5, raise_if_not_found=True)
            self.click(replace[0], after_sleep=1)
            if arguments.weapon_action in ("preview", "weak"):
                self.click(.208, .688, after_sleep=1)
            elif arguments.weapon_action == "restore":
                self.click(.07, .23, after_sleep=1)
            if arguments.weapon_action in ("weak", "restore"):
                selected_weapon = "云琅" if arguments.weapon_action == "restore" else "原初迅刀"
                self.wait_ocr(.7, .1, 1, .3, match=re.compile(re.escape(selected_weapon)), time_out=5, raise_if_not_found=True)
                selected_level = re.compile(r"60\s*/\s*60" if arguments.weapon_action == "restore" else r"1\s*/\s*20")
                self.wait_ocr(.7, .1, 1, .3, match=selected_level, time_out=5, raise_if_not_found=True)
                replace = self.wait_ocr(.72, .82, .84, 1, match="替换", time_out=5, raise_if_not_found=True)
                self.click(replace[0], after_sleep=1)
                self.ensure_equipment_world()
                self.send_key("2", after_sleep=.5)
                self.send_key("c", after_sleep=2)
                self.click(.04, .30, after_sleep=1)
                self.wait_ocr(0, 0, .35, 1, match=re.compile(re.escape(selected_weapon)), time_out=5, raise_if_not_found=True)
                self.wait_ocr(0, .15, .35, .35, match=selected_level, time_out=5, raise_if_not_found=True)
                print("已确认清宵装备", selected_weapon, flush=True)
        self.next_frame()
        print("装备页面", self.ocr(), flush=True)
        self.info_set("Equipment inspected", True)


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
if arguments.nest or arguments.boss or arguments.equipment:
    runtime_config["onetime_tasks"] += [
        ["__main__", "PrepareEquipmentTask" if arguments.equipment else "PrepareQuestBossTask" if arguments.boss else "PrepareQuestNestTask"],
        ["src.task.MultiAccountDailyTask", "MultiAccountDailyTask"],
        ["src.task.NightmareNestTask", "NightmareNestTask"],
    ]
runtime = OK(runtime_config)
task_class = PrepareEquipmentTask if arguments.equipment else PrepareQuestBossTask if arguments.boss else PrepareQuestNestTask if arguments.nest else ResumeQuestTask if arguments.resume else QuestStoryTask
task = runtime.get_onetime_task(task_class)
if arguments.nest or arguments.boss or arguments.resume or arguments.equipment:
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
    elif arguments.expect == "equipment" and not task.info.get("Equipment inspected"):
        raise AssertionError("没有确认装备页面")
    elif arguments.expect == "prepared" and not task.info.get("Battle prepared"):
        raise AssertionError("没有确认挑战准备完成")
finally:
    timer.cancel()
    if runtime.task_executor._frame is not None:
        cv2.imwrite(str(frame_path), runtime.task_executor._frame)
    print("区域搜索状态", task.area_search.context() if task.area_search is not None else None, flush=True)
    print("实际移动观察", observations, flush=True)
    runtime.exit_event.set()
    recorder.join()
    runtime.quit()
