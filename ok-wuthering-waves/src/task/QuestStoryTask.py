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
)
from src.utils.QuestAreaSearch import (
    QuestAreaSearch,
    QuestAreaStuckError,
    detect_quest_area,
    minimap_box,
    minimap_visible,
)
from src.utils.QuestDecisionSession import QuestDecisionSession
from src.utils.QuestNavigator import calculate_camera_turn, compute_movement_action
from src.utils.QuestOcrPrivacy import is_named_quest_interaction, prepare_quest_ocr_frame, quest_goal_from_lines, sanitize_quest_text
from src.utils.QuestProgressTracker import QuestProgressTracker
from src.utils.QuestBackgroundMotion import detect_background_motion
from src.utils.QuestTargetSearch import QuestTargetSearch
from src.utils.QuestVision import (
    BeaconResult,
    ClimbStateResult,
    detect_climbing_state,
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
        self.navigation_error_times: list[float] = []
        self.last_ui_reveal_time: float = 0.0
        self.vision_approach_goal: Optional[str] = None
        self.quest_area_goal: Optional[str] = None

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
        except Exception:
            return True

    def run(self):
        try:
            WWOneTimeTask.run(self)
        except Exception as e:
            logger.warning(f"初始化任务执行环境异常: {e}")

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
        self.quest_combat_count = 0
        self.interaction_decision_waits = 0
        self.jev_call_count = 0
        self.jev_total_tokens = 0
        self.jev_cost_estimate = 0.0
        self.clef_call_count = 0
        self.clef_total_tokens = 0
        self.navigation_error_times = []
        self.last_ui_reveal_time = 0.0
        self.vision_approach_goal = None
        self.quest_area_goal = None
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

            # 1. 优先判定剧情对话
            if self.config.get("Auto Skip Dialog", True):
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
                try:
                    self._handle_letterbox_state(frame)
                except (RuntimeError, ValueError) as error:
                    self._recover_from_navigation_exception(error)
                continue
            else:
                self.letterbox_freeze_start_time = 0.0

            # 4. 判定大世界任务导航与交互
            if not self.in_team_and_world():
                self._stop_all_movement()
                # 剧情对话的跳过按钮等 UI 会在鼠标静止后自动隐藏，轻晃鼠标让其重新显示再识别。
                self._reveal_hidden_ui()
                self._log_wait_state(frame, "当前界面不在队伍大世界（可能处于对话、加载或菜单），暂停移动等待界面恢复")
                self.sleep(.2)
                continue
            try:
                self._handle_world_navigation_and_interaction(frame)
            except (RuntimeError, ValueError) as error:
                self._recover_from_navigation_exception(error)

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

        # 0. 优先检测是否处于攀爬状态
        climb_result = detect_climbing_state(frame)
        if climb_result.is_climbing:
            if self.area_search is not None:
                try:
                    area = self.area_search.find_observation(frame)
                    if area is not None:
                        mx, my, mw, mh = minimap_box(frame)
                        if not self.area_search.observe(area, frame[my:my + mh, mx:mx + mw]):
                            self.area_search = None
                except (RuntimeError, ValueError) as error:
                    self.log_info(f"攀爬期间区域跟踪中断，稍后重新识别黄色区域: {error}")
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
            self.log_info(f"任务要求已经更新：{self.guidance_text}")

        if self.area_search is None:
            area = detect_quest_area(frame)
        else:
            try:
                area = self.area_search.find_observation(frame)
            except (RuntimeError, ValueError) as error:
                self.log_info(f"区域搜索跟踪中断，重新识别黄色任务区域: {error}")
                self.area_search = None
                self.navigation_progress = QuestProgressTracker(require_distance=False)
                area = None
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
        current_distance = self._extract_quest_distance(frame, beacon_result)
        if current_distance is None and not beacon_result.found:
            if re.search(r"跟随.*花朵|Follow.*flower", self.guidance_text, re.IGNORECASE):
                beacon_result = detect_flower_guidance(frame)
                self.navigation_progress.require_distance = False
            else:
                self.navigation_progress.require_distance = True
        previous_distance = self.navigation_progress.best_distance
        self.navigation_progress.observe(current_distance)
        if current_distance is not None and previous_distance is not None and current_distance <= previous_distance - .5:
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
                self.point_arrival_time = None
                self.current_state = self.STATE_DECIDE_INTERACT
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
        if current_distance is not None and current_distance <= 2.0:
            if beacon_result.found:
                beacon_cx = beacon_result.x + beacon_result.width / 2
                beacon_cy = beacon_result.y + beacon_result.height / 2
                if beacon_cy >= height * .60 and abs(beacon_cx - width / 2) <= width * .08 and self.point_approach_seconds < 1.2:
                    self.log_info("任务信标位于角色前下方，向前短步检查剧情触发")
                    self.point_approach_seconds += .15
                    self._apply_movement(["w"], .15)
                    return
            self._stop_all_movement()
            if self.point_arrival_time is None:
                self.point_arrival_time = time.time()
            elif time.time() - self.point_arrival_time >= 10:
                self._notify_navigation_issue("到达任务点后10秒仍没有交互或任务变化，小幅绕行后重新接近")
                self.point_arrival_time = None
                self.point_approach_seconds = 0.0
                try:
                    self.navigation_progress.begin_recovery()
                except RuntimeError:
                    self.log_info("多次绕行仍未触发任务变化，继续等待任务状态刷新")
                    self.sleep(1.0)
                    return
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

        # 移动或调整视角前，若画面中没有带距离的任务指引点，先按 V 重新追踪任务。
        # 范围圈类型的目标没有指引点，跳过此行为。
        if not beacon_result.found and self.quest_area_goal != self.guidance_text:
            now_v = time.time()
            if now_v - getattr(self, "last_v_retrack_time", 0.0) > 12.0:
                self.last_v_retrack_time = now_v
                self.log_info("画面中未发现任务指引点，按 V 重新追踪任务指引")
                self.send_key("v", down_time=0.1)
                self.sleep(0.6)
                return

        # 7. 任务点 30 米内让视觉 AI 决策一次如何抵达目标。送审前保证：
        # a) 小地图任务箭头指向任务点（水平对正）；b) 画面中出现带距离的
        # 黄色任务指引点（未出现时依据任务追踪的上下箭头垂直调整镜头）。
        self.last_vision_approach = None
        if current_distance is not None and current_distance <= 30:
            if arrow_result.found:
                turn_cmd = calculate_camera_turn(
                    screen_width=width,
                    minimap_bearing_deg=arrow_result.bearing_deg,
                    camera_sensitivity=sensitivity,
                    minimap_tolerance_deg=12,
                    max_delta_x=120,
                )
                if turn_cmd.need_turn:
                    self._apply_camera_turn(turn_cmd.delta_x_pixels)
                    self.sleep(0.1)
                    return
            if beacon_result.found:
                self.vision_pitch_attempts = 0
                self.vision_pitch_offset = 0
                beacon_cx = beacon_result.x + beacon_result.width / 2
                beacon_cy = beacon_result.y + beacon_result.height / 2
                # 以指引点在画面中的位置为准逐步居中：偏上抬镜、偏下压镜、
                # 偏左右转镜（指引点旁的箭头即此方位关系）。
                centered = True
                if abs(beacon_cx - width / 2) > width * .18:
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
                self.last_vision_approach = approach
                if approach == "climb":
                    self.log_info("视觉决策：目标在上方，向墙面跳跃攀爬")
                    self._apply_movement(["w"], .45, jump=True)
                    return
                if approach == "drop":
                    self.log_info("视觉决策：目标在下方，向前走出边缘下落")
                    self._apply_movement(["w"], .5)
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
                self.target_search.next_turn(allow_high_count=True)
                self._apply_camera_turn(turn_cmd.delta_x_pixels)
                self.sleep(0.1)
                if current_distance is not None and current_distance <= 20 and abs(angle_error_deg) <= 12:
                    self._apply_movement(["w"], .12)
                return

            # 未启用视觉决策时的确定性兜底：指引点位于画面上方且距离较近，
            # 目标在头顶高处，向墙面跳跃开始攀爬。3 秒内处于攀爬状态时不重复
            # 触发，避免打断正在进行的攀爬。
            if (self.last_vision_approach is None
                    and beacon_result.y + beacon_result.height / 2 < height * .45
                    and current_distance is not None and current_distance <= 12
                    and not detect_climbing_state(frame).is_climbing
                    and time.time() - getattr(self, "last_climbing_seen_time", 0.0) > 3.0
                    and time.time() - getattr(self, "last_climb_start_attempt", 0.0) > 6.0):
                self.last_climb_start_attempt = time.time()
                self.log_info(f"任务指引位于上方 (距离 {current_distance:.1f} 米)，向墙面跳跃开始攀爬")
                self._apply_movement(["w"], .45, jump=True)
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

            self._apply_movement(move_cmd.keys, move_cmd.press_duration)
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
        # 范围圈类型的目标没有指引点，跳过垂直扫寻直接水平旋转，
        # 让范围圈重新进入小地图识别范围。
        if self.quest_area_goal != self.guidance_text and self._pitch_toward_quest_beacon():
            return
        now = time.time()
        if now - self.last_search_log_time > 2.0:
            self.last_search_log_time = now
            self.log_info("视野暂未发现任务信标，正在原地水平旋转视角搜寻目标方位...")
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
        if not force and time.time() - self.guidance_last_read < 2:
            return
        height, width = frame.shape[:2]
        region = Box(round(width * .01), round(height * .23), round(width * .24), round(height * .16))
        boxes = self._ocr_quest_region(frame, region)
        text = quest_goal_from_lines([box.name for box in boxes if box.x <= frame.shape[1] * .05])
        if text:
            self.guidance_text = text
        self.guidance_last_read = time.time()

    def _read_interaction(self, frame: np.ndarray) -> tuple[bool, str]:
        height, width = frame.shape[:2]
        region = Box(round(width * .55), round(height * .38), round(width * .37), round(height * .32))
        boxes = self._ocr_quest_region(frame, region)
        f_box = self.find_one("pick_up_f_hcenter_vcenter", box=region, threshold=.8, frame=frame)
        key_box = None if f_box is None else (f_box.x, f_box.y, f_box.width, f_box.height)
        result = detect_interact_action(frame, ocr_boxes=boxes, key_box=key_box)
        return result.has_f, sanitize_quest_text(result.action_text)

    def _ocr_quest_region(self, frame: np.ndarray, region: Box):
        text_frame, scale = resize_quest_text_frame(frame)
        text_region = Box(round(region.x * scale), round(region.y * scale),
                          round(region.width * scale), round(region.height * scale))
        boxes = self.ocr(box=text_region, frame=text_frame)
        for box in boxes:
            box.x, box.y = round(box.x / scale), round(box.y / scale)
            box.width, box.height = round(box.width / scale), round(box.height / scale)
        return boxes

    def _handle_area_navigation(self, frame: np.ndarray, area, has_f: bool, action_text: str):
        self.current_state = self.STATE_NAVIGATE
        if area is None:
            self._stop_all_movement()
            try:
                self.area_search.lose_observation()
            except RuntimeError:
                self.log_error("连续多帧无法确认黄色任务区域，重置区域搜索并重新识别", notify=True)
                self.area_search = None
                self.navigation_progress = QuestProgressTracker(require_distance=False)
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
        try:
            observed = self.area_search.observe(area, frame[my:my + mh, mx:mx + mw])
        except (RuntimeError, ValueError) as error:
            self.log_info(f"区域观察中断，重置区域搜索: {error}")
            self.area_search = None
            self.navigation_progress = QuestProgressTracker(require_distance=False)
            return
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
        self.log_info(f"正在攀爬，持续 {climbing_duration:.1f} 秒，保持向上移动")
        
        # 引入左下角坐标卡死检测
        import time
        now = time.time()
        if not hasattr(self, 'last_climb_coord_time'):
            self.last_climb_coord_time = now
            self.last_climb_coord = None
            
        if now - self.last_climb_coord_time > 3.0:
            self.last_climb_coord_time = now
            try:
                from src.utils.VideoRouteValidator import coordinate_crop, parse_coordinate_text
                import cv2
                crop = coordinate_crop(frame)
                enlarged = cv2.resize(crop, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_LANCZOS4)
                gray = cv2.cvtColor(enlarged, cv2.COLOR_BGR2GRAY)
                # Binarize with a low threshold because the text is faint gray/white, 
                # but it might be lighter than the floor. Wait, faint gray could be darker or lighter.
                # It's better to just use the original enlarged image for now, OCR models usually handle color images better than badly binarized ones.
                boxes = self.ocr(0.0, 0.0, 1.0, 1.0, frame=enlarged)
                coord_text = " ".join([b.name for b in boxes])
                coord = parse_coordinate_text(coord_text)
                if coord is not None:
                    if self.last_climb_coord is not None:
                        import math
                        dx = coord[0] - self.last_climb_coord[0]
                        dy = coord[1] - self.last_climb_coord[1]
                        dz = coord[2] - self.last_climb_coord[2]
                        dist = math.sqrt(dx*dx + dy*dy + dz*dz)
                        # 如果3秒内坐标移动距离过小，判定为卡死
                        if dist < 2.0:
                            self.log_info(f"左下角坐标 {coord} 几乎未变，判定攀爬卡死，尝试按X下落")
                            self.send_key("x", down_time=0.1)
                            self.sleep(0.5)
                            self.climbing_start_time = 0.0
                            self.navigation_progress.begin_recovery(side_key=self._decide_detour_side(frame))
                            return
                    self.last_climb_coord = coord
            except Exception as e:
                pass
                
        # 超时兜底：仅防御坐标 OCR 与运动检测同时失效的极端情况，正常卡死由
        # 坐标卡死检测（3 秒窗口）与背景运动检测更早接管，高墙攀爬需要更长时间。
        if climbing_duration > 60.0:
            self.log_info("攀爬时间超过60秒，触发超时下落绕行")
            self.send_key("x", down_time=0.1)
            self.sleep(0.5)
            self.climbing_start_time = 0.0
            self.navigation_progress.begin_recovery(side_key=self._decide_detour_side(frame))
            return
        # 起手第一段带跳跃抓墙加速，之后改为普通攀爬：连续跳跃攀爬会快速耗尽
        # 体力导致中途坠落，数米高的墙普通攀爬数秒即可到达。
        self._apply_movement(["w"], 0.3, progress_tracker=self.climbing_progress,
                             jump=climbing_duration < 1.0)
        if not self.climbing_progress.blocked:
            return
        self._stop_all_movement()
        result = self._trigger_ai_decision(self.frame, event="climbing_blocked")
        if result == "climb_drop":
            self.climbing_start_time = 0.0
            self.navigation_progress.begin_recovery(side_key=self._decide_detour_side(self.frame))
            return
        if self.climbing_progress.movement_seconds >= 6:
            self.log_error("连续攀爬没有进展，强制下落绕行", notify=True)
            self.send_key("x", down_time=0.1)
            self.sleep(0.5)
            self.climbing_start_time = 0.0
            self.navigation_progress.begin_recovery(side_key=self._decide_detour_side(self.frame))
            return

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
            # 点击传送点后仍无法前往的，同样放弃传送改为步行
            if self._detect_teleport_unreachable():
                self.log_info("附近信标无法快速到达，放弃传送改为步行前往任务点")
                self.teleport_retry_delay = 150.0
                self._close_map_overlays()
                return False
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

        self.teleport_retry_delay = 30.0
        self.navigation_progress = QuestProgressTracker()
        return True

    def _decide_vision_escape(self, frame: np.ndarray) -> Optional[str]:
        """受阻时让视觉 AI 判断脱离方式（桥底/屋檐等被结构包夹的场景）。"""
        api_url = str(self.config.get("Vision API URL") or os.environ.get("CLEF_API_URL") or "")
        api_key = str(self.config.get("Vision API Key") or os.environ.get("CLEF_API_KEY") or "")
        if not api_url.strip() or not api_key.strip():
            return None
        now = time.time()
        if now - getattr(self, "last_vision_escape_time", 0.0) < 15.0:
            return None
        self.last_vision_escape_time = now
        try:
            decision = decide_escape_action(
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
        except Exception as error:
            self.log_info(f"视觉脱离决策不可用，沿用跳跃/绕行阶梯: {error}")
            return None
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
        keys, duration = self.navigation_progress.next_recovery_movement()
        self._apply_movement(keys, duration)
        if self.navigation_progress.recovery_step is None:
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

    def _recover_from_navigation_exception(self, error: Exception):
        self._stop_all_movement()
        now = time.time()
        previous = getattr(self, "navigation_error_times", None) or []
        self.navigation_error_times = [stamp for stamp in previous if now - stamp < 120]
        self.navigation_error_times.append(now)
        self._notify_navigation_issue(f"剧情导航出现可恢复异常，已重置状态继续执行: {error}")
        self.navigation_progress = QuestProgressTracker()
        self.climbing_progress = QuestProgressTracker(require_distance=False)
        self.climbing_start_time = 0.0
        self.target_search.reset()
        self.area_search = None
        self.point_arrival_time = None
        self.point_approach_seconds = 0.0
        self.letterbox_freeze_start_time = 0.0
        self.last_frame = None
        self.last_motion = None
        self.decision_session = QuestDecisionSession()
        if len(self.navigation_error_times) >= 3:
            self.log_info("两分钟内多次导航异常，额外等待后重试")
            self.sleep(8.0)
        else:
            self.sleep(1.0)

    def _handle_area_search_failure(self, frame: np.ndarray, error: Exception):
        self._stop_all_movement()
        if isinstance(error, QuestAreaStuckError) and self.area_search is not None:
            self.area_search.movement_attempts = 0
            self.area_search.best_waypoint_distance = float("inf")
            self.area_search.stationary_seconds = 0.0
            self.area_search.index += 1
            self._notify_navigation_issue("区域搜索当前路径点被阻挡，跳过该点继续搜索")
            return
        self._notify_navigation_issue(f"区域搜索受阻：{error}，重置搜索状态并重新识别黄色区域")
        self.area_search = None
        self.target_search.reset()
        self.navigation_progress = QuestProgressTracker(require_distance=False)

    def _handle_target_search_failure(self, frame: np.ndarray):
        self._stop_all_movement()
        self._notify_navigation_issue("旋转搜索多圈仍未识别任务指引，重置搜索方向并执行侧向绕行")
        self.target_search.reset()
        self.navigation_progress.begin_recovery(side_key=self._decide_detour_side(frame))
        self._continue_navigation_recovery()

    def _decide_detour_side(self, frame: np.ndarray) -> Optional[str]:
        api_url = str(self.config.get("Vision API URL") or os.environ.get("CLEF_API_URL") or "")
        api_key = str(self.config.get("Vision API Key") or os.environ.get("CLEF_API_KEY") or "")
        if not api_url.strip() or not api_key.strip():
            return None
        try:
            decision = decide_detour_direction(
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
        except Exception as error:
            self.log_info(f"视觉绕行判断不可用，改用默认绕行路线: {error}")
            return None
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
        """任务点 30 米内让视觉 AI 决策一次如何抵达（直走/攀爬/下落/绕行）。

        调用前由调用方保证镜头正对任务点；每个任务目标只调用一次，
        距离退回 35 米外或目标更新后重新武装；未配置视觉 API 或调用
        失败时返回 None，走本地确定性逻辑。
        """
        api_url = str(self.config.get("Vision API URL") or os.environ.get("CLEF_API_URL") or "")
        api_key = str(self.config.get("Vision API Key") or os.environ.get("CLEF_API_KEY") or "")
        if not api_url.strip() or not api_key.strip():
            return None
        if current_distance is None or current_distance > 30:
            if current_distance is None or current_distance > 35:
                self.vision_approach_goal = None
            return None
        goal = self.guidance_text
        if getattr(self, "vision_approach_goal", None) == goal:
            return None
        self.vision_approach_goal = goal
        beacon_ratio = None
        if beacon_result is not None and beacon_result.found:
            beacon_ratio = round((beacon_result.y + beacon_result.height / 2) / frame.shape[0], 2)
        try:
            decision = decide_approach_action(
                frame=frame,
                quest_goal_text=self.guidance_text,
                api_url=api_url,
                api_key=api_key,
                model=str(self.config.get("Vision Model") or "clef"),
                context={
                    "distance_meters": current_distance,
                    "beacon_screen_y_ratio": beacon_ratio,
                    "beacon_above_character": beacon_ratio is not None and beacon_ratio < .45,
                },
            )
        except Exception as error:
            self.log_info(f"视觉抵达决策不可用，使用本地寻路逻辑: {error}")
            return None
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
        if choice is None or choice_probability < .5:
            self.log_info("视觉抵达决策置信度不足，使用本地寻路逻辑")
            return None
        return choice

    def _stop_all_movement(self):
        for key in ["w", "a", "s", "d", "shift"]:
            try:
                self.send_key_up(key)
            except Exception:
                pass

    def _extract_quest_distance(self, frame: np.ndarray, beacon_result: BeaconResult) -> Optional[float]:
        boxes = self.ocr(.01, .23, .20, .43, frame=frame)
        for box in boxes:
            if box.x <= frame.shape[1] * .05:
                distance = parse_distance_text(box.name, require_unit=True)
                if distance is not None:
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

    def _pitch_toward_quest_beacon(self) -> bool:
        """画面中看不到带距离的任务指引点时，垂直调整镜头把它带进画面。

        先向上后向下交替小步尝试；连续多次仍看不到则把镜头俯仰恢复到
        扫寻前的位置并暂缓，由常规水平旋转寻路继续。
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
        delta_y = -80 if attempts < 4 else 80
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
        if not self.is_game_window_active():
            self._stop_all_movement()
            return
        if not keys:
            return
        if self.area_search is not None and progress_tracker is None:
            self.area_search.record_movement(keys, duration)
        before = self.frame.copy() if "w" in keys else None
        try:
            for key in keys:
                self.send_key_down(key)
            if jump:
                self.send_key_down("space")
                self.sleep(.08)
                self.send_key_up("space")
                self.sleep(duration - .08)
            else:
                self.sleep(duration)
        finally:
            try:
                if jump:
                    self.send_key_up("space")
            finally:
                for key in keys:
                    self.send_key_up(key)
        if before is not None:
            self.next_frame()
            motion = detect_background_motion(before, self.frame)
            self.last_motion = motion
            tracker = self.navigation_progress if progress_tracker is None else progress_tracker
            if self.area_search is None or progress_tracker is not None:
                tracker.record_movement(keys, duration, moving=motion.moving)
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
        at_target = event == "point_arrival" or (area_context is not None and area_context["phase"] in ("wait", "search"))
        if at_target and re.search(r"攻击|攻擊|击碎|擊碎|破坏|破壞|摧毁|摧毀|attack|destroy|break", self.guidance_text, re.IGNORECASE):
            actions.append("attack")
        skill_key = None
        if at_target and re.search(r"技能|能力|工具|感知|声骸|聲骸|skill|ability|tool|sensor", self.guidance_text, re.IGNORECASE):
            key_match = re.search(r"(?:按下|按住|点击|點擊|Press|Hold|Tap)\s*[\[（(]?\s*([eEtTqQ])\b", ocr_text, re.IGNORECASE)
            if key_match:
                skill_key = key_match[1].lower()
                actions.append("skill")
        failures = self.decision_session.failed_actions(action_text, location)
        actions = [choice for choice in actions if choice not in failures]
        if not actions:
            raise RuntimeError("任务动作持续未产生进展，当前没有可继续的操作")
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
        interact_confident = action.action_type == "interact" and action.confidence >= 0.4

        if not action.confident and not navigation_choice and not interact_confident:
            if has_f_button:
                self.interaction_decision_waits += 1
                if self.interaction_decision_waits >= 3:
                    raise RuntimeError(f"已识别交互 {action_text}，三次任务判断仍未确认可执行操作")
            self.log_info("任务判断置信度不足，继续取得新的画面信息")
            return "observe"
        if not self.is_game_window_active():
            return "stale"
        self.next_frame()
        if not is_frozen_letterbox:
            self._read_quest_goal(self.frame, force=True)
            if self.guidance_text != goal_before_request or detect_letterbox(self.frame).is_letterbox or detect_dialog_advance_indicator(self.frame).found:
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
            self.send_key("f", down_time=0.1)
        elif action.action_type == "click":
            self.click(0.5, 0.5)
        elif action.action_type == "attack":
            if action.task_kind != "attack" or action.task_confidence < .75:
                return "observe"
            self.click(key="left")
        elif action.action_type == "skill":
            if action.task_kind != "skill" or action.task_confidence < .75 or skill_key is None:
                return "observe"
            self.send_key(skill_key, down_time=.1)
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
        if action.called_model:
            count = getattr(self, "jev_call_count", 0) + 1
            tokens = getattr(self, "jev_total_tokens", 0) + getattr(action, "total_tokens", 0)
            cost = getattr(self, "jev_cost_estimate", 0.0) + getattr(action, "cost", 0.0)
            self.jev_call_count = count
            self.jev_total_tokens = tokens
            self.jev_cost_estimate = cost
            self.info_set("JEV 调用次数", f"{count} 次")
            cost_display = f"${cost:.4f}" if cost > 0 else f"{tokens} tokens"
            self.info_set("JEV 额度消耗", cost_display)


    def skip_confirm(self) -> bool:
        if self.click_skip_dialog_confirm():
            self.confirm_dialog_checked = True
            return True
        if skip_button := self.find_one('skip_quest_confirm', threshold=0.8):
            self.sleep(0.2)
            self.click(skip_button)
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
