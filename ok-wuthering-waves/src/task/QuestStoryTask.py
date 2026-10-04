import os
import time
from typing import Optional

import numpy as np
from ok import Logger
from src.task.BaseCombatTask import BaseCombatTask
from src.task.SkipBaseTask import SkipBaseTask
from src.task.WWOneTimeTask import WWOneTimeTask
from src.utils.QuestDecisionEngine import QuestAction, decide_quest_action
from src.utils.QuestNavigator import calculate_camera_turn, compute_movement_action
from src.utils.QuestVision import (
    detect_dialog_advance_indicator,
    detect_interact_action,
    detect_letterbox,
    detect_minimap_quest_arrow,
    detect_quest_beacon,
    detect_screen_freeze,
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
            "API URL": "",
            "API Key": "",
            "Camera Sensitivity": 1.0,
        }
        self.config_description = {
            "Auto Combat in Quest": "剧情期间遭遇敌人自动进入战斗",
            "Auto Skip Dialog": "剧情对话期间自动跳过",
            "Letterbox Freeze Wait Seconds": "上下黑边剧情动画持续静止触发交互决策的等待秒数",
            "API URL": "jev 视觉模型 API 地址",
            "API Key": "jev 视觉模型授权密钥",
            "Camera Sensitivity": "镜头旋转灵敏度系数",
        }
        self.current_state = self.STATE_IDLE
        self.last_frame: Optional[np.ndarray] = None
        self.letterbox_freeze_start_time: float = 0.0
        self.last_action_time: float = 0.0

    def run(self):
        self.current_state = self.STATE_IDLE
        self.last_frame = None
        self.letterbox_freeze_start_time = 0.0

        while not self.executor.paused:
            self.sleep(0.05)
            frame = self.frame
            if frame is None:
                continue

            # 1. 优先判定剧情对话
            if self.config.get("Auto Skip Dialog", True):
                if self.check_skip() or self.skip_message():
                    self.current_state = self.STATE_DIALOG
                    self.letterbox_freeze_start_time = 0.0
                    self.sleep(0.2)
                    continue

                advance_result = detect_dialog_advance_indicator(frame)
                if advance_result.found:
                    self.current_state = self.STATE_DIALOG
                    self.letterbox_freeze_start_time = 0.0
                    logger.info(
                        f"检测到剧情推进标识，点击界面进入下一段对话 (置信度 {advance_result.confidence:.2f})"
                    )
                    self.click(0.5, 0.8)
                    self.sleep(0.25)
                    continue

            # 2. 判定进入战斗状态
            if self.config.get("Auto Combat in Quest", True):
                if self.in_combat():
                    self.current_state = self.STATE_COMBAT
                    self.letterbox_freeze_start_time = 0.0
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
                    logger.info(
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

        # 检查中心右侧交互按键
        interact_result = detect_interact_action(frame)
        beacon_result = detect_quest_beacon(frame)

        if interact_result.has_f:
            self.current_state = self.STATE_DECIDE_INTERACT
            logger.info("检测到交互按键，准备执行交互动作")
            self._trigger_ai_decision(
                frame,
                has_f_button=True,
                action_text=interact_result.action_text
            )
            self.sleep(1.0)
            return

        # 寻路导航状态
        self.current_state = self.STATE_NAVIGATE
        sensitivity = float(self.config.get("Camera Sensitivity", 1.0))

        if beacon_result.found:
            # 根据信标位置旋转镜头
            beacon_cx = beacon_result.x + beacon_result.width // 2
            turn_cmd = calculate_camera_turn(
                screen_width=width,
                beacon_center_x=beacon_cx,
                camera_sensitivity=sensitivity
            )
            if turn_cmd.need_turn:
                self._apply_camera_turn(turn_cmd.delta_x_pixels)

            # 推进移动
            move_cmd = compute_movement_action(distance_meters=20.0)
            self._apply_movement(move_cmd.keys, move_cmd.press_duration)
        else:
            # 信标不在视野中，依据小地图指示箭头旋转镜头
            arrow_result = detect_minimap_quest_arrow(frame)
            if arrow_result.found:
                turn_cmd = calculate_camera_turn(
                    screen_width=width,
                    minimap_bearing_deg=arrow_result.bearing_deg,
                    camera_sensitivity=sensitivity
                )
                if turn_cmd.need_turn:
                    self._apply_camera_turn(turn_cmd.delta_x_pixels)

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

        logger.info(f"决策引擎执行动作: {action.action_type}, 详情: {action.description}")
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
