import os
import re
import time
from typing import Optional

import numpy as np
from ok import Box, Logger
from src.char.BaseChar import BaseChar
from src.task.BaseCombatTask import BaseCombatTask, CharDeadException, NotInCombatException
from src.task.SkipBaseTask import SkipBaseTask
from src.task.WWOneTimeTask import WWOneTimeTask
from src.utils.QuestDecisionEngine import (
    DETOUR_SIDE_KEYS,
    QuestAction,
    decide_approach_action,
    decide_detour_direction,
    decide_escape_action,
    decide_quest_action,
    decide_puzzle_effect,
    decide_climbing_route,
)
from src.utils.QuestAreaSearch import (
    QuestAreaSearch,
    QuestAreaStuckError,
    detect_quest_area,
    minimap_box,
    minimap_visible,
)
from src.utils.QuestDecisionSession import QuestDecisionSession
from src.utils.QuestSceneState import QuestSceneState, QuestSceneChangedError
from src.utils.QuestTraversalController import QuestTraversalController
from src.utils.QuestPuzzleSession import ExplicitControlHandler, QuestPuzzleSession, PuzzleObservation, object_appearance
from src.utils.VideoRouteValidator import coordinate_crop, parse_coordinate_text
from src.utils.QuestNavigator import calculate_camera_turn, compute_movement_action
from src.utils.QuestOcrPrivacy import is_named_quest_interaction, prepare_quest_ocr_frame, quest_goal_from_lines, sanitize_quest_text
from src.utils.QuestProgressTracker import QuestProgressTracker
from src.utils.QuestBackgroundMotion import detect_background_motion
from src.utils.QuestTargetSearch import QuestTargetSearch
from src.utils.QuestVision import (
    BeaconResult,
    ClimbStateResult,
    detect_climbing_state,
    detect_climbing_stamina,
    detect_quest_vertical_hint,
    detect_dialog_advance_indicator,
    detect_interact_action,
    detect_letterbox,
    detect_minimap_quest_arrow,
    detect_quest_beacon,
    detect_flower_guidance,
    detect_screen_freeze,
    detect_top_left_skip_button,
    parse_distance_text,
    resize_quest_text_frame,
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
            "Vision API URL": "",
            "Vision API Key": "",
            "Vision Model": "clef",
            "Allow Vision Screenshots": False,
            "Camera Sensitivity": 1.0,
            "Switch to First Character for Movement": True,
        }
        self.config_description = {
            "Auto Combat in Quest": "剧情期间遭遇敌人自动进入战斗",
            "Auto Skip Dialog": "剧情对话期间自动跳过",
            "Letterbox Freeze Wait Seconds": "上下黑边剧情动画持续静止触发交互决策的等待秒数",
            "API URL": "jev 文字决策 API 地址",
            "API Key": "jev 授权密钥",
            "Vision API URL": "视觉绕行决策 API 地址（例如 https://api.cloudflare.com/client/v4/accounts/你的账户ID/ai/run/@cf/cloudflare/clef），留空时使用固定绕行路线",
            "Vision API Key": "视觉绕行决策授权密钥，留空时不发送任何游戏画面截图",
            "Vision Model": "视觉决策模型名称：clef 或 clef-flash",
            "Allow Vision Screenshots": "允许发送已遮蔽特征码的游戏截图，用于地形识别和机关操作前后比较",
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
        self.navigation_progress = QuestProgressTracker()
        self.climbing_progress = QuestProgressTracker(require_distance=False)
        self.target_search = QuestTargetSearch()
        self.guidance_text = ""
        self.guidance_last_read = 0.0
        self.area_search = None
        self.decision_session = QuestDecisionSession()
        self.last_motion = None
        self.last_interaction_text = ""
        self.point_arrival_time = None
        self.point_approach_seconds = 0.0
        self.quest_combat_count = 0
        self.interaction_decision_waits = 0
        self.last_teleport_attempt_time: float = 0.0
        self.teleport_retry_delay: float = 30.0
        self.jev_call_count: int = 0
        self.jev_total_tokens: int = 0
        self.jev_cost_estimate: float = 0.0
        self.clef_call_count: int = 0
        self.clef_total_tokens: int = 0
        self.last_ui_reveal_time: float = 0.0
        self.quest_area_goal: Optional[str] = None
        self.quest_scene = QuestSceneState()
        self.traversal = QuestTraversalController(self.quest_scene)
        self.puzzle = QuestPuzzleSession()
        self.puzzle_before_frame = None
        self.last_coordinate_read = 0.0
        self.puzzle_panel_active = False
        self.goal_candidate = None
        self.last_climb_probe_time = 0.0

    def in_team(self):
        result = super().in_team()
        if result[0]:
            self.last_strict_team_time = time.time()
            self.last_world_seen_time = time.time()
            return result
        # 主线剧情使用试用角色时队伍可能只有一人，右侧换人栏为空，队伍栏模板全部缺失。
        # 战斗引擎依赖 in_team 的翻转判定解放动画等状态，这里用左上角小地图兜底：
        # 小地图同样在解放过场、地图界面、传送加载与黑边动画中不可见，语义一致。
        frame = self.frame
        if (frame is not None and frame.size > 0 and not detect_letterbox(frame).is_letterbox
                and minimap_visible(frame)):
            self.last_world_seen_time = time.time()
            return True, 0, 1
        # 小地图偶发识别失败（雾天/HUD 渐隐动画）时的短滞回。
        if time.time() - getattr(self, "last_world_seen_time", 0.0) < 1.0:
            return True, 0, 1
        return result

    def load_chars(self):
        current = self.chars[0] if self.chars else None
        if getattr(current, "story_fallback", False):
            if time.time() - getattr(self, "last_strict_team_time", 0.0) < 1.0:
                self.chars = [None, None, None]  # 真实队伍栏重新出现，交还基础加载流程
            else:
                return True
        in_team, _, _ = super().in_team()
        if in_team:
            return super().load_chars()
        frame = self.frame
        if (frame is None or frame.size == 0 or detect_letterbox(frame).is_letterbox
                or not minimap_visible(frame)
                or time.time() - getattr(self, "last_strict_team_time", 0.0) < 2.0):
            return super().load_chars()
        self.load_hotkey()
        fallback = BaseChar(self, 0)
        fallback.is_current_char = True
        fallback.story_fallback = True
        self.chars = [fallback, fallback, fallback]
        self.log_info("试用角色单人队伍且队伍栏不可见，使用通用战斗角色执行剧情战斗")
        return True

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
        except Exception as error:
            raise RuntimeError("无法确认游戏窗口是否位于前台") from error

    def run(self):
        WWOneTimeTask.run(self)

        self.ensure_in_front()
        self.sleep(.2)
        if not self.is_game_window_active():
            raise RuntimeError("游戏窗口没有进入前台，无法开始剧情导航")

        self.log_info("剧情模式已启动，已激活游戏窗口并重置鼠标位置")
        self.current_state = self.STATE_IDLE
        self.last_frame = None
        self.letterbox_freeze_start_time = 0.0
        self.last_search_log_time = 0.0
        self.climbing_start_time = 0.0
        self.navigation_progress = QuestProgressTracker()
        self.climbing_progress = QuestProgressTracker(require_distance=False)
        self.target_search = QuestTargetSearch()
        self.guidance_text = ""
        self.guidance_last_read = 0.0
        self.area_search = None
        self.decision_session = QuestDecisionSession()
        self.last_motion = None
        self.last_interaction_text = ""
        self.point_arrival_time = None
        self.point_approach_seconds = 0.0
        self.direct_interact_key = None
        self.direct_interact_attempts = 0
        self.direct_interact_last_time = 0.0
        self.quest_combat_count = 0
        self.interaction_decision_waits = 0
        self.jev_call_count = 0
        self.jev_total_tokens = 0
        self.jev_cost_estimate = 0.0
        self.clef_call_count = 0
        self.clef_total_tokens = 0
        self.last_ui_reveal_time = 0.0
        self.quest_area_goal = None
        self.quest_scene = QuestSceneState()
        self.traversal = QuestTraversalController(self.quest_scene)
        self.puzzle.reset_goal()
        self.puzzle_before_frame = None
        self.last_coordinate_read = 0.0
        self.puzzle_panel_active = False
        self.goal_candidate = None
        self.last_climb_probe_time = 0.0
        self.info_set("JEV 调用次数", "0 次")
        self.info_set("JEV 额度消耗", "0 tokens")
        self.info_set("Clef 调用次数", "0 次")
        self.info_set("Clef 额度消耗", "0 tokens")

        while not self.executor.paused:
            self.sleep(0.05)
            if not self.is_game_window_active():
                self._stop_all_movement()
                self._recover_game_window_focus()
                self.sleep(0.2)
                continue

            frame = self.frame
            if frame is None:
                continue

            if self.puzzle.pending is not None:
                if self.in_combat():
                    self.puzzle.interrupt("combat_started")
                    self.decision_session.mark_transition("combat_started")
                    self.quest_scene.waiting_for = None
                    continue
                self._check_puzzle_result(frame)
                if self.puzzle.pending is not None:
                    self.sleep(.2)
                    continue
                frame = self.frame

            if self.puzzle_panel_active:
                if minimap_visible(frame):
                    self.puzzle_panel_active = False
                else:
                    observation = self._observe_puzzle(frame)
                    if observation.phase == "panel":
                        self._execute_puzzle_action("panel_select", "", "puzzle_panel")
                        continue
                    if observation.phase in ("dialog", "cutscene"):
                        self.puzzle_panel_active = False
                    elif time.time() - self.puzzle_panel_started > 8:
                        # 机关面板迟迟识别不出操作提示：重置机关会话并按画面
                        # 过期处理，等待下一帧重新观察，不终止任务。
                        self._notify_navigation_issue("机关界面持续无法识别操作提示，重置机关会话后重试")
                        self.puzzle.reset_goal()
                        self.puzzle_panel_active = False
                        self.puzzle_before_frame = None
                        continue

            # 1. 优先判定剧情对话
            if self.config.get("Auto Skip Dialog", True):
                # 剧情跳过确认框(提示样式无模板)优先处理，避免确认框卡住整个流程
                if self.current_state == self.STATE_DIALOG and self._handle_story_skip_confirm():
                    self.sleep(0.5)
                    continue
                if self.check_skip():
                    self._mark_scene_transition("dialog_started", self.STATE_DIALOG)
                    self.current_state = self.STATE_DIALOG
                    self.letterbox_freeze_start_time = 0.0
                    self.log_info("检测到剧情跳过选项，点击执行跳过")
                    self.sleep(0.2)
                    continue

                top_left_skip = detect_top_left_skip_button(frame)
                if top_left_skip.found:
                    self._mark_scene_transition("dialog_started", self.STATE_DIALOG)
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
                    # Force OS level click using win32api because game ignores background clicks
                    cx, cy = skip_box.center()
                    import win32api
                    import win32con
                    import win32gui
                    hwnd_window = getattr(self, "hwnd", None)
                    hwnd = getattr(hwnd_window, "hwnd", 0) if hwnd_window is not None else 0
                    if not hwnd:
                        # 窗口标题含尾随空格，FindWindow 精确匹配会失败，按窗口类名兜底
                        hwnd = win32gui.FindWindow('UnrealWindow', None)
                    if hwnd:
                        pt = win32gui.ClientToScreen(hwnd, (int(cx), int(cy)))
                        win32api.SetCursorPos(pt)
                        self.sleep(0.05)
                        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                        self.sleep(0.15)
                        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
                        self.log_debug(f"物理鼠标点击剧情跳过按钮 ({int(cx)}, {int(cy)})")
                    else:
                        self.click_box(skip_box, down_time=0.15, after_sleep=0.3)
                    self.sleep(0.3)
                    self.wait_until(self.skip_confirm, time_out=3.0, raise_if_not_found=False)
                    self.sleep(0.2)
                    continue

                if self.skip_message():
                    self._mark_scene_transition("dialog_started", self.STATE_DIALOG)
                    self.current_state = self.STATE_DIALOG
                    self.letterbox_freeze_start_time = 0.0
                    self.log_info("检测到短消息对话，点击推进")
                    self.sleep(0.2)
                    continue

                advance_result = detect_dialog_advance_indicator(frame)
                if advance_result.found:
                    self._mark_scene_transition("dialog_started", self.STATE_DIALOG)
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
                    self._mark_scene_transition("combat_started", self.STATE_COMBAT)
                    self.current_state = self.STATE_COMBAT
                    self.letterbox_freeze_start_time = 0.0
                    self.log_info("检测到进入战斗状态，交由角色战斗执行器执行操作")
                    self._perform_quest_combat()
                    continue
                if self.current_state == self.STATE_COMBAT:
                    self._finish_quest_combat()
                    continue

            # 3. 判定上下黑边剧情动画
            letterbox_info = detect_letterbox(frame)
            if letterbox_info.is_letterbox:
                self._mark_scene_transition("cutscene_started", self.STATE_LETTERBOX_CUTSCENE)
                self.current_state = self.STATE_LETTERBOX_CUTSCENE
                self._handle_letterbox_state(frame)
                continue
            else:
                self.letterbox_freeze_start_time = 0.0

            # 4. 判定大世界任务导航与交互
            if not self.in_team_and_world():
                self._stop_all_movement()
                # 剧情对话的跳过按钮等 UI 会在鼠标静止后自动隐藏，轻晃鼠标让其重新显示再识别。
                self._reveal_hidden_ui()
                if self._handle_tutorial_panel(frame):
                    continue
                self._log_wait_state(frame, "当前界面不在队伍大世界（可能处于对话、加载或菜单），暂停移动等待界面恢复")
                self.sleep(.2)
                continue
            try:
                self._handle_world_navigation_and_interaction(frame)
            except QuestSceneChangedError:
                self._stop_all_movement()
                self.traversal.invalidate()
                continue
            except RuntimeError as error:
                # 验证类失败（镜头/攀爬/机关/搜索/交互确认）不应终止任务：
                # 通知后重置导航与机关状态并绕行重试，交由用户决定是否人工介入。
                self._stop_all_movement()
                self._notify_navigation_issue(f"{error}，重置状态并绕行重试")
                frame_now = self.frame
                if frame_now is not None and frame_now.size and detect_climbing_state(frame_now).is_climbing:
                    self.send_key("x", down_time=.1)
                    self.sleep(0.5)
                self.traversal.reset_goal()
                self.puzzle.reset_goal()
                # 绕行上限触发后计数已满，先清空循环计数再开新绕行，
                # 避免 begin_recovery 在降级路径内二次抛出。
                self.navigation_progress.reset_cycle()
                self.navigation_progress.begin_recovery()
                self._continue_navigation_recovery()
                continue
            except Exception:
                self._stop_all_movement()
                raise

    def _perform_quest_combat(self):
        try:
            self.get_current_char().perform()
        except CharDeadException:
            raise
        except NotInCombatException as error:
            if type(error) is not NotInCombatException:
                raise
            self._finish_quest_combat()

    def _finish_quest_combat(self):
        self._stop_all_movement()
        self._mark_scene_transition("combat_completed", self.STATE_IDLE)
        self.current_state = self.STATE_IDLE
        self.next_frame()

    def _mark_scene_transition(self, outcome: str, state: str):
        if self.current_state != state:
            self.quest_scene.transition(state)
            self.quest_scene.waiting_for = "combat_completed" if state == self.STATE_COMBAT else "scene_completed" if state == self.STATE_LETTERBOX_CUTSCENE else None
            self.traversal.invalidate()
            self.puzzle.interrupt(outcome)
            if self.current_state == self.STATE_COMBAT:
                self.combat_end()
                self.quest_combat_count += 1
                self.info_set("剧情战斗次数", self.quest_combat_count)
                self.climbing_start_time = 0.0
                self.climbing_progress = QuestProgressTracker(require_distance=False)
                self.guidance_last_read = 0.0
                self.last_frame = None
                self.last_motion = None
                self.log_info("剧情战斗结束，继续检查剧情与任务指引")
            self.decision_session.mark_transition(outcome)
            self.area_search = None
            self.navigation_progress = QuestProgressTracker()
            self.target_search.reset()
            self.point_arrival_time = None
            self.point_approach_seconds = 0.0
            self.interaction_decision_waits = 0

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
        self._read_quest_goal(frame)
        climb_result = detect_climbing_state(frame)
        self.quest_scene.transition("climbing" if climb_result.is_climbing else "world")
        if self.quest_scene.goal != self.guidance_text:
            vertical = self.quest_scene.vertical
            self.quest_scene.set_goal(self.guidance_text)
            self.quest_scene.vertical = vertical
            self.traversal.reset_goal()
            self.puzzle.reset_goal()
        self._read_scene_coordinates(frame)

        # 0. 优先检测是否处于攀爬状态
        if climb_result.is_climbing:
            if self.area_search is not None:
                area = self.area_search.find_observation(frame)
                if area is not None:
                    mx, my, mw, mh = minimap_box(frame)
                    if not self.area_search.observe(area, frame[my:my + mh, mx:mx + mw]):
                        self.area_search = None
            now = time.time()
            self.last_climbing_seen_time = now
            if self.climbing_start_time == 0.0:
                self.climbing_start_time = now
                self.climbing_progress = QuestProgressTracker(require_distance=False)
                self.navigation_progress.recovery_step = None
                self.navigation_progress.movement_seconds = 0.0
            climbing_duration = now - self.climbing_start_time
            self._handle_climbing_state(frame, climbing_duration)
            return
        else:
            if self.climbing_start_time != 0.0:
                self.navigation_progress.movement_seconds = 0.0
                self.navigation_progress.stationary_observed = False
            self.climbing_start_time = 0.0

        # 1. 检查是否存在 F 键交互
        has_f, action_text = self._read_interaction(frame)

        if action_text != self.last_interaction_text:
            self.interaction_decision_waits = 0
        self.last_interaction_text = action_text
        if self.decision_session.observe(self.guidance_text, action_text, time.time()):
            self.area_search = None
            self.navigation_progress = QuestProgressTracker()
            self.target_search.reset()
            self.point_arrival_time = None
            self.point_approach_seconds = 0.0
            self.interaction_decision_waits = 0
            self.goal_first_seen_time = time.time()
            self.goal_seen_beacon = False
            self.v_retrack_count = 0
            self.quest_scene.set_goal(self.guidance_text)
            self.traversal.reset_goal()
            self.puzzle.reset_goal()
            self.log_info(f"任务要求已经更新：{self.guidance_text}")

        if self.area_search is None:
            area = detect_quest_area(frame)
        else:
            area = self.area_search.find_observation(frame)
        if area is None and self.area_search is not None and detect_quest_beacon(frame).found:
            self.decision_session.mark_transition("quest_marker_changed")
            self.area_search = None
            self.navigation_progress = QuestProgressTracker()
            self.log_info("黄色任务区域已切换为任务信标，继续跟随新的指引")
        if area is not None or self.area_search is not None:
            # 标记当前目标为"范围圈"类型：没有指引点，丢失时不要按 V 或
            # 垂直扫寻找指引点，靠旋转让范围圈重新进入小地图识别范围
            self.quest_area_goal = self.guidance_text
            self._handle_area_navigation(frame, area, has_f, action_text)
            return

        # 2. 提取任务信标与目标距离
        beacon_result = detect_quest_beacon(frame)
        if beacon_result.found:
            self.quest_area_goal = None
            self.goal_seen_beacon = True
            self.beacon_seen_streak = min(3, getattr(self, "beacon_seen_streak", 0) + 1)
            self.last_beacon_seen_time = time.time()
            # 信标已找回：V 重试计数清零，「多次按 V」只在同一段连续丢失内累计。
            self.v_retrack_count = 0
            self.last_beacon_cy_ratio = (beacon_result.y + beacon_result.height / 2) / frame.shape[0]
        else:
            self.beacon_seen_streak = 0
        current_distance = self._extract_quest_distance(frame, beacon_result)
        self.quest_scene.distance = current_distance
        self._read_scene_coordinates(frame)
        if current_distance is None and not beacon_result.found:
            if re.search(r"跟随.*花朵|Follow.*flower", self.guidance_text, re.IGNORECASE):
                beacon_result = detect_flower_guidance(frame)
            # 面板显示"近距离"等无数值距离时，改用纯位移卡住判定兜底。
            self.navigation_progress.require_distance = False
        previous_distance = self.navigation_progress.best_distance
        self.navigation_progress.observe(current_distance)
        if current_distance is not None and previous_distance is not None and current_distance <= previous_distance - .5:
            self.traversal.moved_to_new_location()
            if self.decision_session.pending is not None and self.decision_session.pending["action"] in ("jump", "recover"):
                self.decision_session.mark_transition("movement_observed")
        if current_distance is None or current_distance > 2:
            self.point_arrival_time = None
            self.point_approach_seconds = 0.0

        # 3. 若任务目标距离超过 200 米，尝试打开地图定位并传送到附近传送点
        if current_distance is not None and current_distance > 200.0:
            if self._try_teleport_to_nearest_waypoint(current_distance):
                return

        # 4. 判定是否属于已到达任务点附近的交互
        arrow_result = detect_minimap_quest_arrow(frame)
        self.quest_scene.bearing = arrow_result.bearing_deg if arrow_result.found else None
        is_near_goal = False

        if current_distance is not None:
            if current_distance <= 3.0:
                is_near_goal = True
        else:
            if not arrow_result.found:
                is_near_goal = True

        if has_f:
            if is_near_goal or is_named_quest_interaction(self.guidance_text, action_text):
                self._stop_all_movement()
                self.point_arrival_time = None
                self.current_state = self.STATE_DECIDE_INTERACT
                # 已走到任务点，画面里又只有这一个交互提示，直接按 F，
                # 不再做交互名与任务文本的匹配（繁简差异曾导致卡死循环）。
                if is_near_goal and self._direct_interact_at_target(action_text):
                    self.sleep(.2)
                    return
                decision = self._trigger_ai_decision(
                    frame,
                    has_f_button=True,
                    action_text=action_text,
                    event="near_interaction",
                    current_distance=current_distance,
                )
                if decision != "search":
                    self.sleep(.2)
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
        if current_distance is not None and current_distance <= 2.0 and self.quest_scene.vertical == "unknown":
            if beacon_result.found:
                beacon_cx = beacon_result.x + beacon_result.width / 2
                beacon_cy = beacon_result.y + beacon_result.height / 2
                if beacon_cy >= height * .60 and abs(beacon_cx - width / 2) <= width * .08 and self.point_approach_seconds < 1.2:
                    self.log_info("任务信标位于角色前下方，向前短步检查剧情触发")
                    self.point_approach_seconds += .15
                    self._apply_movement(["w"], .15)
                    return
            self._stop_all_movement()
            self.log_info(f"已到达任务目标附近 (距离 {current_distance:.1f} 米)，停止移动，等待交互触发")
            if self.point_arrival_time is None:
                self.point_arrival_time = time.time()
            elif time.time() - self.point_arrival_time >= 10:
                self._notify_navigation_issue("到达任务点后10秒仍没有交互或任务变化，小幅绕行后重新接近")
                self.point_arrival_time = None
                self.point_approach_seconds = 0.0
                self.navigation_progress.begin_recovery()
                self._continue_navigation_recovery()
                return
            self._trigger_ai_decision(frame, has_f_button=has_f, action_text=action_text,
                                      event="point_arrival", current_distance=current_distance)
            self.sleep(0.4)
            return

        # 绕行期间保持移动方向，完成整个过程后恢复信标导航。
        if self.navigation_progress.recovery_step is not None:
            self._continue_navigation_recovery()
            return
        if self.navigation_progress.blocked:
            self.log_info(f"累计前进 {self.navigation_progress.movement_seconds:.1f} 秒后背景保持静止且任务距离没有缩减，执行侧向绕行")
            self._handle_stuck_recovery(frame)
            return

        # 画面中没有任务信标时立即按 V 重新追踪任务指引——丢失的信标靠转视角
        # 找不回来，按 V 才会重新出现。范围圈类型目标（小地图黄圈）本身没有
        # 信标，交给区域搜索/绕行逻辑处理，不按 V。
        beacon_confirmed = (getattr(self, "goal_seen_beacon", False)
                            and getattr(self, "beacon_seen_streak", 0) >= 2
                            and time.time() - getattr(self, "last_beacon_seen_time", 0.0) < 90.0)
        area_suspect = (not beacon_confirmed
                        and time.time() - getattr(self, "goal_first_seen_time", 0.0) > 20.0)
        near_conversion = (current_distance is not None and current_distance < 40
                           and getattr(self, "last_beacon_cy_ratio", 1.0) >= 0.25)
        if not beacon_result.found and self.quest_area_goal != self.guidance_text:
            now_v = time.time()
            if now_v - getattr(self, "last_v_retrack_time", 0.0) >= 4.0:
                self.last_v_retrack_time = now_v
                self.v_retrack_count = getattr(self, "v_retrack_count", 0) + 1
                self.log_info("画面中没有任务信标，按 V 重新追踪任务指引")
                self.send_key("v", down_time=0.1)
                self.sleep(0.8)
                if self.v_retrack_count % 4 == 0:
                    self._notify_navigation_issue("多次按 V 仍未出现任务信标，小幅绕行变换位置后再试")
                    self.navigation_progress.begin_recovery()
                    self._continue_navigation_recovery()
            else:
                self.sleep(0.2)
            return

        # 镜头调整依据世界目标的位置，角色朝向仅用于小地图导航。
        if current_distance is not None and current_distance <= 30:
            if beacon_result.found:
                self.vision_pitch_attempts = 0
                self.vision_pitch_offset = 0
                beacon_cx = beacon_result.x + beacon_result.width / 2
                beacon_cy = beacon_result.y + beacon_result.height / 2
                # 以指引点在画面中的位置为准逐步居中：偏上抬镜、偏下压镜、
                # 偏左右转镜（指引点旁的箭头即此方位关系）。
                centered = True
                if abs(beacon_cx - width / 2) > width * .18:
                    try:
                        camera_turn = self.traversal.observe_camera((beacon_cx - width / 2) / width * 90)
                    except RuntimeError:
                        # 镜头调整持续无法让指引点居中：重置镜头验证并侧向绕行
                        # 重新接近，而不是终止任务。
                        self._notify_navigation_issue("镜头调整持续没有改善目标位置，重置验证并小幅绕行后重新接近")
                        self.traversal.reset_goal()
                        self.navigation_progress.begin_recovery()
                        self._continue_navigation_recovery()
                        return
                    if not camera_turn:
                        self._refresh_traversal_observation()
                        return
                    self._apply_camera_turn(120 if beacon_cx > width / 2 else -120)
                    centered = False
                elif beacon_cy < height * .15:
                    self._apply_camera_pitch(-80)
                    centered = False
                elif beacon_cy > height * .85:
                    self._apply_camera_pitch(80)
                    centered = False
                if not centered and getattr(self, "vision_center_attempts", 0) < 8:
                    self.vision_center_attempts = getattr(self, "vision_center_attempts", 0) + 1
                    self.sleep(0.1)
                    return
                self.vision_center_attempts = 0
                approach = self._maybe_vision_approach(frame, current_distance, beacon_result)
                if approach == "unknown":
                    self._refresh_traversal_observation()
                    return
                if approach == "climb":
                    if time.time() - getattr(self, "last_climb_probe_time", 0.0) < 1.2:
                        self.sleep(.2)
                        return
                    self.log_info("视觉决策：目标在上方，向墙面跳跃攀爬")
                    try:
                        self.traversal.begin_jump()
                    except RuntimeError:
                        # 同一位置跳跃多次没有进展，改用侧向绕行寻找新通路。
                        self._notify_navigation_issue("同一位置跳跃多次没有进展，改用侧向绕行")
                        self.traversal.reset_goal()
                        self._handle_stuck_recovery(frame)
                        return
                    self.last_climb_probe_time = time.time()
                    self._apply_movement(["w"], .45, jump=True)
                    return
                if approach == "drop":
                    self.log_info("已确认目标下方通路，短距离前进并检查下降状态")
                    self._apply_movement(["w"], .2)
                    return
                if approach == "detour":
                    self.log_info("视觉决策：正面受阻，执行侧向绕行")
                    self._handle_stuck_recovery(frame)
                    return
            elif self._pitch_toward_quest_beacon():
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
                tolerance_ratio=.06 if current_distance is not None and current_distance <= 20 else .02,
                max_delta_x=100
            )
            if turn_cmd.need_turn:
                if not self.traversal.observe_camera(angle_error_deg):
                    self._refresh_traversal_observation()
                    return
                self.target_search.next_turn(allow_high_count=True)
                self._apply_camera_turn(turn_cmd.delta_x_pixels)
                self.sleep(0.1)
                if current_distance is not None and current_distance <= 20 and abs(angle_error_deg) <= 12:
                    self._apply_movement(["w"], .12)
                return

            self.target_search.reset()
            self.target_search.remember_forward_target(current_distance)

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

            duration = min(move_cmd.press_duration, .2 if effective_distance <= 5 else .4)
            self._apply_movement(move_cmd.keys, duration)
            if self._navigation_stalled(current_distance):
                self._notify_navigation_issue("任务距离与人物坐标持续无变化，判定卡住，执行侧向绕行")
                self.navigation_progress.begin_recovery()
                self._continue_navigation_recovery()
            return

        # 9. 视野无信标，依据小地图指示箭头旋转镜头并移动
        arrow_result = detect_minimap_quest_arrow(frame)
        if arrow_result.found:
            turn_cmd = calculate_camera_turn(
                screen_width=width,
                minimap_bearing_deg=arrow_result.bearing_deg,
                camera_sensitivity=sensitivity,
                minimap_tolerance_deg=12 if current_distance is not None and current_distance <= 20 else 6,
                max_delta_x=120
            )
            if turn_cmd.need_turn:
                self.target_search.next_turn(allow_high_count=True)
                self._apply_camera_turn(turn_cmd.delta_x_pixels)
                self.sleep(0.1)
                # 短步让角色采用当前镜头方向，再读取角色箭头的实际朝向。
                self._apply_movement(["w"], .15)
                return

            self.log_info("已朝向小地图任务目标，向前慢步推进")
            self.target_search.reset()
            self.target_search.remember_forward_target(current_distance)
            self._apply_movement(["w"], 0.25)
            return

        forward_duration = self.target_search.next_forward_probe(current_distance)
        if forward_duration:
            self.log_info("任务图标暂时消失，沿刚确认的前方短步移动并检查距离")
            self._apply_movement(["w"], forward_duration)
            return

        # 10. 视野与小地图暂无目标标识：先垂直扫寻（任务目标常在建筑上方，
        # 水平旋转找不到），扫不到再把镜头恢复原位后水平旋转搜寻信标。
        # 跳过条件：范围圈类型目标、无指引点目标、近距离信标转换范围圈。
        prefer_down = getattr(self, "last_beacon_cy_ratio", 1.0) > 0.75
        if (not area_suspect and not near_conversion
                and self.quest_area_goal != self.guidance_text
                and self._pitch_toward_quest_beacon(prefer_down=prefer_down)):
            return
        now = time.time()
        if now - self.last_search_log_time > 2.0:
            self.last_search_log_time = now
            self.log_info("视野暂未发现任务信标，正在原地水平旋转视角搜寻目标方位...")
            # 定期保存当前画面用于诊断范围圈/信标识别失败原因
            try:
                import cv2 as _cv2
                _cv2.imwrite(os.path.join("logs", "rotate_debug.png"), frame)
            except Exception:
                pass
        if self.target_search.turn_count == 10:
            self.log_info("长时间未发现目标，尝试按 V 键重新追踪任务指引")
            self.send_key("v", down_time=0.1)
            self.sleep(0.5)
        try:
            turn_pixels = self.target_search.next_turn()
        except RuntimeError:
            self._handle_target_search_failure(frame)
            return
        self._apply_camera_turn(turn_pixels)
        self.sleep(0.2)

    def _read_quest_goal(self, frame: np.ndarray, force: bool = False):
        if not force and time.time() - getattr(self, "guidance_last_read", 0.0) < 2:
            return
        height, width = frame.shape[:2]
        region = Box(round(width * .01), round(height * .23), round(width * .24), round(height * .16))
        boxes = self._ocr_quest_region(frame, region)
        panel_text = " ".join(box.name for box in boxes)
        self.quest_scene.vertical = "below" if re.search(r"[▼▽↓]", panel_text) else "above" if re.search(r"[▲△↑]", panel_text) else "unknown"
        if self.quest_scene.vertical == "unknown":
            for box in boxes:
                if parse_distance_text(box.name, require_unit=True) is not None:
                    self.quest_scene.vertical = detect_quest_vertical_hint(frame, (box.x, box.y, box.width, box.height))
                    break
        text = quest_goal_from_lines([box.name for box in boxes if box.x <= frame.shape[1] * .05])
        if text:
            if not self.guidance_text or text == self.guidance_text:
                self.guidance_text = text
                self.goal_candidate = None
            elif text == getattr(self, "goal_candidate", None):
                self.guidance_text = text
                self.goal_candidate = None
            else:
                self.goal_candidate = text
        self.guidance_last_read = time.time()

    def _handle_tutorial_panel(self, frame: np.ndarray) -> bool:
        """识别多页教程提示面板：连按 D 翻页，出现确认按钮后点击。

        面板特征：底部 A/D 翻页按钮（每页都有）与"切换至最后一页后
        可关闭界面"提示文案；最后一页提示文案消失、出现"确认"按钮，
        因此用 A/D 字母或提示文案任一命中即认定面板。
        """
        try:
            height, width = frame.shape[:2]
            page_strip = self._ocr_quest_region(
                frame, Box(round(width * .20), round(height * .80), round(width * .60), round(height * .07)))
            letters = {(box.name or "").strip().upper() for box in page_strip}
            marker_region = Box(round(width * .30), round(height * .92), round(width * .40), round(height * .07))
            marker_text = "".join(sanitize_quest_text(box.name) for box in self._ocr_quest_region(frame, marker_region))
            panel_seen = "A" in letters or "D" in letters or "最后一页" in marker_text
            if not panel_seen:
                # 单页教程面板（如"鬼界"）没有 A/D 按钮与提示文案，
                # 标题旁的"?"帮助图标是两类面板共有的固定特征。
                panel_seen = self._tutorial_icon_matched(frame)
            if not panel_seen:
                return False
            confirm_region = Box(round(width * .35), round(height * .88), round(width * .30), round(height * .10))
            confirm = next((box for box in self._ocr_quest_region(frame, confirm_region)
                            if (box.name or "").replace(" ", "") in ("确认", "确認")), None)
            if confirm is not None:
                self.log_info("教程面板已翻到最后一页，点击确认关闭")
                self.click(confirm, after_sleep=0.5)
                self.tutorial_panel_presses = 0
                return True
            if getattr(self, "tutorial_panel_presses", 0) >= 15:
                self.tutorial_panel_presses = 0
                self._notify_navigation_issue("教程面板多次翻页仍未出现确认按钮，暂停自动翻页")
                return False
            self.tutorial_panel_presses = getattr(self, "tutorial_panel_presses", 0) + 1
            self.log_info("检测到教程提示面板，按 D 翻页")
            self.send_key("d", down_time=0.05)
            self.sleep(0.4)
            return True
        except Exception:
            return False

    def _tutorial_icon_matched(self, frame: np.ndarray) -> bool:
        """标题旁的"?"帮助图标是单页/多页教程面板共有的固定特征。"""
        import cv2
        template = getattr(self, "_tutorial_icon_template", None)
        if template is None:
            path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                "assets", "tutorial_help_icon.png")
            if not os.path.exists(path):
                return False
            template = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
            if template is None:
                return False
            self._tutorial_icon_template = template
        height, width = frame.shape[:2]
        x, y = round(width * .02), round(height * .25)
        region = cv2.cvtColor(frame[y:round(height * .40), x:round(width * .10)], cv2.COLOR_BGR2GRAY)
        best = 0.0
        for factor in (.8, 1.0, 1.05, 1.2):
            scale = width / 1920.0 * factor
            th, tw = max(5, round(template.shape[0] * scale)), max(5, round(template.shape[1] * scale))
            scaled = cv2.resize(template, (tw, th), interpolation=cv2.INTER_AREA)
            if region.shape[0] >= th and region.shape[1] >= tw:
                result = cv2.matchTemplate(region, scaled, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, _ = cv2.minMaxLoc(result)
                best = max(best, max_val)
        return best >= .7

    def _read_interaction(self, frame: np.ndarray) -> tuple[bool, str]:
        height, width = frame.shape[:2]
        region = Box(round(width * .55), round(height * .38), round(width * .37), round(height * .32))
        boxes = self._ocr_quest_region(frame, region)
        f_box = self.find_one("pick_up_f_hcenter_vcenter", box=region, threshold=.8, frame=frame)
        key_box = None if f_box is None else (f_box.x, f_box.y, f_box.width, f_box.height)
        result = detect_interact_action(frame, ocr_boxes=boxes, key_box=key_box)
        return result.has_f, sanitize_quest_text(result.action_text)

    def _direct_interact_at_target(self, action_text: str) -> bool:
        """已到达任务点且画面出现交互提示时直接按 F。

        到点后唯一交互就是任务目标本身，无需再做交互名匹配；同一目标最多
        重试 3 次，之后交回 AI 决策路径兜底。返回 False 表示本次不执行。
        """
        now = time.time()
        key = (self.guidance_text, action_text)
        if getattr(self, "direct_interact_key", None) != key:
            self.direct_interact_key = key
            self.direct_interact_attempts = 0
        if self.decision_session.pending is not None or self.puzzle.pending is not None:
            return False
        if "interact" in self.decision_session.failed_actions(action_text, "near_interaction"):
            return False
        if now - getattr(self, "direct_interact_last_time", 0.0) < 2.5:
            return False
        if getattr(self, "direct_interact_attempts", 0) >= 3:
            return False
        self.direct_interact_last_time = now
        self.direct_interact_attempts = getattr(self, "direct_interact_attempts", 0) + 1
        self.log_info(f"已到达任务点且出现交互提示 [F] {action_text or '(未识别)'}，直接执行交互")
        result = self._execute_puzzle_action("interact", action_text, "near_interaction")
        return result != "stale"

    def _ocr_quest_region(self, frame: np.ndarray, region: Box):
        text_frame, scale = resize_quest_text_frame(frame)
        text_region = Box(round(region.x * scale), round(region.y * scale),
                          round(region.width * scale), round(region.height * scale))
        boxes = self.ocr(box=text_region, frame=text_frame)
        for box in boxes:
            box.x, box.y = round(box.x / scale), round(box.y / scale)
            box.width, box.height = round(box.width / scale), round(box.height / scale)
        return boxes

    def _read_scene_coordinates(self, frame):
        now = time.time()
        if now - self.last_coordinate_read < 1:
            return
        import cv2
        self.last_coordinate_read = now
        crop = cv2.resize(coordinate_crop(frame), None, fx=3, fy=3, interpolation=cv2.INTER_LANCZOS4)
        boxes = self.ocr(frame=crop)
        coordinate = None
        for box in boxes:
            # 时间文字可能与坐标位于同一个 OCR 文本框。
            match = re.search(r"-?\d+\s*[,，]\s*-?\d+\s*[,，]\s*-?\d+", box.name)
            if match:
                coordinate = parse_coordinate_text(match[0])
                break
        previous = self.traversal.coordinate
        self.traversal.record_coordinate(coordinate, now)
        if previous is not None and coordinate is not None:
            import math
            if math.dist(previous, coordinate) >= 3:
                self.traversal.moved_to_new_location()

    def _navigation_stalled(self, current_distance: Optional[float]) -> bool:
        """撞墙判定：任务距离与人物坐标两个信号同时持续无变化才判卡。

        任一信号出现进展（距离较窗口基准缩减 0.5 米，或坐标位移 0.5 米）
        即重置 6 秒观察窗口；两个信号在整个窗口内都无变化才触发绕行。
        信号缺失（面板无数值、坐标 OCR 未读到）时不阻断判定，按无变化计。
        """
        import math
        now = time.time()
        sample_distance = current_distance
        sample_coordinate = self.traversal.coordinate
        if sample_distance is None and sample_coordinate is None:
            # 两个信号都缺失，无法判定，重置后等待下一次有效采样。
            self.stall_base_distance = None
            self.stall_base_coordinate = None
            self.stall_since = None
            return False
        base_distance = getattr(self, "stall_base_distance", None)
        base_coordinate = getattr(self, "stall_base_coordinate", None)
        # 信号首次可用时采纳为基准，保证窗口内比较始终有效。
        if base_distance is None and sample_distance is not None:
            self.stall_base_distance = base_distance = sample_distance
        if base_coordinate is None and sample_coordinate is not None:
            self.stall_base_coordinate = base_coordinate = sample_coordinate
        progressed = (
            (sample_distance is not None and base_distance is not None
             and sample_distance <= base_distance - 0.5)
            or (sample_coordinate is not None and base_coordinate is not None
                and math.dist(sample_coordinate, base_coordinate) >= 0.5)
        )
        if progressed:
            self.stall_base_distance = sample_distance
            self.stall_base_coordinate = sample_coordinate
            self.stall_since = now
            return False
        stall_since = getattr(self, "stall_since", None)
        if stall_since is None:
            self.stall_since = now
            return False
        if now - stall_since >= 6.0:
            self.stall_base_distance = None
            self.stall_base_coordinate = None
            self.stall_since = None
            return True
        return False

    def _refresh_traversal_observation(self):
        self._stop_all_movement()
        self.traversal.invalidate()
        self._read_quest_goal(self.frame, force=True)
        self._apply_camera_pitch(40 if self.traversal.observation_attempts % 2 else -40)
        self.sleep(.2)
        self.next_frame()

    def _call_quest_vision(self, function, **kwargs):
        if not self.config.get("Allow Vision Screenshots", False):
            raise PermissionError("游戏截图发送尚未授权")
        self._stop_all_movement()
        goal = self.guidance_text
        context = dict(kwargs.get("context") or {})
        context["scene"] = self.quest_scene.context()
        kwargs["context"] = context
        self.decision_session.reserve_call(time.time())
        result = function(**kwargs)
        self.next_frame()
        self._read_quest_goal(self.frame, force=True)
        if (not self.is_game_window_active() or self.guidance_text != goal or getattr(self, "goal_candidate", None)
                or detect_letterbox(self.frame).is_letterbox
                or detect_dialog_advance_indicator(self.frame).found or self.in_combat()):
            raise QuestSceneChangedError("视觉请求期间场景已经改变，重新检查任务状态")
        return result

    def _observe_puzzle(self, frame, interaction=None):
        if minimap_visible(frame):
            self._read_quest_goal(frame, force=True)
        if interaction is None:
            _, interaction = self._read_interaction(frame)
        boxes = self.ocr(frame=prepare_quest_ocr_frame(frame))
        texts = tuple(sanitize_quest_text(box.name) for box in boxes if box.name)
        state_texts = tuple(sanitize_quest_text(box.name) for box in boxes if box.name
                            and box.y < frame.shape[0] * .75 and box.x < frame.shape[1] * .92)
        controls = tuple((sanitize_quest_text(box.name), (box.x + box.width / 2, box.y + box.height / 2))
                         for box in boxes if box.name and frame.shape[1] * .2 < box.x < frame.shape[1] * .8
                         and frame.shape[0] * .25 < box.y < frame.shape[0] * .85)
        phase = "world"
        if detect_letterbox(frame).is_letterbox:
            phase = "cutscene"
        elif detect_dialog_advance_indicator(frame).found or detect_top_left_skip_button(frame).found:
            phase = "dialog"
        elif not minimap_visible(frame):
            phase = "unknown"
            if any(re.search(r"确认|返回|取消|Confirm|Cancel|Back", text, re.IGNORECASE) for text in texts):
                phase = "panel"
        height, width = frame.shape[:2]
        appearance = object_appearance(frame, (int(width * .25), int(height * .2), int(width * .6), int(height * .55)))
        position = self.traversal.coordinate if (self.traversal.last_coordinate_time is not None
                                               and time.time() - self.traversal.last_coordinate_time <= 2) else None
        return PuzzleObservation(self.guidance_text, interaction, texts, position, phase, appearance=appearance,
                                 controls=controls, state_texts=state_texts, frame=frame.copy())

    def _check_puzzle_result(self, frame):
        if not self.is_game_window_active():
            self._stop_all_movement()
            self.puzzle.interrupt("focus_changed")
            self.decision_session.mark_transition("focus_changed")
            self.quest_scene.waiting_for = None
            return
        observation = self._observe_puzzle(frame)
        pending = self.puzzle.pending
        if pending is None:
            return
        elapsed = time.time() - pending["time"]
        before = self.puzzle_before_frame
        moving = None if before is None else detect_background_motion(before, frame).moving
        if (elapsed >= 1.5 and not pending.get("visual_checked")
                and self.config.get("Allow Vision Screenshots", False)
                and self.config.get("Vision API URL") and self.config.get("Vision API Key")
                and pending["handler"].effect(pending["before"], observation) is None):
            pending["visual_checked"] = True
            self.decision_session.reserve_call(time.time())
            result = decide_puzzle_effect(before, frame, self.guidance_text, pending["step"].kind,
                                          self.config["Vision API URL"], self.config["Vision API Key"],
                                          model=self.config.get("Vision Model") or "clef",
                                          context={"object_id": pending["step"].object_id,
                                                   "interaction": pending["before"].interaction,
                                                   "before_state": pending["before"].state,
                                                   "after_state": observation.state,
                                                   "expected_effect": pending["step"].expected_effect})
            self.clef_call_count += 1
            self.clef_total_tokens += int(result.get("total_tokens", 0) or 0)
            self.info_set("Clef 调用次数", f"{self.clef_call_count} 次")
            self.info_set("Clef 额度消耗", f"{self.clef_total_tokens} tokens")
            self.next_frame()
            observation = self._observe_puzzle(self.frame)
            if (self.is_game_window_active() and observation.goal == pending["before"].goal
                    and observation.phase == pending["before"].phase and not self.in_combat()):
                if result.get("choice") == "changed" and (result.get("probabilities") or {}).get("changed", 0) >= .75:
                    observation.visual_state = "changed"
        outcome = self.puzzle.verify(observation, time.time(), animation=moving is True)
        if outcome != "pending":
            self.puzzle_panel_active = outcome == "panel_started"
            if self.puzzle_panel_active:
                self.puzzle_panel_started = time.time()
            self.decision_session.mark_transition(outcome)
            self.quest_scene.waiting_for = None
            self.puzzle_before_frame = None
            self.quest_scene.object_state = self.puzzle.context()
            self.info_set("机关验证", outcome)
            self.log_info(f"机关操作验证结果：{outcome}")

    def _can_continue_quest_input(self):
        return (not self.executor.paused and self.is_game_window_active()
                and not detect_letterbox(self.frame).is_letterbox
                and not detect_dialog_advance_indicator(self.frame).found
                and not detect_top_left_skip_button(self.frame).found
                and minimap_visible(self.frame) and not self.in_combat())

    def _hold_quest_input(self, keys, duration, mouse=False):
        if duration <= 0:
            raise ValueError("操作持续时间必须大于零")
        elapsed = 0.0
        pressed = []
        try:
            for key in keys:
                if mouse:
                    self.mouse_down(key=key)
                else:
                    self.send_key_down(key)
                pressed.append(key)
            while elapsed < duration:
                interval = min(.2, duration - elapsed)
                self.sleep(interval)
                elapsed += interval
                self.next_frame()
                if not self._can_continue_quest_input():
                    self.quest_scene.input_interruptions += 1
                    break
        finally:
            release_errors = []
            for key in pressed:
                try:
                    if mouse:
                        self.mouse_up(key=key)
                    else:
                        self.send_key_up(key)
                except Exception as error:
                    release_errors.append(error)
            if release_errors:
                raise release_errors[0]
        return elapsed

    def _execute_puzzle_action(self, action, action_text, location, expected_key=None):
        self.next_frame()
        observation = self._observe_puzzle(self.frame, action_text)
        try:
            step, handler = self.puzzle.prepare(observation, action)
        except RuntimeError as error:
            # 机关规则或状态无法确认（缺提示/状态重复无效果等）不应终止任务：
            # 本帧按画面过期处理，等待下一帧重新观察。
            self._notify_navigation_issue(f"{error}，本帧跳过机关操作")
            return "stale"
        if expected_key is not None and step.key != expected_key:
            return "stale"
        self.quest_scene.object_id = step.object_id
        self.quest_scene.available_actions = [step.action]
        self.quest_scene.object_state = self.puzzle.context()
        self.next_frame()
        if observation.frame.shape != self.frame.shape:
            return "stale"
        if observation.phase == "world":
            self._read_quest_goal(self.frame, force=True)
            if self.guidance_text != observation.goal or self.goal_candidate:
                return "stale"
        if action == "interact":
            current_f, current_text = self._read_interaction(self.frame)
            if not current_f or current_text != step.preconditions["interaction"]:
                return "stale"
        if action == "panel_select":
            if observation.phase != "panel" or not self.is_game_window_active() or minimap_visible(self.frame):
                return "stale"
            x, y = step.target
            height, width = self.frame.shape[:2]
            region = Box(round(max(0, x - width * .08)), round(max(0, y - height * .04)),
                         round(width * .16), round(height * .08))
            labels = self._ocr_quest_region(self.frame, region)
            if not any(sanitize_quest_text(box.name) == step.preconditions["control"] for box in labels):
                return "stale"
        elif observation.phase != "world" or not self._can_continue_quest_input():
            return "stale"
        self.puzzle_before_frame = self.frame.copy()
        self.puzzle.begin(step, handler, observation, time.time())
        self.decision_session.record(action, action_text, location, time.time(), external_verification=True)
        self.quest_scene.waiting_for = "mechanism_effect"
        if step.target is not None:
            self.click(step.target[0] / self.frame.shape[1], step.target[1] / self.frame.shape[0])
        else:
            self._hold_quest_input([step.key], step.duration, mouse=step.action == "attack")
        if self.puzzle.pending is not None:
            self.puzzle.pending["time"] = time.time()
        self._check_puzzle_result(self.frame)
        return action

    def _handle_area_navigation(self, frame: np.ndarray, area, has_f: bool, action_text: str):
        self.current_state = self.STATE_NAVIGATE
        if area is None:
            self._stop_all_movement()
            self.area_search.lose_observation()
            self.sleep(.2)
            return
        entering = self.area_search is None
        if entering:
            self.area_search = QuestAreaSearch()
            self.navigation_progress = QuestProgressTracker(require_distance=False)
            self.target_search.reset()
            self.log_info("识别到黄色任务区域，开始向圈中心移动")
        mx, my, mw, mh = minimap_box(frame)
        pending_keys = self.area_search.pending_keys
        pending_duration = self.area_search.pending_duration
        observed = self.area_search.observe(area, frame[my:my + mh, mx:mx + mw])
        if not observed:
            self.decision_session.mark_transition("quest_area_changed")
            self.decision_session.seen_states.clear()
            self.area_search = QuestAreaSearch()
            self.area_search.observe(area, frame[my:my + mh, mx:mx + mw])
            self.navigation_progress = QuestProgressTracker(require_distance=False)
            entering = True
            self.log_info("任务区域已经改变，开始接近新的圈中心")
        elif pending_keys is not None:
            moving = self.area_search.last_displacement >= .2
            self.traversal.record_map_progress(self.area_search.last_displacement if moving else 0)
            self.navigation_progress.record_movement(list(pending_keys), pending_duration, moving=moving)
            if moving and self.decision_session.pending is not None and self.decision_session.pending["action"] in ("jump", "recover"):
                self.decision_session.mark_transition("movement_observed")
        if self.config.get("Switch to First Character for Movement", True) and self._ensure_first_character():
            return
        if entering or (has_f and area.center_distance <= area.radius):
            result = self._trigger_ai_decision(
                frame, has_f_button=has_f, action_text=action_text,
                event="area_entry" if entering else "area_interaction",
            )
            if result not in ("search", "observe", "stale"):
                return
        if self.navigation_progress.recovery_step is not None:
            self._continue_navigation_recovery()
            return
        if self.navigation_progress.blocked or self.area_search.blocked:
            self._handle_stuck_recovery(frame)
            return
        previous_ring = self.area_search.completed_rings
        try:
            mode, bearing, distance = self.area_search.navigate(time.time())
        except RuntimeError as error:
            self._handle_area_search_failure(frame, error)
            return
        if self.area_search.phase == "center":
            self.navigation_progress.observe(distance)
        elif mode in ("waypoint", "center"):
            self.navigation_progress.jump_attempts = 0
            self.navigation_progress.recovery_decision_waits = 0
        phase_label = {"center": "接近圈中心", "wait": "等待任务触发", "search": "搜索任务区域"}[self.area_search.phase]
        self.info_set("区域搜索", f"{phase_label}，中心距离 {area.center_distance:.1f} 像素，搜索路线进度 {self.area_search.search_fraction:.0%}")
        if mode == "center":
            self._stop_all_movement()
            waiting = time.time() - self.area_search.arrived_at
            if waiting < 2:
                self.sleep(.2)
                return
            if self.quest_scene.vertical in ("above", "below"):
                approach = self._maybe_vision_approach(frame, None, detect_quest_beacon(frame))
                if approach == "climb":
                    try:
                        self.traversal.begin_jump()
                    except RuntimeError:
                        self._notify_navigation_issue("同一位置跳跃多次没有进展，改用侧向绕行")
                        self.traversal.reset_goal()
                        self._handle_stuck_recovery(frame)
                    else:
                        self._apply_movement(["w"], .4, jump=True)
                elif approach == "drop":
                    self._apply_movement(["w"], .2)
                elif approach == "detour":
                    self._handle_stuck_recovery(frame)
                elif approach != "walk":
                    # 圈中心目标在其他高度且视觉无法确认通路，绕行探路而不是终止任务。
                    self._notify_navigation_issue("已接近黄色圈中心，但目标位于其他高度，改用侧向绕行探路")
                    self._handle_stuck_recovery(frame)
                if approach != "walk":
                    return
            result = self._trigger_ai_decision(frame, has_f_button=has_f, action_text=action_text, event="area_center")
            if result in ("interact", "attack", "skill"):
                return
            if result == "search" or waiting >= 6:
                self.area_search.start_search()
                self.log_info("圈中心未触发任务变化，开始由内向外移动搜索")
            else:
                self.sleep(.2)
            return
        if mode == "complete":
            if self.decision_session.pending is not None:
                self.sleep(.2)
                return
            result = self._trigger_ai_decision(frame, has_f_button=has_f, action_text=action_text, event="area_complete")
            if result in ("interact", "attack", "skill"):
                return
            raise RuntimeError("黄色圈内搜索已经完成，任务仍未推进")
        if mode == "waypoint":
            if self.area_search.completed_rings > previous_ring:
                self.log_info(f"区域搜索已完成 {self.area_search.completed_rings} 圈，检查任务要求")
                self._trigger_ai_decision(frame, has_f_button=has_f, action_text=action_text, event="area_ring")
            return
        if mode == "calibrate":
            self.log_info(f"确认移动方向：按键 {self.area_search.movement_keys}，中心距离 {area.center_distance:.1f} 像素")
            self._apply_movement(self.area_search.movement_keys, .3)
            return
        if mode == "walk":
            self.log_info(f"向区域搜索点移动：按键 {self.area_search.movement_keys}，目标距离 {distance:.1f} 像素，观察移动 {self.area_search.last_displacement:.2f} 像素")
            self._apply_movement(self.area_search.movement_keys, self.area_search.movement_duration(distance))
            return
        self.sleep(.2)

    def _handle_climbing_state(self, frame, climbing_duration: float = 0.0):
        self.quest_scene.phase = "climbing"
        self.quest_scene.stamina = detect_climbing_stamina(frame)
        if self.quest_scene.stamina is not None and self.quest_scene.stamina <= .15:
            self._stop_all_movement()
            raise RuntimeError("攀爬体力接近耗尽，当前没有确认可到达的休息位置")
        self._read_scene_coordinates(frame)
        if climbing_duration > 60:
            self._stop_all_movement()
            raise RuntimeError("攀爬超过60秒仍未抵达通路，需要重新确认地形")
        self._apply_movement(["w"], .3, progress_tracker=self.climbing_progress)
        if self.quest_scene.height_progress is not None and self.quest_scene.height_progress > 0:
            self.traversal.failed_actions.pop("climb_lateral", None)
            self.climbing_progress.movement_seconds = 0.0
            self.climbing_progress.stationary_observed = False
            return
        if not self.climbing_progress.blocked:
            return
        self._stop_all_movement()
        api_url = str(self.config.get("Vision API URL") or os.environ.get("CLEF_API_URL") or "")
        api_key = str(self.config.get("Vision API Key") or os.environ.get("CLEF_API_KEY") or "")
        if self.config.get("Allow Vision Screenshots", False) and api_url and api_key:
            decision = self._call_quest_vision(decide_climbing_route, frame=self.frame,
                                               quest_goal_text=self.guidance_text,
                                               api_url=api_url, api_key=api_key,
                                               model=self.config.get("Vision Model") or "clef",
                                               context=self.quest_scene.context())
            self.clef_call_count += 1
            self.clef_total_tokens += int(decision.get("total_tokens", 0) or 0)
            self.info_set("Clef 调用次数", f"{self.clef_call_count} 次")
            self.info_set("Clef 额度消耗", f"{self.clef_total_tokens} tokens")
            choice = decision.get("choice")
            probability = (decision.get("probabilities") or {}).get(choice, 0)
            if not detect_climbing_state(self.frame).is_climbing:
                raise QuestSceneChangedError("视觉请求期间已经退出攀爬，重新检查地形")
            if probability >= .75 and choice in ("left", "right"):
                attempts = self.traversal.failed_actions.get("climb_lateral", 0)
                if attempts >= 2:
                    raise RuntimeError("攀爬横向移动两次仍没有高度进展，需要其他通路证据")
                self.traversal.failed_actions["climb_lateral"] = attempts + 1
                self._apply_movement(["a" if choice == "left" else "d"], .3, progress_tracker=self.climbing_progress)
                return
            if probability >= .75 and choice == "release":
                self._hold_quest_input(["x"], .1)
                self.traversal.invalidate()
                return
        result = self._trigger_ai_decision(self.frame, event="climbing_blocked")
        if result == "climb_drop":
            self.climbing_start_time = 0.0
            self.traversal.invalidate()
        elif self.climbing_progress.movement_seconds >= 8:
            raise RuntimeError("攀爬持续没有高度或位置进展，当前证据无法确认下一步通路")

    def _detect_teleport_unreachable(self) -> bool:
        """识别地图任务面板的"附近信标无法快速到达"红色提示。"""
        try:
            frame = self.frame
            if frame is None or frame.size == 0:
                return False
            for box in self.ocr(0.68, 0.82, 1.0, 0.92, frame=frame):
                text = getattr(box, "name", "") or ""
                if "无法" in text or ("信标" in text and "到达" in text):
                    return True
        except Exception:
            return False
        return False

    def _detect_closer_to_target_dialog(self, frame: Optional[np.ndarray] = None) -> bool:
        """识别地图快速旅行确认弹窗中的当前位置更接近目标点提示。"""
        try:
            target_frame = frame if frame is not None else self.frame
            if target_frame is None or target_frame.size == 0:
                return False
            for box in self.ocr(0.20, 0.35, 0.80, 0.60, frame=target_frame):
                text = getattr(box, "name", "") or ""
                if ("更接近" in text and "目标点" in text) or "更接近目标点" in text or "当前位置更接近" in text:
                    return True
        except Exception:
            return False
        return False

    def _cancel_and_abort_teleport_for_closer_target(self, frame: Optional[np.ndarray] = None):
        """取消快速旅行确认弹窗，退出地图界面，保持地面步行前往目标。"""
        self.log_info("当前位置更接近目标点，放弃传送，改为直接步行走过去")
        target_frame = frame if frame is not None else self.frame
        cancel_boxes = []
        if target_frame is not None and target_frame.size > 0:
            try:
                cancel_boxes = self.ocr(0.20, 0.55, 0.45, 0.70, match="取消", frame=target_frame)
            except Exception:
                cancel_boxes = []

        if cancel_boxes:
            self.click(cancel_boxes[0])
        else:
            self.click(0.335, 0.628)
        self.sleep(0.5)

        self.teleport_retry_delay = 300.0
        self._close_map_overlays()

    def _find_proceed_button(self, frame: Optional[np.ndarray] = None):
        """寻找任务面板右下角前往按钮。"""
        target_frame = frame if frame is not None else self.frame
        if target_frame is None or target_frame.size == 0:
            return None
        try:
            boxes = self.ocr(0.75, 0.85, 0.98, 0.98, match=["前往", "Proceed"], frame=target_frame)
            if boxes:
                return boxes[0]
        except Exception:
            return None
        return None

    def _close_map_overlays(self):
        for _ in range(3):
            self.send_key("esc", down_time=0.1)
            self.sleep(1.2)
            if self.in_team_and_world():
                break
        self.navigation_progress = QuestProgressTracker()

    def _try_teleport_to_nearest_waypoint(self, current_distance: float) -> bool:
        now = time.time()
        last_attempt = getattr(self, "last_teleport_attempt_time", 0.0)
        if now - last_attempt < getattr(self, "teleport_retry_delay", 30.0):
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

        # 3.5 出现"附近信标无法快速到达"提示时放弃传送，关闭界面改为步行
        if self._detect_teleport_unreachable():
            self.log_info("附近信标无法快速到达，放弃传送改为步行前往任务点")
            self.teleport_retry_delay = 150.0
            self._close_map_overlays()
            return False

        if self._detect_closer_to_target_dialog():
            self._cancel_and_abort_teleport_for_closer_target()
            return False

        # 4. 在地图中检测前往/快速旅行按钮或寻找附近传送信标
        travel_clicked = False
        try:
            if hasattr(self, "click_traval_button") and self.click_traval_button():
                travel_clicked = True
        except Exception:
            pass

        if travel_clicked:
            self.sleep(0.8)
            if self._detect_closer_to_target_dialog():
                self._cancel_and_abort_teleport_for_closer_target()
                return False

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
                if travel_clicked:
                    self.sleep(0.8)
                    if self._detect_closer_to_target_dialog():
                        self._cancel_and_abort_teleport_for_closer_target()
                        return False

        if not travel_clicked:
            # 点击传送点后仍无法前往的，同样放弃传送改为步行
            if self._detect_teleport_unreachable():
                self.log_info("附近信标无法快速到达，放弃传送改为步行前往任务点")
                self.teleport_retry_delay = 150.0
                self._close_map_overlays()
                return False

            proceed_btn = self._find_proceed_button()
            if proceed_btn:
                self.click(proceed_btn)
            else:
                self.click(0.89, 0.92)
            self.sleep(1.0)

            # 点击前往后，若触发"当前位置更接近目标点"提示，取消弹窗并退出，直接走过去
            if self._detect_closer_to_target_dialog():
                self._cancel_and_abort_teleport_for_closer_target()
                return False

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

        self.teleport_retry_delay = 30.0
        self.navigation_progress = QuestProgressTracker()
        return True

    def _decide_vision_escape(self, frame: np.ndarray) -> Optional[str]:
        """受阻时让视觉 AI 判断脱离方式（桥底/屋檐等被结构包夹的场景）。"""
        api_url = str(self.config.get("Vision API URL") or os.environ.get("CLEF_API_URL") or "")
        api_key = str(self.config.get("Vision API Key") or os.environ.get("CLEF_API_KEY") or "")
        if not self.config.get("Allow Vision Screenshots", False) or not api_url.strip() or not api_key.strip():
            return None
        now = time.time()
        if now - getattr(self, "last_vision_escape_time", 0.0) < 15.0:
            return None
        self.last_vision_escape_time = now
        decision = self._call_quest_vision(decide_escape_action,
                frame=frame,
                quest_goal_text=self.guidance_text,
                api_url=api_url,
                api_key=api_key,
                model=str(self.config.get("Vision Model") or "clef"),
                context={
                    "stationary_movement_seconds": round(self.navigation_progress.movement_seconds, 1),
                    "jump_attempts": self.navigation_progress.jump_attempts,
                    "recovery_count": self.navigation_progress.recovery_count,
                },
            )
        self.clef_call_count += 1
        self.clef_total_tokens += int(decision.get("total_tokens", 0) or 0)
        self.info_set("Clef 调用次数", f"{self.clef_call_count} 次")
        self.info_set("Clef 额度消耗", f"{self.clef_total_tokens} tokens")
        choice = decision.get("choice")
        probabilities = decision.get("probabilities") or {}
        choice_probability = float(probabilities.get(choice, 0) or 0)
        self.log_info(
            f"Clef 脱离决策: escape={choice}, probability={choice_probability:.2f}, "
            f"probabilities={probabilities}"
        )
        if choice is None or choice_probability < .5:
            self.log_info("视觉脱离决策置信度不足，沿用跳跃/绕行阶梯")
            return None
        return choice

    def _handle_stuck_recovery(self, frame: np.ndarray):
        self._stop_all_movement()
        # 桥底/屋檐等被结构包夹时跳跃无用，先让视觉 AI 判断脱离方式
        escape = self._decide_vision_escape(frame)
        if escape == "jump":
            self._perform_jump_recovery()
            return
        if escape in ("back_left", "back_right", "retreat"):
            side_key = {"back_left": "a", "back_right": "d"}.get(escape)
            self.navigation_progress.begin_recovery(side_key=side_key, escape_route=True)
            location = str(self.area_search.completed_rings) if self.area_search is not None else "movement_blocked"
            self.decision_session.record("recover", self.last_interaction_text, location, time.time())
            self.log_info(f"视觉决策：按 ({escape}) 执行长距离脱离路线")
            self._continue_navigation_recovery()
            return
        if self.navigation_progress.jump_attempts == 0:
            self._perform_jump_recovery()
            return
        calls_before = self.jev_call_count
        current_distance = self.navigation_progress.best_distance if self.area_search is None else None
        result = self._trigger_ai_decision(frame, event="movement_blocked", current_distance=current_distance)
        if result in ("jump", "stale"):
            return
        if result in ("wait", "observe"):
            if self.jev_call_count > calls_before:
                self.navigation_progress.recovery_decision_waits += 1
            if self.navigation_progress.recovery_decision_waits >= 3:
                raise RuntimeError("跳跃后仍然受阻，三次任务判断没有确认可继续的移动操作")
            self.sleep(.2)
            return
        if result != "recover":
            raise RuntimeError(f"受阻状态返回了无法执行的移动动作：{result}")
        side_key = self._decide_detour_side(frame)
        self.navigation_progress.begin_recovery(side_key=side_key)
        location = str(self.area_search.completed_rings) if self.area_search is not None else "movement_blocked"
        self.decision_session.record("recover", self.last_interaction_text, location, time.time())
        if side_key is not None:
            self.log_info(f"检测到角色前进受阻，第 {self.navigation_progress.recovery_count} 次绕行，按视觉判断方向 ({side_key}) 执行后退、侧向移动与前进")
        else:
            self.log_info(f"检测到角色前进受阻，第 {self.navigation_progress.recovery_count} 次绕行，执行后退、侧向移动与前进")
        self._continue_navigation_recovery()

    def _perform_jump_recovery(self):
        if not self.is_game_window_active():
            return
        if self.decision_session.pending is not None:
            self.sleep(.2)
            return
        if detect_climbing_state(self.frame).is_climbing:
            return
        try:
            self.traversal.begin_jump()
        except RuntimeError:
            # 同一位置跳跃已达上限：重置地形验证，改走绕行阶梯，不再终止任务。
            self._notify_navigation_issue("同一位置跳跃多次没有进展，改用绕行阶梯")
            self.traversal.reset_goal()
            return
        self.navigation_progress.begin_jump()
        keys = self.area_search.movement_keys if self.area_search is not None and self.area_search.camera_heading is not None else ["w"]
        self.log_info(f"移动受阻，第 {self.navigation_progress.jump_attempts} 次尝试向任务方向跳跃，按键 {keys}")
        location = str(self.area_search.completed_rings) if self.area_search is not None else "movement_blocked"
        self.decision_session.record("jump", self.last_interaction_text, location, time.time())
        if self.area_search is not None:
            self.area_search.stationary_seconds = 0.0
        self._apply_movement(keys, .45, jump=True)
        self.sleep(.4)
        self.next_frame()
        if detect_climbing_state(self.frame).is_climbing:
            self.decision_session.mark_transition("climbing_started")

    def _continue_navigation_recovery(self):
        if self.navigation_progress.recovery_step is None:
            return
        keys, duration = self.navigation_progress.next_recovery_movement()
        self._apply_movement(keys, duration)
        if self.navigation_progress.recovery_step is None:
            self.traversal.invalidate()
            self.log_info("侧向绕行完成，重新识别任务方向与距离")

    def _notify_navigation_issue(self, message: str):
        now = time.time()
        last = getattr(self, "last_navigation_notify_time", 0.0)
        if now - last >= 30.0:
            self.last_navigation_notify_time = now
            self.log_error(message, notify=True)
        else:
            self.log_info(message)

    def _log_wait_state(self, frame: np.ndarray, message: str):
        now = time.time()
        if now - getattr(self, "last_wait_state_log_time", 0.0) < 10.0:
            return
        self.last_wait_state_log_time = now
        matches = sum(1 for name in ("char_1_text", "char_2_text", "char_3_text")
                      if self.find_one(name, threshold=.8) is not None)
        self.log_info(f"{message} (队伍栏角色位识别 {matches}/3)")
        try:
            import cv2
            cv2.imwrite(os.path.join("logs", "wait_state_debug.png"), frame)
        except Exception as error:
            self.log_debug(f"保存等待状态诊断截图失败: {error}")

    def _recover_game_window_focus(self):
        now = time.time()
        if now - getattr(self, "last_focus_recover_time", 0.0) < 10.0:
            return
        self.last_focus_recover_time = now
        self.log_info("游戏窗口不在前台，角色已暂停移动，尝试重新激活游戏窗口")
        try:
            self.ensure_in_front()
        except Exception as error:
            logger.warning(f"重新激活游戏窗口失败: {error}")

    def _handle_area_search_failure(self, frame: np.ndarray, error: Exception):
        self._stop_all_movement()
        if isinstance(error, QuestAreaStuckError) and self.area_search is not None and self.area_search.index > 0:
            self.area_search.movement_attempts = 0
            self.area_search.best_waypoint_distance = float("inf")
            self.area_search.stationary_seconds = 0.0
            self.area_search.index += 1
            self._notify_navigation_issue("区域搜索当前路径点被阻挡，跳过该点继续搜索")
            return
        raise error

    def _handle_target_search_failure(self, frame: np.ndarray):
        self._stop_all_movement()
        self._notify_navigation_issue("旋转搜索多圈仍未识别任务指引，重置搜索方向并执行侧向绕行")
        self.target_search.reset()
        self.navigation_progress.begin_recovery(side_key=self._decide_detour_side(frame))
        self._continue_navigation_recovery()

    def _decide_detour_side(self, frame: np.ndarray) -> Optional[str]:
        api_url = str(self.config.get("Vision API URL") or os.environ.get("CLEF_API_URL") or "")
        api_key = str(self.config.get("Vision API Key") or os.environ.get("CLEF_API_KEY") or "")
        if not self.config.get("Allow Vision Screenshots", False) or not api_url.strip() or not api_key.strip():
            return None
        decision = self._call_quest_vision(decide_detour_direction,
                frame=frame,
                quest_goal_text=self.guidance_text,
                api_url=api_url,
                api_key=api_key,
                model=str(self.config.get("Vision Model") or "clef"),
                context={
                    "stuck": True,
                    "distance_meters": self.navigation_progress.best_distance,
                    "stationary_movement_seconds": round(self.navigation_progress.movement_seconds, 1),
                    "jump_attempts": self.navigation_progress.jump_attempts,
                    "previous_recovery_count": self.navigation_progress.recovery_count,
                },
            )
        self.clef_call_count += 1
        self.clef_total_tokens += int(decision.get("total_tokens", 0) or 0)
        self.info_set("Clef 调用次数", f"{self.clef_call_count} 次")
        self.info_set("Clef 额度消耗", f"{self.clef_total_tokens} tokens")
        direction = decision.get("choice")
        confidence = float(decision.get("confidence", 0) or 0)
        probabilities = decision.get("probabilities") or {}
        choice_probability = float(probabilities.get(direction, 0) or 0)
        self.log_info(
            f"Clef 视觉绕行判断: direction={direction}, confidence={confidence:.2f}, "
            f"probability={choice_probability:.2f}, probabilities={probabilities}"
        )
        # confidence 是模型校准置信度而非选项概率，绕行决策看所选选项自身概率。
        if choice_probability < .5:
            self.log_info("视觉绕行判断置信度不足，改用默认绕行路线")
            return None
        return DETOUR_SIDE_KEYS.get(direction)

    def _maybe_vision_approach(self, frame: np.ndarray, current_distance: Optional[float],
                               beacon_result) -> Optional[str]:
        """根据持续保存的地形证据选择接近方式。"""
        cached = self.traversal.current()
        if cached is not None:
            return cached
        api_url = str(self.config.get("Vision API URL") or os.environ.get("CLEF_API_URL") or "")
        api_key = str(self.config.get("Vision API Key") or os.environ.get("CLEF_API_KEY") or "")
        if not self.config.get("Allow Vision Screenshots", False) or not api_url.strip() or not api_key.strip():
            return None
        if self.area_search is None and (current_distance is None or current_distance > 30):
            return None
        goal = self.guidance_text
        beacon_ratio = None
        if beacon_result is not None and beacon_result.found:
            beacon_ratio = round((beacon_result.y + beacon_result.height / 2) / frame.shape[0], 2)
        decision = self._call_quest_vision(decide_approach_action,
                frame=frame,
                quest_goal_text=self.guidance_text,
                api_url=api_url,
                api_key=api_key,
                model=str(self.config.get("Vision Model") or "clef"),
                context={
                    "distance_meters": current_distance,
                    "beacon_screen_y_ratio": beacon_ratio,
                    "beacon_above_character": beacon_ratio is not None and beacon_ratio < .45,
                    "scene": self.quest_scene.context(),
                    "failed_actions": self.traversal.failed_actions,
                },
            )
        self.clef_call_count += 1
        self.clef_total_tokens += int(decision.get("total_tokens", 0) or 0)
        self.info_set("Clef 调用次数", f"{self.clef_call_count} 次")
        self.info_set("Clef 额度消耗", f"{self.clef_total_tokens} tokens")
        choice = decision.get("choice")
        confidence = float(decision.get("confidence", 0) or 0)
        probabilities = decision.get("probabilities") or {}
        choice_probability = float(probabilities.get(choice, 0) or 0)
        self.log_info(
            f"Clef 抵达决策: approach={choice}, confidence={confidence:.2f}, "
            f"probability={choice_probability:.2f}, probabilities={probabilities}"
        )
        # confidence 是模型校准置信度，抵达决策看所选选项自身概率。
        minimum = .75 if choice in ("climb", "drop") else .5
        if choice is None or choice == "unknown" or choice_probability < minimum:
            self.traversal.observation_attempts += 1
            if self.traversal.observation_attempts >= 3:
                # 连续无法确认通路：重置视觉验证并侧向绕行，不终止任务。
                self._notify_navigation_issue("连续三次视觉观察无法确认目标通路，重置后侧向绕行")
                self.traversal.reset_goal()
                return "detour"
            return "unknown"
        try:
            self.traversal.remember(choice)
        except RuntimeError:
            # 相同位置地形操作连续没有进展：重置后换路。
            self._notify_navigation_issue("相同位置的地形操作连续两次没有进展，重置后侧向绕行")
            self.traversal.reset_goal()
            return "detour"
        return choice

    def _stop_all_movement(self):
        for key in ["w", "a", "s", "d", "shift", "space", "f", "e", "q", "t", "x"]:
            try:
                self.send_key_up(key)
            except Exception:
                pass

    def _extract_quest_distance(self, frame: np.ndarray, beacon_result: BeaconResult) -> Optional[float]:
        boxes = self.ocr(.01, .23, .20, .43, frame=frame)
        for box in boxes:
            if getattr(box, "x", 0) <= frame.shape[1] * .05:
                distance = parse_distance_text(box.name, require_unit=True)
                if distance is not None:
                    if self.quest_scene.vertical == "unknown":
                        self.quest_scene.vertical = detect_quest_vertical_hint(frame, (box.x, box.y, box.width, box.height))
                    return distance

        if beacon_result.found:
            fh, fw = frame.shape[:2]
            rx = max(0.0, (beacon_result.x - beacon_result.width) / fw)
            ry = min(.90, (beacon_result.y + beacon_result.height) / fh)
            r_to_x = min(1.0, (beacon_result.x + beacon_result.width * 2) / fw)
            r_to_y = min(.90, (beacon_result.y + beacon_result.height * 3) / fh)
            if ry < r_to_y:
                for box in self.ocr(rx, ry, r_to_x, r_to_y, frame=frame):
                    distance = parse_distance_text(box.name, require_unit=True)
                    if distance is not None:
                        return distance

        return None

    def _apply_camera_turn(self, delta_x: int):
        if not self.is_game_window_active():
            return
        if delta_x == 0:
            return
        try:
            import win32api
            import win32con
            win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, delta_x, 0, 0, 0)
        except Exception:
            from pynput import mouse
            controller = mouse.Controller()
            controller.move(delta_x, 0)

    def _apply_camera_pitch(self, delta_y: int):
        if not self.is_game_window_active():
            return
        if delta_y == 0:
            return
        try:
            import win32api
            import win32con
            win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, 0, delta_y, 0, 0)
        except Exception:
            from pynput import mouse
            controller = mouse.Controller()
            controller.move(0, delta_y)

    def _pitch_toward_quest_beacon(self, prefer_down: bool = False) -> bool:
        """画面中看不到带距离的任务指引点时，垂直调整镜头把它带进画面。

        扫寻方向按指引点最后出现的位置决定（从画面顶部出画先向上，
        从底部出画先向下），先向优先方向扫 4 步再反向扫 4 步；连续多次
        仍看不到则把镜头俯仰恢复到扫寻前的位置并暂缓，由常规寻路继续。
        """
        attempts = getattr(self, "vision_pitch_attempts", 0)
        now = time.time()
        if attempts >= 8:
            if now - getattr(self, "last_vision_pitch_time", 0.0) < 20.0:
                # 扫寻失败：先把镜头俯仰恢复到扫寻前位置，避免长时间仰头/低头
                offset = getattr(self, "vision_pitch_offset", 0)
                if offset:
                    self._apply_camera_pitch(-offset)
                    self.vision_pitch_offset = 0
                return False
            self.vision_pitch_attempts = 0
            attempts = 0
            self.vision_pitch_offset = 0
        self.vision_pitch_attempts = attempts + 1
        self.last_vision_pitch_time = now
        first, second = (80, -80) if prefer_down else (-80, 80)
        delta_y = first if attempts < 4 else second
        self.vision_pitch_offset = getattr(self, "vision_pitch_offset", 0) + delta_y
        self.log_debug(f"镜头未看到带距离的任务指引点，垂直调整视角 dy={delta_y}")
        self._apply_camera_pitch(delta_y)
        self.sleep(0.15)
        return True

    def _reveal_hidden_ui(self, interval: float = 1.5) -> bool:
        """剧情界面按钮在鼠标静止数秒后自动淡出，轻晃鼠标（净位移为零）让 UI 重新显示。

        返回本次调用是否执行了晃动；晃动后强制取新画面，下一轮循环即可识别恢复显示的按钮。
        """
        now = time.time()
        if now - self.last_ui_reveal_time < interval:
            return False
        if not self.is_game_window_active():
            return False
        self.last_ui_reveal_time = now
        try:
            import win32api
            import win32con
            win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, 6, 0, 0, 0)
            self.sleep(0.05)
            win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, -6, 0, 0, 0)
        except Exception:
            try:
                from pynput import mouse
                controller = mouse.Controller()
                controller.move(6, 0)
                self.sleep(0.05)
                controller.move(-6, 0)
            except Exception:
                return False
        self.log_debug("鼠标静止导致剧情 UI 隐藏，轻晃鼠标刷新界面显示")
        self.next_frame()
        return True

    def _apply_movement(self, keys: list, duration: float, progress_tracker=None, jump=False):
        if jump and duration < .08:
            raise ValueError("跳跃移动时间必须覆盖跳跃按键持续时间")
        if not self._can_continue_quest_input():
            self._stop_all_movement()
            return
        if not keys:
            return
        interruptions = self.quest_scene.input_interruptions
        before = self.frame.copy() if any(key in ("w", "a", "s", "d") for key in keys) else None
        elapsed = 0.0
        if jump:
            elapsed = self._hold_quest_input(list(keys) + ["space"], .08)
        if duration > elapsed and self._can_continue_quest_input():
            elapsed += self._hold_quest_input(keys, duration - elapsed)
        if elapsed > 0:
            self.quest_scene.movement_actions += 1
        if self.quest_scene.input_interruptions != interruptions:
            self.traversal.invalidate()
            return
        if self.area_search is not None and progress_tracker is None and elapsed > 0:
            self.area_search.record_movement(keys, elapsed)
        if before is not None:
            self.next_frame()
            motion = detect_background_motion(before, self.frame)
            self.last_motion = motion
            tracker = self.navigation_progress if progress_tracker is None else progress_tracker
            if self.area_search is None or progress_tracker is not None:
                tracker.record_movement(keys, elapsed, moving=motion.moving)
            self.traversal.record_progress(motion.moving, time.time(), duration=elapsed)
            self.quest_scene.attempted_directions.append("+".join(keys))
            self.log_debug(f"移动背景判断: moving={motion.moving}, displacement={motion.displacement_pixels:.2f}, vertical={motion.vertical_pixels:.2f}, points={motion.tracked_points}")

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
        is_frozen_letterbox: bool = False,
        event: str = "interaction",
        current_distance: Optional[float] = None,
    ):
        self._stop_all_movement()
        now = time.time()
        if is_frozen_letterbox:
            event = "frozen_cutscene"
        self.decision_session.observe(self.guidance_text, action_text, now)
        if self.decision_session.pending is not None:
            self.sleep(.2)
            return "observe"
        area_context = self.area_search.context() if self.area_search is not None else None
        location = str(self.area_search.completed_rings) if self.area_search is not None else event
        if has_f_button:
            observation = self._observe_puzzle(frame, action_text)
            if self.quest_scene.goal != self.guidance_text or self.goal_candidate:
                return "stale"
            try:
                obj = self.puzzle.identify(observation)
            except RuntimeError as error:
                # 机关身份无法确认不应终止任务：镜头变化导致对象登记与画面
                # 脱节时清空重学；多同名对象歧义则本帧按画面过期处理。
                if "无法重新确认机关身份" in str(error):
                    self._notify_navigation_issue(f"{error}，重置机关识别后重试")
                    self.puzzle.reset_goal()
                return "stale"
            self.quest_scene.object_id = obj.object_id
            location = f"object:{obj.object_id}:{obj.version}:{self.puzzle.environment_version}"
        progress = location
        if area_context is not None:
            progress += ":" + area_context["phase"]
        if event == "movement_blocked":
            progress += f":{self.navigation_progress.jump_attempts}:{self.navigation_progress.recovery_decision_waits}"
        if has_f_button:
            progress += f":interaction:{self.interaction_decision_waits}"
        history = self.decision_session.context()
        if history and history[-1]["goal"] == self.decision_session.goal and history[-1]["location"] == location:
            progress += f":{len(history)}:{history[-1]['outcome']}"
        if is_frozen_letterbox:
            attempts = sum(item["location"] == event and item["goal"] == self.decision_session.goal for item in self.decision_session.context())
            if attempts >= 3:
                raise RuntimeError("剧情静止期间三次决策没有推进任务，停止重复操作")
            progress += f":{attempts}"
        if not self.decision_session.can_call(event, action_text, progress, now):
            return "observe"
        api_url = str(self.config.get("API URL") or os.environ.get("JEV_API_URL") or "")
        api_key = str(self.config.get("API Key") or os.environ.get("JEV_API_KEY") or "")

        boxes = self.ocr(frame=prepare_quest_ocr_frame(frame))
        ocr_text = sanitize_quest_text("\n".join(box.name for box in boxes if box.name))
        actions = ["wait"]
        if is_frozen_letterbox:
            actions.append("click")
        elif event == "climbing_blocked":
            actions.extend(["climb_continue", "climb_drop"])
        elif event == "movement_blocked":
            actions.append("recover")
            if self.navigation_progress.jump_attempts < 2:
                actions.append("jump")
        elif self.area_search is not None or event == "near_interaction":
            actions.append("search")
        if has_f_button:
            actions.append("interact")
            if is_named_quest_interaction(self.guidance_text, action_text):
                actions.remove("wait")
        at_target = event == "point_arrival" or (area_context is not None and area_context["phase"] in ("wait", "search"))
        if at_target and re.search(r"攻击|攻擊|击碎|擊碎|破坏|破壞|摧毁|摧毀|attack|destroy|break", self.guidance_text, re.IGNORECASE):
            actions.append("attack")
        skill_key = None
        if at_target and re.search(r"技能|能力|工具|感知|声骸|聲骸|skill|ability|tool|sensor", self.guidance_text, re.IGNORECASE):
            key_match = ExplicitControlHandler.CONTROL.search(ocr_text)
            if key_match:
                skill_key = key_match[1].lower()
                actions.append("skill")
        if (at_target and not has_f_button and skill_key is None and "attack" not in actions
                and re.search(r"解谜|解密|机关|搬运|放置|旋转|puzzle|mechanism|rotate|place", self.guidance_text, re.IGNORECASE)):
            raise RuntimeError("当前任务需要机关操作，但画面中没有明确的对象、交互或操作按键")
        failures = self.decision_session.failed_actions(action_text, location)
        actions = [choice for choice in actions if choice not in failures or choice in ("interact", "attack", "skill")]
        if not actions:
            raise RuntimeError("任务动作持续未产生进展，当前没有可继续的操作")
        self.quest_scene.available_actions = actions
        stationary_seconds = self.climbing_progress.movement_seconds if event == "climbing_blocked" else self.navigation_progress.movement_seconds
        movement_context = None if self.last_motion is None else {
            "source": "world_background",
            "moving": self.last_motion.moving,
            "displacement_pixels": round(self.last_motion.displacement_pixels, 2),
            "vertical_pixels": round(self.last_motion.vertical_pixels, 2),
        }
        if self.area_search is not None and event == "movement_blocked":
            stationary_seconds = max(stationary_seconds, self.area_search.stationary_seconds)
            if self.area_search.blocked:
                movement_context = {
                    "source": "minimap_terrain",
                    "moving": False,
                    "displacement_pixels": round(self.area_search.last_displacement, 2),
                }
        context = {
            "event": event,
            "has_f_button": has_f_button,
            "interaction_text": action_text,
            "distance_meters": current_distance,
            "area": area_context,
            "movement": movement_context,
            "stationary_movement_seconds": round(stationary_seconds, 1),
            "jump_attempts": self.navigation_progress.jump_attempts,
            "skill_key": skill_key,
            "recent_actions": self.decision_session.context(),
            "available_actions": actions,
            "scene": self.quest_scene.context(),
            "puzzle": self.puzzle.context(),
        }
        goal_before_request = self.guidance_text

        action: QuestAction = decide_quest_action(
            frame=frame,
            has_f_button=has_f_button,
            action_text=action_text,
            quest_goal_text=self.guidance_text,
            is_frozen_letterbox=is_frozen_letterbox,
            api_url=api_url,
            api_key=api_key,
            ocr_text=ocr_text,
            context=context,
        )
        self._record_jev_usage(action)
        if action.action_type not in actions:
            raise ValueError(f"任务决策返回了当前无法执行的动作：{action.action_type}")
        self.log_info(f"JEV 判断: action={action.action_type}, confidence={action.confidence:.2f}, task={action.task_kind}, event={event}")
        navigation_choice = event == "movement_blocked" and action.recovery_confident
        named_match = has_f_button and is_named_quest_interaction(self.guidance_text, action_text)
        interact_confident = action.action_type == "interact" and named_match

        if not action.confident and not navigation_choice and not interact_confident:
            if has_f_button:
                self.interaction_decision_waits += 1
                if self.interaction_decision_waits >= 3:
                    raise RuntimeError("连续三次无法确认当前交互与任务的关系，需要新的任务或机关提示")
            self.log_info("任务判断置信度不足，继续取得新的画面信息")
            return "observe"
        if not self.is_game_window_active():
            self.log_info("游戏窗口不在前台，丢弃本次交互决策，等待窗口恢复后重试")
            return "stale"
        self.next_frame()
        if not is_frozen_letterbox:
            self._read_quest_goal(self.frame, force=True)
            if (self.guidance_text != goal_before_request or getattr(self, "goal_candidate", None)
                    or detect_letterbox(self.frame).is_letterbox
                    or detect_dialog_advance_indicator(self.frame).found or self.in_combat()):
                self.decision_session.observe(self.guidance_text, action_text, time.time())
                return "stale"
        elif not detect_letterbox(self.frame).is_letterbox:
            return "stale"
        if action.action_type == "interact" and action.key == "f":
            if not has_f_button:
                self.log_info("当前交互按键F不可见，继续搜索任务目标")
                return "search" if "search" in actions else "observe"
            current_f, current_text = self._read_interaction(self.frame)
            if not current_f or current_text != action_text:
                return "stale"
            if not named_match and (action.interaction_relevance is None or action.interaction_relevance < .75):
                return "observe"
            return self._execute_puzzle_action("interact", action_text, location)
        elif action.action_type == "click":
            self.click(0.5, 0.5)
        elif action.action_type == "attack":
            if action.task_kind != "attack" or action.task_confidence < .75:
                return "observe"
            return self._execute_puzzle_action("attack", action_text, location)
        elif action.action_type == "skill":
            if action.task_kind != "skill" or action.task_confidence < .75 or skill_key is None:
                return "observe"
            return self._execute_puzzle_action("skill", action_text, location, expected_key=skill_key)
        elif action.action_type == "climb_drop":
            if not detect_climbing_state(self.frame).is_climbing:
                return "stale"
            self.send_key("x", down_time=.1)
        elif action.action_type == "jump":
            self._perform_jump_recovery()
        if action.action_type not in ("search", "recover", "climb_continue", "jump"):
            self.decision_session.record(action.action_type, action_text, location, time.time())

        if action.wait_seconds > 0:
            self.sleep(action.wait_seconds)
        return action.action_type

    def _record_jev_usage(self, action: QuestAction):
        if action.called_model or getattr(action, "total_tokens", 0) > 0 or getattr(action, "cost", 0.0) > 0:
            count = getattr(self, "jev_call_count", 0) + 1
            tokens = getattr(self, "jev_total_tokens", 0) + getattr(action, "total_tokens", 0)
            cost = getattr(self, "jev_cost_estimate", 0.0) + getattr(action, "cost", 0.0)
            self.jev_call_count = count
            self.jev_total_tokens = tokens
            self.jev_cost_estimate = cost
            self.info_set("JEV 调用次数", f"{count} 次")
            cost_display = f"${cost:.4f}" if cost > 0 else f"{tokens} tokens"
            self.info_set("JEV 额度消耗", cost_display)


    def _handle_story_skip_confirm(self) -> bool:
        """识别"是否确认跳过"提示框与新版"剧情梗概"界面并执行跳过。

        这类弹窗样式没有对应模板，通过 OCR 识别特征文本后点击对应按钮。
        剧情梗概界面在点击跳过后弹出，提供"继续观看"和"跳过剧情"两个按钮。
        """
        try:
            frame = self.frame
            if frame is None or frame.size == 0:
                return False
            question_seen = False
            confirm_button = None
            synopsis_seen = False
            synopsis_skip_button = None
            synopsis_continue_button = None
            for box in self.ocr(0.2, 0.3, 0.9, 0.8, frame=frame):
                text = (getattr(box, "name", "") or "").replace(" ", "")
                if not text:
                    continue
                if "确认跳过" in text or ("是否" in text and "跳过" in text):
                    question_seen = True
                if text in ("确认", "确認"):
                    confirm_button = box
                if "梗概" in text:
                    synopsis_seen = True
                # 按钮文本不超过 8 字，避免误点梗概正文中包含相同关键词的行
                if len(text) <= 8 and "跳过" in text and ("剧情" in text or "劇情" in text):
                    synopsis_skip_button = box
                elif len(text) <= 8 and ("继续" in text or "繼續" in text) and ("观看" in text or "觀看" in text):
                    synopsis_continue_button = box
            if synopsis_seen:
                self.log_info("检测到剧情梗概界面，点击跳过剧情")
                if synopsis_skip_button is not None:
                    self.click(synopsis_skip_button, after_sleep=0.5)
                elif synopsis_continue_button is not None:
                    # "跳过剧情"与"继续观看"关于屏幕中轴对称，按镜像位置点击
                    cx, cy = synopsis_continue_button.center()
                    h, w = frame.shape[:2]
                    self.click(1 - cx / w, cy / h, after_sleep=0.5)
                else:
                    self.click(0.63, 0.655, after_sleep=0.5)
                return True
            if not question_seen:
                return False
            self.log_info("检测到剧情跳过确认框，点击确认")
            if confirm_button is not None:
                self.click(confirm_button, after_sleep=0.5)
            else:
                self.click(0.66, 0.69, after_sleep=0.5)
            return True
        except Exception:
            return False

    def skip_confirm(self) -> bool:
        if self.click_skip_dialog_confirm():
            self.confirm_dialog_checked = True
            return True
        if skip_button := self.find_one('skip_quest_confirm', threshold=0.8):
            self.sleep(0.2)
            self.click(skip_button)
            return True
        if self._handle_story_skip_confirm():
            return True
        # 不用 in_team_and_world 判断：小地图兜底会让剧情对话状态误判为已回到大世界。
        # 跳过完成的准据是所有跳过入口都已从画面上消失。
        if self.find_skip() is not None:
            return False
        frame = self.frame
        if frame is None or frame.size == 0:
            return False
        return not detect_top_left_skip_button(frame).found

    def skip_message(self) -> bool:
        if self.find_one("message", horizontal_variance=0.15):
            if message_dialog := self.find_one("message_dialog", vertical_variance=0.4, horizontal_variance=0.2):
                click = message_dialog.copy(y_offset=2.5 * message_dialog.height)
                click.width = self.width_of_screen(0.63)
                self.click(click, after_sleep=0.2)
                logger.info(f"点击推进短消息对话 {click}")
                return True
        return False
