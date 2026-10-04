import os
import re
import time
from typing import Optional

import numpy as np
from ok import Box, Logger
from src.task.BaseCombatTask import BaseCombatTask
from src.task.SkipBaseTask import SkipBaseTask
from src.task.WWOneTimeTask import WWOneTimeTask
from src.utils.QuestDecisionEngine import QuestAction, decide_quest_action
from src.utils.QuestNavigator import calculate_camera_turn, compute_movement_action
from src.utils.QuestOcrPrivacy import prepare_quest_ocr_frame, sanitize_quest_text
from src.utils.QuestProgressTracker import QuestProgressTracker
from src.utils.QuestVision import (
    BeaconResult,
    ClimbStateResult,
    detect_climbing_state,
    detect_dialog_advance_indicator,
    detect_interact_action,
    detect_letterbox,
    detect_minimap_quest_arrow,
    detect_quest_beacon,
    detect_screen_freeze,
    detect_top_left_skip_button,
    parse_distance_text,
)

logger = Logger.get_logger(__name__)


class QuestStoryTask(WWOneTimeTask, BaseCombatTask, SkipBaseTask):
    owns_switch_healer_config = True

    STATE_IDLE = "IDLE"
    STATE_DIALOG = "DIALOG"
    STATE_LETTERBOX_CUTSCENE = "LETTERBOX_CUTSCENE"
    STATE_COMBAT = "COMBAT"
    STATE_NAVIGATE = "NAVIGATE"
    STATE_DECIDE_INTERACT = "DECIDE_INTERACT"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "📜 Enhanced Quest Story Mode"
        self.description = "自动跳过剧情对话、剧情战斗自动输出、自动寻路跟随黄色任务信标与智能交互决策"
        self.default_config = {
            "_enabled": True,
            "Auto Combat in Quest": True,
            "Auto Skip Dialog": True,
            "Letterbox Freeze Wait Seconds": 30.0,
            "API URL": "https://api.typesafe.ai/v1/systemone",
            "API Key": "",
            "Camera Sensitivity": 1.0,
            "Switch to First Character for Movement": True,
        }
        self.config_description = {
            "Auto Combat in Quest": "剧情期间遭遇敌人自动进入战斗",
            "Auto Skip Dialog": "剧情对话期间自动跳过",
            "Letterbox Freeze Wait Seconds": "上下黑边剧情动画持续静止触发交互决策的等待秒数",
            "API URL": "jev 文字决策 API 地址",
            "API Key": "jev 授权密钥",
            "Camera Sensitivity": "镜头旋转灵敏度系数",
            "Switch to First Character for Movement": "移动寻路期间固定切换至一号位角色",
        }
        self.current_state = self.STATE_IDLE
        self.last_frame: Optional[np.ndarray] = None
        self.letterbox_freeze_start_time: float = 0.0
        self.last_action_time: float = 0.0
        self.last_search_log_time: float = 0.0
        self.last_char_switch_time: float = 0.0
        self.climbing_start_time: float = 0.0
        self.last_nav_frame: Optional[np.ndarray] = None
        self.navigation_movement_pending = False
        self.navigation_progress = QuestProgressTracker()
        self.stuck_start_time: float = 0.0
        self.last_observed_distance: Optional[float] = None
        self.stuck_count: int = 0
        self.last_teleport_attempt_time: float = 0.0
        self.jev_call_count: int = 0
        self.jev_total_tokens: int = 0
        self.jev_cost_estimate: float = 0.0

    def is_game_window_active(self) -> bool:
        try:
            import win32gui
            fg_hwnd = win32gui.GetForegroundWindow()
            if not fg_hwnd:
                return False

            hwnd_obj = getattr(self, "hwnd", None)
            if hwnd_obj is not None:
                if hasattr(hwnd_obj, "is_foreground") and hwnd_obj.is_foreground():
                    return True
                target_hwnd = getattr(hwnd_obj, "hwnd", 0)
                if target_hwnd and fg_hwnd == target_hwnd:
                    return True

            fg_title = win32gui.GetWindowText(fg_hwnd)
            if any(game_name in fg_title for game_name in ("鸣潮", "Wuthering Waves", "鳴潮")):
                return True

            return False
        except Exception:
            return True

    def run(self):
        try:
            WWOneTimeTask.run(self)
        except Exception as e:
            logger.warning(f"初始化任务执行环境异常: {e}")

        self.log_info("剧情模式已启动，已激活游戏窗口并重置鼠标位置")
        self.current_state = self.STATE_IDLE
        self.last_frame = None
        self.letterbox_freeze_start_time = 0.0
        self.last_search_log_time = 0.0
        self.climbing_start_time = 0.0
        self.navigation_movement_pending = False
        self.navigation_progress = QuestProgressTracker()
        self.last_nav_frame = None
        self.stuck_start_time = 0.0
        self.last_observed_distance = None
        self.jev_call_count = 0
        self.jev_total_tokens = 0
        self.jev_cost_estimate = 0.0
        self.info_set("JEV 调用次数", "0 次")
        self.info_set("JEV 额度消耗", "0 tokens")

        while not self.executor.paused:
            self.sleep(0.05)
            if not self.is_game_window_active():
                self._stop_all_movement()
                self.sleep(0.2)
                continue

            frame = self.frame
            if frame is None:
                continue

            # 1. 优先判定剧情对话
            if self.config.get("Auto Skip Dialog", True):
                if self.check_skip():
                    self.current_state = self.STATE_DIALOG
                    self.letterbox_freeze_start_time = 0.0
                    self.log_info("检测到剧情跳过选项，点击执行跳过")
                    self.sleep(0.2)
                    continue

                top_left_skip = detect_top_left_skip_button(frame)
                if top_left_skip.found:
                    self.current_state = self.STATE_DIALOG
                    self.letterbox_freeze_start_time = 0.0
                    self.log_info(
                        f"检测到左上角剧情跳过按钮 (置信度 {top_left_skip.confidence:.2f})，点击执行跳过"
                    )
                    skip_box = Box(
                        top_left_skip.x,
                        top_left_skip.y,
                        top_left_skip.width,
                        top_left_skip.height,
                        confidence=top_left_skip.confidence,
                        name="top_left_skip_dialog"
                    )
                    self.click_box(skip_box, down_time=0.05, after_sleep=0.3)
                    self.wait_until(self.skip_confirm, time_out=3.0, raise_if_not_found=False)
                    self.sleep(0.2)
                    continue

                if self.skip_message():
                    self.current_state = self.STATE_DIALOG
                    self.letterbox_freeze_start_time = 0.0
                    self.log_info("检测到短消息对话，点击推进")
                    self.sleep(0.2)
                    continue

                advance_result = detect_dialog_advance_indicator(frame)
                if advance_result.found:
                    self.current_state = self.STATE_DIALOG
                    self.letterbox_freeze_start_time = 0.0
                    self.log_info(
                        f"检测到剧情对白推进标识 (置信度 {advance_result.confidence:.2f})，点击界面进入下一段对话"
                    )
                    self.click(0.5, 0.8)
                    self.sleep(0.25)
                    continue

            # 2. 判定进入战斗状态
            if self.config.get("Auto Combat in Quest", True):
                if self.in_combat():
                    self.current_state = self.STATE_COMBAT
                    self.letterbox_freeze_start_time = 0.0
                    self.log_info("检测到进入战斗状态，交由角色战斗执行器执行操作")
                    self.get_current_char().perform()
                    continue

            # 3. 判定上下黑边剧情动画
            letterbox_info = detect_letterbox(frame)
            if letterbox_info.is_letterbox:
                self.current_state = self.STATE_LETTERBOX_CUTSCENE
                self._handle_letterbox_state(frame)
                continue
            else:
                self.letterbox_freeze_start_time = 0.0

            # 4. 判定大世界任务导航与交互
            self._handle_world_navigation_and_interaction(frame)

    def _handle_letterbox_state(self, frame: np.ndarray):
        now = time.time()
        if self.last_frame is not None and self.last_frame.shape == frame.shape:
            is_frozen, _ = detect_screen_freeze(self.last_frame, frame, diff_threshold=2.0)
            if is_frozen:
                if self.letterbox_freeze_start_time == 0.0:
                    self.letterbox_freeze_start_time = now
                freeze_duration = now - self.letterbox_freeze_start_time
                wait_limit = float(self.config.get("Letterbox Freeze Wait Seconds", 30.0))
                if freeze_duration >= wait_limit:
                    self.log_info(
                        f"黑边动画静止已达 {freeze_duration:.1f} 秒，触发交互决策"
                    )
                    self._trigger_ai_decision(frame, is_frozen_letterbox=True)
                    self.letterbox_freeze_start_time = now
            else:
                self.letterbox_freeze_start_time = 0.0
        else:
            self.letterbox_freeze_start_time = 0.0

        self.last_frame = frame.copy()

    def _handle_world_navigation_and_interaction(self, frame: np.ndarray):
        if not self.is_game_window_active():
            self._stop_all_movement()
            return

        height, width = frame.shape[:2]

        # 0. 优先检测是否处于攀爬状态
        climb_result = detect_climbing_state(frame)
        if climb_result.is_climbing:
            now = time.time()
            if self.climbing_start_time == 0.0:
                self.climbing_start_time = now
            climbing_duration = now - self.climbing_start_time
            self._handle_climbing_escape(frame, climbing_duration)
            return
        else:
            self.climbing_start_time = 0.0

        # 1. 检查是否存在 F 键交互
        has_f = False
        action_text = ""
        try:
            if hasattr(self, "find_f_with_text") and self.find_f_with_text() is not None:
                has_f = True
        except Exception:
            pass

        if not has_f:
            interact_result = detect_interact_action(frame)
            if interact_result.has_f:
                has_f = True
                action_text = interact_result.action_text

        # 2. 提取任务信标与目标距离
        beacon_result = detect_quest_beacon(frame)
        current_distance = self._extract_quest_distance(frame, beacon_result)
        self.navigation_progress.observe(current_distance)

        # 3. 若任务目标距离超过 200 米，尝试打开地图定位并传送到附近传送点
        if current_distance is not None and current_distance > 200.0:
            if self._try_teleport_to_nearest_waypoint(current_distance):
                return

        # 4. 判定是否属于已到达任务点附近的交互
        arrow_result = detect_minimap_quest_arrow(frame)
        is_near_goal = False

        if current_distance is not None:
            if current_distance <= 3.0:
                is_near_goal = True
        else:
            if not arrow_result.found:
                is_near_goal = True

        if has_f:
            if is_near_goal:
                self._stop_all_movement()
                self.navigation_progress = QuestProgressTracker()
                self.current_state = self.STATE_DECIDE_INTERACT
                self.log_info("已到达任务目标附近且出现交互按键 [F]，执行交互推进")
                self._trigger_ai_decision(
                    frame,
                    has_f_button=True,
                    action_text=action_text
                )
                self.sleep(1.2)
                return
            else:
                dist_info = f"当前距离任务目标还有 {current_distance:.1f} 米" if current_distance is not None else "任务目标仍在远方"
                self.log_info(f"检测到路过交互 [F] ({dist_info})，忽略路过交互继续前进寻路")

        # 4. 寻路导航状态
        self.current_state = self.STATE_NAVIGATE
        sensitivity = float(self.config.get("Camera Sensitivity", 1.0))

        if self.config.get("Switch to First Character for Movement", True):
            self._ensure_first_character()

        # 5. 到达 1 至 2 米范围内停止移动并等待交互
        if current_distance is not None and current_distance <= 2.0:
            self._stop_all_movement()
            self.log_info(f"已到达任务目标附近 (距离 {current_distance:.1f} 米)，停止移动，等待交互触发")
            if beacon_result.found:
                beacon_cx = beacon_result.x + beacon_result.width // 2
                turn_cmd = calculate_camera_turn(
                    screen_width=width,
                    beacon_center_x=beacon_cx,
                    camera_sensitivity=sensitivity,
                    max_delta_x=60
                )
                if turn_cmd.need_turn:
                    self._apply_camera_turn(turn_cmd.delta_x_pixels)
            self.sleep(0.4)
            return

        # 绕行期间保持移动方向，完成整个过程后恢复信标导航。
        if self.navigation_progress.recovery_step is not None:
            self._continue_navigation_recovery()
            return
        if self.navigation_progress.blocked:
            self.log_info(f"累计前进 {self.navigation_progress.movement_seconds:.1f} 秒后任务距离没有缩减，执行侧向绕行")
            self._handle_stuck_recovery(frame)
            return

        # 7. 检查短时间贴墙受阻卡滞
        if self._check_and_handle_stuck(frame, current_distance):
            return

        # 8. 视野中存在任务信标
        if beacon_result.found:
            beacon_cx = beacon_result.x + beacon_result.width // 2
            diff_x = beacon_cx - width / 2.0
            angle_error_deg = (diff_x / (width / 2.0)) * 45.0

            turn_cmd = calculate_camera_turn(
                screen_width=width,
                beacon_center_x=beacon_cx,
                camera_sensitivity=sensitivity,
                max_delta_x=100
            )
            if turn_cmd.need_turn:
                self._apply_camera_turn(turn_cmd.delta_x_pixels)
                self.sleep(0.1)
                return

            effective_distance = current_distance if current_distance is not None else 10.0
            move_cmd = compute_movement_action(
                distance_meters=effective_distance,
                angle_error_deg=angle_error_deg
            )

            # 角度误差较大时原地校准视角，严禁边转向边移动以避免环绕画圈
            if move_cmd.mode == "wait":
                self.log_info(f"正在原地校准视角朝向信标 (角度误差 {angle_error_deg:.1f}°)")
                self.sleep(0.1)
                return

            if move_cmd.mode == "walk":
                dist_str = f"{current_distance:.1f} 米" if current_distance is not None else "近距离"
                self.log_info(f"距离目标 {dist_str} (小于等于20米)，采用慢走模式平稳推进")
            elif move_cmd.mode == "sprint":
                dist_str = f"{current_distance:.1f} 米" if current_distance is not None else "远距离"
                self.log_info(f"距离目标 {dist_str}，快跑冲刺快速推进")

            self._apply_movement(move_cmd.keys, move_cmd.press_duration)
            return

        # 9. 视野无信标，依据小地图指示箭头旋转镜头并移动
        arrow_result = detect_minimap_quest_arrow(frame)
        if arrow_result.found:
            norm_deg = arrow_result.bearing_deg % 360.0
            angle_diff = norm_deg - 360.0 if norm_deg > 180.0 else norm_deg

            turn_cmd = calculate_camera_turn(
                screen_width=width,
                minimap_bearing_deg=arrow_result.bearing_deg,
                camera_sensitivity=sensitivity,
                max_delta_x=120
            )
            if turn_cmd.need_turn:
                self._apply_camera_turn(turn_cmd.delta_x_pixels)
                self.sleep(0.1)
                return

            # 若角度误差超过 20 度，原地旋转镜头，严禁边转向边移动以避免环绕画圈
            if abs(angle_diff) > 20.0:
                self.log_info(f"依据小地图指引原地调整朝向 (方位角 {arrow_result.bearing_deg:.1f}°)")
                self.sleep(0.1)
                return

            self.log_info("已朝向小地图任务目标，向前慢步推进")
            self._apply_movement(["w"], 0.25)
            return

        # 10. 视野与小地图暂无目标标识，原地水平旋转视角搜寻信标，避免盲目前冲撞击墙体
        now = time.time()
        if now - self.last_search_log_time > 2.0:
            self.last_search_log_time = now
            self.log_info("视野暂未发现任务信标，正在原地水平旋转视角搜寻目标方位...")
        self._apply_camera_turn(120)
        self.sleep(0.2)

    def _handle_climbing_escape(self, frame: np.ndarray, climbing_duration: float = 0.0):
        self._stop_all_movement()
        self.log_info(
            f"检测到角色处于攀爬状态 (持续 {climbing_duration:.1f} 秒)，发送 [X] 键脱离攀爬并后撤调整路线"
        )
        self.send_key("x", down_time=0.1)
        self.sleep(0.35)
        self._handle_stuck_recovery(frame)

    def _try_teleport_to_nearest_waypoint(self, current_distance: float) -> bool:
        now = time.time()
        last_attempt = getattr(self, "last_teleport_attempt_time", 0.0)
        if now - last_attempt < 30.0:
            return False

        self.last_teleport_attempt_time = now
        self._stop_all_movement()
        self.log_info(
            f"任务目标距离超过 200 米 (当前 {current_distance:.1f} 米)，尝试打开地图定位并传送到附近传送点"
        )

        # 1. 发送快捷键 J 打开任务日志界面
        self.send_key("j")
        self.sleep(1.5)

        # 2. 若进入任务日志界面，点击右下角定位按钮
        if not self.in_team_and_world():
            self.click(0.89, 0.92)
            self.sleep(1.5)
        else:
            # 若未打开任务日志界面，发送快捷键 M 打开大地图
            self.send_key("m")
            self.sleep(2.0)

        # 3. 检查地图界面是否已打开
        if self.in_team_and_world():
            self.log_info("地图界面未能成功打开，继续执行常规地面寻路")
            return False

        # 4. 在地图中检测前往/快速旅行按钮或寻找附近传送信标
        travel_clicked = False
        try:
            if hasattr(self, "click_traval_button") and self.click_traval_button():
                travel_clicked = True
        except Exception:
            pass

        if not travel_clicked:
            teleport_point = self.find_best_match_in_box(
                self.box_of_screen(0.15, 0.15, 0.85, 0.85),
                ["map_way_point", "map_way_point_big"],
                0.6
            )
            if teleport_point:
                self.click(teleport_point)
                self.sleep(1.2)
                try:
                    if hasattr(self, "click_traval_button") and self.click_traval_button():
                        travel_clicked = True
                except Exception:
                    pass

        if not travel_clicked:
            self.click(0.89, 0.92)
            self.sleep(1.0)
            if hasattr(self, "click_confirm"):
                self.click_confirm()

        # 5. 等待传送加载完成并返回大世界
        self.log_info("正在执行传送，等待加载完成并返回大世界")
        self.wait_in_team_and_world(time_out=60, raise_if_not_found=False)
        self.sleep(2.0)

        # 若依然停留在菜单或地图界面，发送 ESC 键退出
        if not self.in_team_and_world():
            self.send_key("esc")
            self.sleep(1.5)

        self.navigation_progress = QuestProgressTracker()
        return True

    def _check_and_handle_stuck(self, frame: np.ndarray, current_distance: Optional[float]) -> bool:
        if not self.navigation_movement_pending:
            self.last_nav_frame = frame.copy()
            self.last_observed_distance = current_distance
            self.stuck_start_time = 0.0
            return False
        now = time.time()
        is_stuck = False
        last_nav = getattr(self, "last_nav_frame", None)
        stuck_start = getattr(self, "stuck_start_time", 0.0)
        last_dist = getattr(self, "last_observed_distance", None)

        if last_nav is not None and last_nav.shape == frame.shape:
            ch, cw = frame.shape[:2]
            center_crop_curr = frame[int(ch * 0.25):int(ch * 0.75), int(cw * 0.25):int(cw * 0.75)]
            center_crop_prev = last_nav[int(ch * 0.25):int(ch * 0.75), int(cw * 0.25):int(cw * 0.75)]
            diff_val = float(np.mean(np.abs(center_crop_curr.astype(float) - center_crop_prev.astype(float))))

            distance_not_reduced = True
            if current_distance is not None and last_dist is not None:
                if current_distance < last_dist - 0.2:
                    distance_not_reduced = False

            if diff_val < 3.0 and distance_not_reduced:
                if stuck_start == 0.0:
                    self.stuck_start_time = now
                elif now - stuck_start >= 1.2:
                    is_stuck = True
            else:
                self.stuck_start_time = 0.0
        else:
            self.stuck_start_time = 0.0

        self.last_nav_frame = frame.copy()
        if current_distance is not None:
            self.last_observed_distance = current_distance

        if is_stuck:
            self._handle_stuck_recovery(frame)
            self.stuck_start_time = 0.0
            return True

        return False

    def _handle_stuck_recovery(self, frame: np.ndarray):
        self._stop_all_movement()
        self.navigation_progress.begin_recovery()
        self.log_info(f"检测到角色前进受阻，第 {self.navigation_progress.recovery_count} 次绕行，执行后退、侧向移动与前进")
        self._continue_navigation_recovery()

    def _continue_navigation_recovery(self):
        keys, duration = self.navigation_progress.next_recovery_movement()
        self._apply_movement(keys, duration)
        self.last_nav_frame = None
        self.stuck_start_time = 0.0
        if self.navigation_progress.recovery_step is None:
            self.log_info("侧向绕行完成，重新识别任务方向与距离")

    def _stop_all_movement(self):
        for key in ["w", "a", "s", "d", "shift"]:
            try:
                self.send_key_up(key)
            except Exception:
                pass

    def _extract_quest_distance(self, frame: np.ndarray, beacon_result: BeaconResult) -> Optional[float]:
        dist_regex = re.compile(r"(\d+(?:\.\d+)?)\s*(?:米|m|M)?")
        # 1. 优先扫描左侧任务提示区域
        try:
            boxes = self.ocr(0.01, 0.18, 0.28, 0.48, match=dist_regex, frame=frame)
            if not boxes:
                # 传入 match 未匹配到时直接全量识别该区域框
                boxes = self.ocr(0.01, 0.18, 0.28, 0.48, frame=frame)
            if boxes:
                for box in boxes:
                    text = box.name or ""
                    dist = parse_distance_text(text)
                    if dist is not None:
                        return dist
        except Exception:
            pass

        # 2. 检查信标周围文字区域
        if beacon_result.found:
            try:
                fh, fw = frame.shape[:2]
                rx = max(0.0, (beacon_result.x - beacon_result.width) / fw)
                ry = min(1.0, (beacon_result.y + beacon_result.height) / fh)
                r_to_x = min(1.0, (beacon_result.x + beacon_result.width * 2) / fw)
                r_to_y = min(1.0, (beacon_result.y + beacon_result.height * 3) / fh)
                boxes = self.ocr(rx, ry, r_to_x, r_to_y, match=dist_regex, frame=frame)
                if not boxes:
                    boxes = self.ocr(rx, ry, r_to_x, r_to_y, frame=frame)
                if boxes:
                    for box in boxes:
                        text = box.name or ""
                        dist = parse_distance_text(text)
                        if dist is not None:
                            return dist
            except Exception:
                pass

        return None

    def _apply_camera_turn(self, delta_x: int):
        if not self.is_game_window_active():
            return
        if delta_x == 0:
            return
        self.navigation_movement_pending = False
        self.stuck_start_time = 0.0
        try:
            import win32api
            import win32con
            win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, delta_x, 0, 0, 0)
        except Exception:
            from pynput import mouse
            controller = mouse.Controller()
            controller.move(delta_x, 0)

    def _apply_movement(self, keys: list, duration: float):
        if not self.is_game_window_active():
            self._stop_all_movement()
            return
        if not keys:
            return
        self.navigation_movement_pending = True
        try:
            for key in keys:
                self.send_key_down(key)
            self.sleep(duration)
        finally:
            for key in keys:
                self.send_key_up(key)
        self.navigation_progress.record_movement(keys, duration)

    def _ensure_first_character(self) -> bool:
        now = time.time()
        if now - self.last_char_switch_time < 1.0:
            return False
        try:
            in_team_status, current_index, _ = self.in_team()
            if in_team_status and current_index != 0:
                self.last_char_switch_time = now
                self.log_info(f"当前出战为 {current_index + 1} 号位角色，切换至一号位角色执行移动")
                self.send_key("1", after_sleep=0.3)
                return True
        except Exception as e:
            logger.warning(f"检查并切换一号位角色异常: {e}")
        return False

    def _trigger_ai_decision(
        self,
        frame: np.ndarray,
        has_f_button: bool = False,
        action_text: str = "",
        is_frozen_letterbox: bool = False
    ):
        api_url = str(self.config.get("API URL") or os.environ.get("JEV_API_URL") or "")
        api_key = str(self.config.get("API Key") or os.environ.get("JEV_API_KEY") or "")

        ocr_text = ""
        if is_frozen_letterbox or not has_f_button:
            boxes = self.ocr(frame=prepare_quest_ocr_frame(frame))
            ocr_text = sanitize_quest_text("\n".join(box.name for box in boxes if box.name))

        action: QuestAction = decide_quest_action(
            frame=frame,
            has_f_button=has_f_button,
            action_text=action_text,
            quest_goal_text="跟随任务引导推进剧情",
            is_frozen_letterbox=is_frozen_letterbox,
            api_url=api_url,
            api_key=api_key,
            ocr_text=ocr_text,
        )
        self._record_jev_usage(action)

        self.log_info(f"决策引擎执行动作: {action.action_type}, 详情: {action.description}")
        if action.action_type == "interact" and action.key == "f":
            self.send_key("f", down_time=0.1)
        elif action.action_type == "click":
            self.click(0.5, 0.5)
        elif action.action_type == "attack":
            self.click(key="left")
        elif action.action_type == "skill" and action.key:
            self.send_key(action.key, down_time=0.1)

        if action.wait_seconds > 0:
            self.sleep(action.wait_seconds)

    def _record_jev_usage(self, action: QuestAction):
        if getattr(action, "total_tokens", 0) > 0 or getattr(action, "cost", 0.0) > 0:
            count = getattr(self, "jev_call_count", 0) + 1
            tokens = getattr(self, "jev_total_tokens", 0) + getattr(action, "total_tokens", 0)
            cost = getattr(self, "jev_cost_estimate", 0.0) + getattr(action, "cost", 0.0)
            self.jev_call_count = count
            self.jev_total_tokens = tokens
            self.jev_cost_estimate = cost
            self.info_set("JEV 调用次数", f"{count} 次")
            cost_display = f"${cost:.4f}" if cost > 0 else f"{tokens} tokens"
            self.info_set("JEV 额度消耗", cost_display)


    def skip_message(self) -> bool:
        if self.find_one("message", horizontal_variance=0.15):
            if message_dialog := self.find_one("message_dialog", vertical_variance=0.4, horizontal_variance=0.2):
                click = message_dialog.copy(y_offset=2.5 * message_dialog.height)
                click.width = self.width_of_screen(0.63)
                self.click(click, after_sleep=0.2)
                logger.info(f"点击推进短消息对话 {click}")
                return True
        return False
