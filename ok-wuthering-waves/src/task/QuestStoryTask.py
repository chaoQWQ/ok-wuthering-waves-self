import os
import time
from typing import Optional

import numpy as np
from ok import Box, Logger
from src.task.BaseCombatTask import BaseCombatTask
from src.task.SkipBaseTask import SkipBaseTask
from src.task.WWOneTimeTask import WWOneTimeTask
from src.utils.QuestDecisionEngine import QuestAction, decide_quest_action
from src.utils.QuestNavigator import calculate_camera_turn, compute_movement_action
from src.utils.QuestVision import (
    BeaconResult,
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
            "API URL": "jev 视觉模型 API 地址",
            "API Key": "jev 视觉模型授权密钥",
            "Camera Sensitivity": "镜头旋转灵敏度系数",
            "Switch to First Character for Movement": "移动寻路期间固定切换至一号位角色",
        }
        self.current_state = self.STATE_IDLE
        self.last_frame: Optional[np.ndarray] = None
        self.letterbox_freeze_start_time: float = 0.0
        self.last_action_time: float = 0.0
        self.last_search_log_time: float = 0.0
        self.last_char_switch_time: float = 0.0
        self.last_nav_frame: Optional[np.ndarray] = None
        self.stuck_start_time: float = 0.0
        self.last_observed_distance: Optional[float] = None
        self.stuck_count: int = 0

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

        while not self.executor.paused:
            self.sleep(0.05)
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
                    fh, fw = frame.shape[:2]
                    rx = (top_left_skip.x + top_left_skip.width / 2.0) / fw
                    ry = (top_left_skip.y + top_left_skip.height / 2.0) / fh
                    self.click(rx, ry, after_sleep=0.3)
                    self.wait_until(self.skip_confirm, time_out=2.5, raise_if_not_found=False)
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
        height, width = frame.shape[:2]

        # 检查交互按键
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

        # 出现 F 键交互立即停止全部移动并执行交互
        if has_f:
            self._stop_all_movement()
            self.current_state = self.STATE_DECIDE_INTERACT
            self.log_info(f"检测到交互按键 [F]，停止移动并执行交互")
            self._trigger_ai_decision(
                frame,
                has_f_button=True,
                action_text=action_text
            )
            self.sleep(1.2)
            return

        # 寻路导航状态
        self.current_state = self.STATE_NAVIGATE
        sensitivity = float(self.config.get("Camera Sensitivity", 1.0))

        if self.config.get("Switch to First Character for Movement", True):
            self._ensure_first_character()

        beacon_result = detect_quest_beacon(frame)
        current_distance = self._extract_quest_distance(frame, beacon_result)

        # 检查并处理撞墙受阻卡滞
        if self._check_and_handle_stuck(frame, current_distance):
            return

        # 到达 1 至 2 米范围内停止移动并等待交互
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

        # 视野无信标，依据小地图指示箭头旋转镜头并移动
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

            # 若角度误差超过 20 度，原地旋转镜头，严禁边转向边移动以避免环绕画圈
            if abs(angle_diff) > 20.0:
                self.log_info(f"依据小地图指引原地调整朝向 (方位角 {arrow_result.bearing_deg:.1f}°)")
                self.sleep(0.1)
                return

            self.log_info(f"朝向小地图指引方向对齐完成，向前慢步推进")
            self._apply_movement(["w"], 0.25)
            return

        # 视野与小地图暂无目标标识，原地水平旋转视角搜寻信标，避免盲目前冲撞击墙体
        now = time.time()
        if now - self.last_search_log_time > 2.0:
            self.last_search_log_time = now
            self.log_info("视野暂未发现任务信标，正在原地水平旋转视角搜寻目标方位...")
        self._apply_camera_turn(120)
        self.sleep(0.2)

    def _check_and_handle_stuck(self, frame: np.ndarray, current_distance: Optional[float]) -> bool:
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
        count = getattr(self, "stuck_count", 0) + 1
        self.stuck_count = count
        self.log_info(f"检测到角色前进受阻卡滞 (第 {count} 次)，执行后撤与转向脱困避障")
        # 1. 后退拉开与阻碍物距离
        self.send_key_down("s")
        self.sleep(0.4)
        self.send_key_up("s")
        self.sleep(0.1)

        # 2. 交替侧向转向绕行
        turn_angle_delta = 160 if (count % 2 == 1) else -160
        self._apply_camera_turn(turn_angle_delta)
        self.sleep(0.1)

        # 3. 短暂侧向小步推进绕过阻隔边缘
        side_key = "d" if (count % 2 == 1) else "a"
        self.send_key_down("w")
        self.send_key_down(side_key)
        self.sleep(0.4)
        self.send_key_up(side_key)
        self.send_key_up("w")
        self.sleep(0.1)

    def _stop_all_movement(self):
        for key in ["w", "a", "s", "d", "shift"]:
            try:
                self.send_key_up(key)
            except Exception:
                pass

    def _extract_quest_distance(self, frame: np.ndarray, beacon_result: BeaconResult) -> Optional[float]:
        try:
            boxes = self.ocr(0.01, 0.18, 0.28, 0.48, match=r"(\d+(?:\.\d+)?)\s*(?:米|m|M)", frame=frame)
            if boxes:
                text = boxes[0].name or ""
                dist = parse_distance_text(text)
                if dist is not None:
                    return dist
        except Exception:
            pass

        if beacon_result.found:
            try:
                fh, fw = frame.shape[:2]
                rx = max(0.0, (beacon_result.x - beacon_result.width) / fw)
                ry = min(1.0, (beacon_result.y + beacon_result.height) / fh)
                r_to_x = min(1.0, (beacon_result.x + beacon_result.width * 2) / fw)
                r_to_y = min(1.0, (beacon_result.y + beacon_result.height * 3) / fh)
                boxes = self.ocr(rx, ry, r_to_x, r_to_y, match=r"(\d+(?:\.\d+)?)\s*(?:米|m|M)?", frame=frame)
                if boxes:
                    text = boxes[0].name or ""
                    dist = parse_distance_text(text)
                    if dist is not None:
                        return dist
            except Exception:
                pass

        return None

    def _apply_camera_turn(self, delta_x: int):
        try:
            import win32api
            import win32con
            win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, delta_x, 0, 0, 0)
        except Exception:
            from pynput import mouse
            controller = mouse.Controller()
            controller.move(delta_x, 0)

    def _apply_movement(self, keys: list, duration: float):
        if not keys:
            return
        for key in keys:
            self.send_key_down(key)
        self.sleep(duration)
        for key in keys:
            self.send_key_up(key)

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

        action: QuestAction = decide_quest_action(
            frame=frame,
            has_f_button=has_f_button,
            action_text=action_text,
            quest_goal_text="跟随任务引导推进剧情",
            is_frozen_letterbox=is_frozen_letterbox,
            api_url=api_url,
            api_key=api_key
        )

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

    def skip_message(self) -> bool:
        if self.find_one("message", horizontal_variance=0.15):
            if message_dialog := self.find_one("message_dialog", vertical_variance=0.4, horizontal_variance=0.2):
                click = message_dialog.copy(y_offset=2.5 * message_dialog.height)
                click.width = self.width_of_screen(0.63)
                self.click(click, after_sleep=0.2)
                logger.info(f"点击推进短消息对话 {click}")
                return True
        return False
