import time
import unittest
import numpy as np
from ok import Logger

from src.task.QuestStoryTask import QuestStoryTask
from src.utils.QuestProgressTracker import QuestProgressTracker
from src.utils.QuestPuzzleSession import QuestPuzzleSession
from src.utils.QuestTargetSearch import QuestTargetSearch
from src.utils.QuestDecisionSession import QuestDecisionSession
from src.utils.QuestSceneState import QuestSceneState
from src.utils.QuestTraversalController import QuestTraversalController


class DummyMethod:
    width = 1280
    height = 720


class DummyExecutor:
    method = DummyMethod()
    device_manager = type("DummyDeviceManager", (), {"supported_ratio": 16 / 9})()
    paused = False
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)

    def sleep(self, seconds):
        pass

    def next_frame(self, time_out=6):
        return self.frame

    def nullable_frame(self):
        return self.frame


class TestQuestStoryTask(unittest.TestCase):

    def setUp(self):
        self.task = QuestStoryTask.__new__(QuestStoryTask)
        self.task._executor = DummyExecutor()
        self.task._trigger_ai_decision = lambda *args, **kwargs: None
        self.task.config = {
            "Auto Combat in Quest": True,
            "Auto Skip Dialog": True,
            "Letterbox Freeze Wait Seconds": 30.0,
            "API URL": "",
            "API Key": "",
            "Camera Sensitivity": 1.0,
            "Switch to First Character for Movement": True,
        }
        self.task.current_state = QuestStoryTask.STATE_IDLE
        self.task.quest_scene = QuestSceneState()
        self.task.traversal = QuestTraversalController(self.task.quest_scene)
        self.task.puzzle = QuestPuzzleSession()
        self.task.last_frame = None
        self.task.letterbox_freeze_start_time = 0.0
        self.task.last_search_log_time = 0.0
        self.task.last_char_switch_time = 0.0
        self.task.last_nav_frame = None
        self.task.navigation_movement_pending = True
        self.task.navigation_progress = QuestProgressTracker()
        self.task.stuck_start_time = 0.0
        self.task.last_observed_distance = None
        self.task.stuck_count = 0
        self.task.last_teleport_attempt_time = 0.0
        self.task.jev_call_count = 0
        self.task.jev_total_tokens = 0
        self.task.jev_cost_estimate = 0.0
        self.task.guidance_last_read = 0.0
        self.task.guidance_text = ""
        self.task.last_coordinate_read = 0.0
        self.task.useless_interactions = set()
        self.task.logger = Logger.get_logger("test")
        self.task.ui_logs = []
        self.task.info_set = lambda k, v: self.task.ui_logs.append((k, v))
        self.task.sent_keys = []
        self.task.send_key = lambda k, **kw: self.task.sent_keys.append(("send", str(k)))
        self.task.send_key_down = lambda k: self.task.sent_keys.append(("down", k))
        self.task.send_key_up = lambda k: self.task.sent_keys.append(("up", k))
        self.task.in_team = lambda: (True, 0, 3)
        self.task.sleep = lambda s: None
        self.task.is_game_window_active = lambda: True
        self.task.ocr_default_threshold = 0.5
        self.task.ocr = lambda *args, **kwargs: []
        self.task.find_one = lambda *args, **kwargs: None
        self.task.climbing_start_time = 0.0
        self.task.climbing_progress = QuestProgressTracker(require_distance=False)
        self.task.target_search = QuestTargetSearch()
        self.task.area_search = None
        self.task.decision_session = QuestDecisionSession()
        self.task.last_motion = None
        self.task.last_interaction_text = ""
        self.task.point_arrival_time = None
        self.task.point_approach_seconds = 0.0
        self.task.quest_combat_count = 0
        self.task.interaction_decision_waits = 0

    def test_task_states_definition(self):
        self.assertEqual(QuestStoryTask.STATE_IDLE, "IDLE")
        self.assertEqual(QuestStoryTask.STATE_DIALOG, "DIALOG")
        self.assertEqual(QuestStoryTask.STATE_LETTERBOX_CUTSCENE, "LETTERBOX_CUTSCENE")
        self.assertEqual(QuestStoryTask.STATE_COMBAT, "COMBAT")
        self.assertEqual(QuestStoryTask.STATE_NAVIGATE, "NAVIGATE")
        self.assertEqual(QuestStoryTask.STATE_DECIDE_INTERACT, "DECIDE_INTERACT")

    def test_log_info_updates_ui_logs(self):
        self.task.log_info("正在寻找剧情任务目标")
        self.assertTrue(any(k == "Log" and "正在寻找剧情任务目标" in v for k, v in self.task.ui_logs))

    def test_handle_letterbox_state_freezing(self):
        # 模拟两帧完全相同的黑边动画画面
        frame1 = np.zeros((576, 1024, 3), dtype=np.uint8)
        frame1[100:476, :] = 100
        frame2 = frame1.copy()

        # 第一帧输入，记录 last_frame
        self.task._handle_letterbox_state(frame1)
        self.assertIsNotNone(self.task.last_frame)
        self.assertEqual(self.task.letterbox_freeze_start_time, 0.0)

        # 第二帧输入，帧差为 0，开始计时
        self.task._handle_letterbox_state(frame2)
        self.assertGreater(self.task.letterbox_freeze_start_time, 0.0)

    def test_handle_letterbox_state_movement_resets_timer(self):
        frame1 = np.zeros((576, 1024, 3), dtype=np.uint8)
        frame1[100:476, :] = 100
        frame_moving = np.zeros((576, 1024, 3), dtype=np.uint8)
        frame_moving[100:476, :] = 200

        self.task._handle_letterbox_state(frame_moving)
        self.assertEqual(self.task.letterbox_freeze_start_time, 0.0)

    def test_advance_indicator_triggers_dialog_state(self):
        import cv2, os
        img_path = r"C:\Users\zc\.gemini\antigravity\brain\c0a6ae12-fc8b-477d-b1ca-5cbeb3872327\.user_uploaded\media_1791092805274.png"
        if not os.path.exists(img_path):
            self.skipTest("推进样本图片不存在")
        frame = cv2.imread(img_path)
        from src.utils.QuestVision import detect_dialog_advance_indicator
        res = detect_dialog_advance_indicator(frame)
        self.assertTrue(res.found)
        if res.found:
            self.task.current_state = QuestStoryTask.STATE_DIALOG
            self.task.letterbox_freeze_start_time = 0.0
        self.assertEqual(self.task.current_state, QuestStoryTask.STATE_DIALOG)

    def test_top_left_skip_triggers_dialog_state(self):
        import cv2, os
        img_path = r"C:\Users\zc\.gemini\antigravity\brain\c0a6ae12-fc8b-477d-b1ca-5cbeb3872327\.user_uploaded\media_1791096463074.png"
        if not os.path.exists(img_path):
            self.skipTest("左上角跳过样本图片不存在")
        frame = cv2.imread(img_path)
        from src.utils.QuestVision import detect_top_left_skip_button
        res = detect_top_left_skip_button(frame)
        self.assertTrue(res.found)
        if res.found:
            self.task.current_state = QuestStoryTask.STATE_DIALOG
            self.task.letterbox_freeze_start_time = 0.0
        self.assertEqual(self.task.current_state, QuestStoryTask.STATE_DIALOG)

    def test_detect_top_left_skip_button_on_user_uploaded_1791108600262(self):
        import cv2, os
        img_path = r"C:\Users\zc\.gemini\antigravity\brain\c0a6ae12-fc8b-477d-b1ca-5cbeb3872327\.user_uploaded\media_1791108600262.jpg"
        if not os.path.exists(img_path):
            self.skipTest("用户最新上传图片不存在")
        frame = cv2.imread(img_path)
        from src.utils.QuestVision import detect_top_left_skip_button
        res = detect_top_left_skip_button(frame)
        self.assertTrue(res.found)
        self.assertGreaterEqual(res.confidence, 0.70)
        # 验证坐标在屏幕左上角区域
        self.assertLess(res.x, int(frame.shape[1] * 0.25))
        self.assertLess(res.y, int(frame.shape[0] * 0.22))

    def test_skip_message_callable_defined(self):
        self.assertTrue(hasattr(self.task, "skip_message"))
        self.assertTrue(callable(getattr(self.task, "skip_message")))

    def test_world_navigation_moves_forward_when_beacon_found(self):
        import cv2, os
        img_path = r"tests/images/quest_navigation_aligned.png"
        if not os.path.exists(img_path):
            self.skipTest("对齐信标测试图片不存在")
        frame = cv2.imread(img_path)
        self.task._handle_world_navigation_and_interaction(frame)
        self.assertEqual(self.task.current_state, QuestStoryTask.STATE_NAVIGATE)
        # 验证已向底层发送了向前移动按键
        self.assertTrue(any(k == "w" for act, k in self.task.sent_keys))

    def test_world_navigation_triggers_interact_when_f_present(self):
        import cv2, os
        img_path = r"C:\Users\zc\.gemini\antigravity\brain\c0a6ae12-fc8b-477d-b1ca-5cbeb3872327\.user_uploaded\media_1791091315830.jpg"
        if not os.path.exists(img_path):
            self.skipTest("大世界交互样本图片不存在")
        frame = cv2.imread(img_path)
        decision_called = []
        self.task._trigger_ai_decision = lambda *args, **kwargs: decision_called.append(True)
        self.task._handle_world_navigation_and_interaction(frame)
        self.assertEqual(self.task.current_state, QuestStoryTask.STATE_DECIDE_INTERACT)
        self.assertTrue(len(decision_called) > 0)

    def test_ensure_first_character_switches_when_not_in_first_position(self):
        self.task.in_team = lambda: (True, 1, 3)
        switched = self.task._ensure_first_character()
        self.assertTrue(switched)
        self.assertTrue(any(act == "send" and k == "1" for act, k in self.task.sent_keys))
        self.assertTrue(any(k == "Log" and "切换至一号位角色" in v for k, v in self.task.ui_logs))

    def test_ensure_first_character_noop_when_already_in_first_position(self):
        self.task.in_team = lambda: (True, 0, 3)
        switched = self.task._ensure_first_character()
        self.assertFalse(switched)
    def test_world_navigation_stops_within_two_meters(self):
        import cv2, numpy as np
        dummy_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.task._extract_quest_distance = lambda f, b: 1.5
        self.task._handle_world_navigation_and_interaction(dummy_frame)
        # 验证停止前进，不发送 w 键移动
        self.assertFalse(any(act == "down" and k == "w" for act, k in self.task.sent_keys))
        # 验证释放全部按键
        self.assertTrue(any(act == "up" and k == "w" for act, k in self.task.sent_keys))
        self.assertTrue(any(k == "Log" and "停止移动" in v for k, v in self.task.ui_logs))

    def test_world_navigation_slow_walks_under_twenty_meters(self):
        import cv2, os
        img_path = r"tests/images/quest_navigation_aligned.png"
        if not os.path.exists(img_path):
            self.skipTest("对齐信标测试图片不存在")
        frame = cv2.imread(img_path)
        self.task._extract_quest_distance = lambda f, b: 15.0
        self.task._handle_world_navigation_and_interaction(frame)
        self.assertTrue(any(act == "down" and k == "w" for act, k in self.task.sent_keys))
        # 验证距离小于等于 20 米时绝对不按 shift 冲刺
        self.assertFalse(any(k == "shift" for act, k in self.task.sent_keys))
        self.assertTrue(any(k == "Log" and "慢走模式" in v for k, v in self.task.ui_logs))

    def test_world_navigation_sprints_over_twenty_meters(self):
        import cv2, os
        img_path = r"tests/images/quest_navigation_aligned.png"
        if not os.path.exists(img_path):
            self.skipTest("对齐信标测试图片不存在")
        frame = cv2.imread(img_path)
        self.task._extract_quest_distance = lambda f, b: 35.0
        self.task._handle_world_navigation_and_interaction(frame)
        self.assertTrue(any(act == "down" and k == "w" for act, k in self.task.sent_keys))
        # 验证距离大于 20 米时采用冲刺
        self.assertTrue(any(act == "down" and k == "shift" for act, k in self.task.sent_keys))
        self.assertTrue(any(k == "Log" and "冲刺" in v for k, v in self.task.ui_logs))

    def test_world_navigation_user_screenshot_stops_and_interacts(self):
        import cv2, os
        img_path = r"C:\Users\zc\.gemini\antigravity\brain\c0a6ae12-fc8b-477d-b1ca-5cbeb3872327\.user_uploaded\media_1791103623271.jpg"
        if not os.path.exists(img_path):
            self.skipTest("用户最新上传图片不存在")
        frame = cv2.imread(img_path)
        decision_called = []
        self.task._trigger_ai_decision = lambda *args, **kwargs: decision_called.append(True)
        self.task._handle_world_navigation_and_interaction(frame)
        self.assertEqual(self.task.current_state, QuestStoryTask.STATE_DECIDE_INTERACT)
        self.assertTrue(len(decision_called) > 0)
        # 验证触发停止并松开移动按键
        self.assertTrue(any(act == "up" and k == "w" for act, k in self.task.sent_keys))
    def test_is_game_window_active_blocks_actions_when_inactive(self):
        self.task.is_game_window_active = lambda: False
        dummy_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.task._apply_camera_turn(100)
        self.task._apply_movement(["w"], 0.5)
        self.task._handle_world_navigation_and_interaction(dummy_frame)
        # 验证处于后台时不产生任何按键下发动作
        self.assertFalse(any(act == "down" for act, k in self.task.sent_keys))
        self.assertEqual(self.task.current_state, QuestStoryTask.STATE_IDLE)

    def test_passing_by_interaction_ignored_when_distance_over_three_meters(self):
        import cv2, os
        img_path = r"C:\Users\zc\.gemini\antigravity\brain\c0a6ae12-fc8b-477d-b1ca-5cbeb3872327\.user_uploaded\media_1791091183192.jpg"
        if not os.path.exists(img_path):
            self.skipTest("大世界信标样本图片不存在")
        frame = cv2.imread(img_path)
        self.task._extract_quest_distance = lambda f, b: 62.0
        # 注入路过出现 F 键状态
        self.task._read_interaction = lambda f: (True, "调查")
        self.task.find_f_with_text = lambda: True
        self.task._handle_world_navigation_and_interaction(frame)
        # 验证未进入交互状态，保持寻路导航并忽略路过交互
        self.assertEqual(self.task.current_state, QuestStoryTask.STATE_NAVIGATE)
        self.assertTrue(any(k == "Log" and "忽略路过交互" in v for k, v in self.task.ui_logs))

    def test_teleport_triggered_when_distance_over_two_hundred_meters(self):
        import cv2, os
        img_path = r"C:\Users\zc\.gemini\antigravity\brain\c0a6ae12-fc8b-477d-b1ca-5cbeb3872327\.user_uploaded\media_1791109804023.jpg"
        if not os.path.exists(img_path):
            self.skipTest("用户最新上传图片不存在")
        frame = cv2.imread(img_path)
        self.task._extract_quest_distance = lambda f, b: 245.0

        teleport_called = []
        self.task._try_teleport_to_nearest_waypoint = lambda dist: (teleport_called.append(dist) or True)
        self.task._handle_world_navigation_and_interaction(frame)

        self.assertEqual(len(teleport_called), 1)
        self.assertEqual(teleport_called[0], 245.0)

    def test_try_teleport_to_nearest_waypoint_flow(self):
        self.task.in_team_and_world = lambda: False
        self.task.clicked_cords = []
        self.task.click = lambda x, y, **kw: self.task.clicked_cords.append((x, y))
        self.task.wait_in_team_and_world = lambda **kw: True
        self.task.click_traval_button = lambda: True

        res = self.task._try_teleport_to_nearest_waypoint(current_distance=280.0)
        self.assertTrue(res)
        self.assertTrue(any(act == "send" and k == "j" for act, k in self.task.sent_keys))
        self.assertTrue(any(x == 0.89 and y == 0.92 for x, y in self.task.clicked_cords))
        self.assertTrue(any(k == "Log" and "超过 200 米" in v for k, v in self.task.ui_logs))

    def test_record_jev_usage_updates_ui_table(self):
        from src.utils.QuestDecisionEngine import QuestAction
        action = QuestAction(
            action_type="turn",
            key=None,
            description="向右旋转视角走向石板路",
            wait_seconds=0.2,
            turn_pixels=120,
            total_tokens=920,
            cost=0.0018
        )
        self.task._record_jev_usage(action)
        self.assertEqual(self.task.jev_call_count, 1)
        self.assertEqual(self.task.jev_total_tokens, 920)
        self.assertAlmostEqual(self.task.jev_cost_estimate, 0.0018, places=4)
        self.assertTrue(any(k == "JEV 调用次数" and "1 次" in v for k, v in self.task.ui_logs))
        self.assertTrue(any(k == "JEV 额度消耗" and "$0.0018" in v for k, v in self.task.ui_logs))

    def test_extract_quest_distance_with_regex_matching_box(self):
        from src.utils.QuestVision import BeaconResult
        dummy_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        # 模拟 OCR 返回包含 "62米" 的框
        class MockBox:
            def __init__(self, name):
                self.name = name
        self.task.ocr = lambda *args, **kwargs: [MockBox("前往目标地点 62米")]
        dist = self.task._extract_quest_distance(dummy_frame, BeaconResult(found=False, x=0, y=0, width=0, height=0, confidence=0.0))
        self.assertEqual(dist, 62.0)

    def test_detect_closer_to_target_dialog_on_real_image(self):
        import os
        import cv2
        from ok.feature.Box import Box
        from onnxocr.onnx_paddleocr import ONNXPaddleOcr
        img_path = os.path.join(os.path.dirname(__file__), "images", "quest_teleport_closer_dialog.png")
        self.assertTrue(os.path.exists(img_path))
        frame = cv2.imread(img_path)

        ocr_engine = ONNXPaddleOcr(use_openvino=True, use_angle_cls=False)

        def real_ocr(x=0, y=0, to_x=1, to_y=1, match=None, frame=None, **kwargs):
            target = frame if frame is not None else self.task.frame
            h, w = target.shape[:2]
            crop = target[int(h * y):int(h * to_y), int(w * x):int(w * to_x)]
            res = ocr_engine.ocr(crop)
            boxes = []
            if res and res[0]:
                for item in res[0]:
                    pos, (text, score) = item
                    boxes.append(Box(pos[0][0], pos[0][1], pos[2][0] - pos[0][0], pos[2][1] - pos[0][1], name=text, confidence=score))
            return boxes

        self.task.ocr = real_ocr
        self.assertTrue(self.task._detect_closer_to_target_dialog(frame))

        normal_img = os.path.join(os.path.dirname(__file__), "images", "quest_navigation_aligned.png")
        if os.path.exists(normal_img):
            normal_frame = cv2.imread(normal_img)
            self.assertFalse(self.task._detect_closer_to_target_dialog(normal_frame))

    def test_try_teleport_aborts_when_closer_prompt_triggers(self):
        clicks = []
        self.task.click = lambda *args, **kwargs: clicks.append(args)
        in_team_status = [False]  # 初始在地图界面
        self.task.in_team_and_world = lambda: in_team_status[0]
        def do_close():
            in_team_status[0] = True
        self.task._close_map_overlays = do_close
        self.task._detect_teleport_unreachable = lambda: False
        self.task.find_best_match_in_box = lambda *args, **kwargs: None
        self.task.click_traval_button = lambda: False
        self.task._find_proceed_button = lambda *args, **kwargs: None

        # 触发更接近目标点弹窗
        self.task._detect_closer_to_target_dialog = lambda *args, **kwargs: True

        result = self.task._try_teleport_to_nearest_waypoint(260.0)

        # 验证放弃传送直接走过去，返回 False
        self.assertFalse(result)
        # 验证设置了较长的重试间隔 300 秒
        self.assertEqual(self.task.teleport_retry_delay, 300.0)
        # 验证成功调用退出地图界面回到大世界
        self.assertTrue(in_team_status[0])

    def test_story_synopsis_clicks_skip_button(self):
        from ok.feature.Box import Box
        clicks = []
        self.task.click = lambda *args, **kwargs: clicks.append(args)
        self.task._executor.frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        self.task.ocr = lambda *args, **kwargs: [
            Box(880, 350, 160, 40, name="剧情梗概", confidence=0.9),
            Box(600, 684, 210, 46, name="继续观看", confidence=0.9),
            Box(1105, 684, 210, 46, name="跳过剧情", confidence=0.9),
        ]
        self.assertTrue(self.task._handle_story_skip_confirm())
        self.assertEqual(len(clicks), 1)
        self.assertEqual(clicks[0][0].name, "跳过剧情")

    def test_story_synopsis_mirrors_continue_button(self):
        from ok.feature.Box import Box
        clicks = []
        self.task.click = lambda *args, **kwargs: clicks.append(args)
        self.task._executor.frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        self.task.ocr = lambda *args, **kwargs: [
            Box(880, 350, 160, 40, name="剧情梗概", confidence=0.9),
            Box(600, 684, 210, 46, name="继续观看", confidence=0.9),
        ]
        self.assertTrue(self.task._handle_story_skip_confirm())
        x, y = clicks[0][:2]
        # "跳过剧情"与"继续观看"关于屏幕中轴对称
        self.assertAlmostEqual(x, 1 - 705 / 1920, places=2)
        self.assertAlmostEqual(y, 707 / 1080, places=2)

    def test_story_synopsis_fallback_fixed_position(self):
        from ok.feature.Box import Box
        clicks = []
        self.task.click = lambda *args, **kwargs: clicks.append(args)
        self.task._executor.frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        self.task.ocr = lambda *args, **kwargs: [
            Box(880, 350, 160, 40, name="剧情梗概", confidence=0.9),
        ]
        self.assertTrue(self.task._handle_story_skip_confirm())
        x, y = clicks[0][:2]
        self.assertAlmostEqual(x, 0.63, places=2)
        self.assertAlmostEqual(y, 0.655, places=2)

    def test_story_synopsis_body_text_not_mistaken_for_button(self):
        from ok.feature.Box import Box
        clicks = []
        self.task.click = lambda *args, **kwargs: clicks.append(args)
        self.task._executor.frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        self.task.ocr = lambda *args, **kwargs: [
            Box(880, 350, 160, 40, name="剧情梗概", confidence=0.9),
            Box(560, 430, 800, 120, name="接下来穗穗打算去梦州城调查梦州边庭和珥钧堂的情报并跳过剧情简介", confidence=0.9),
        ]
        self.assertTrue(self.task._handle_story_skip_confirm())
        # 正文行不含独立按钮文本，应回退到固定坐标而不是点击正文
        x, y = clicks[0][:2]
        self.assertAlmostEqual(x, 0.63, places=2)
        self.assertAlmostEqual(y, 0.655, places=2)

    def test_story_skip_confirm_still_clicks_confirm(self):
        from ok.feature.Box import Box
        clicks = []
        self.task.click = lambda *args, **kwargs: clicks.append(args)
        self.task._executor.frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        self.task.ocr = lambda *args, **kwargs: [
            Box(700, 500, 500, 60, name="是否确认跳过剧情", confidence=0.9),
            Box(850, 640, 220, 50, name="确认", confidence=0.9),
        ]
        self.assertTrue(self.task._handle_story_skip_confirm())
        self.assertEqual(clicks[0][0].name, "确认")

    def test_story_synopsis_not_detected_without_title(self):
        from ok.feature.Box import Box
        clicks = []
        self.task.click = lambda *args, **kwargs: clicks.append(args)
        self.task._executor.frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        self.task.ocr = lambda *args, **kwargs: [
            Box(1105, 684, 210, 46, name="跳过剧情", confidence=0.9),
        ]
        # 没有梗概标题和确认框问题时不应触发点击
        self.assertFalse(self.task._handle_story_skip_confirm())
        self.assertEqual(len(clicks), 0)

    def test_direct_interact_presses_f_when_arrived(self):
        calls = []
        self.task.guidance_text = "和烟舒聊聊"
        self.task._execute_puzzle_action = \
            lambda action, text, location, expected_key=None: calls.append((action, text, location)) or "interact"
        self.assertTrue(self.task._direct_interact_at_target("烟舒"))
        self.assertEqual(calls, [("interact", "烟舒", "near_interaction")])

    def test_direct_interact_skips_when_session_pending(self):
        self.task.guidance_text = "和烟舒聊聊"
        self.task.decision_session.record("wait", "烟舒", "near_interaction", time.time())
        calls = []
        self.task._execute_puzzle_action = lambda *args, **kwargs: calls.append(args)
        self.assertFalse(self.task._direct_interact_at_target("烟舒"))
        self.assertEqual(calls, [])

    def test_direct_interact_respects_cooldown_and_attempt_cap(self):
        self.task.guidance_text = "和烟舒聊聊"
        calls = []
        self.task._execute_puzzle_action = \
            lambda action, text, location, expected_key=None: calls.append(action) or "interact"
        for _ in range(3):
            self.task.direct_interact_last_time = 0.0
            self.assertTrue(self.task._direct_interact_at_target("烟舒"))
        # 冷却期内不重复按键，重试 3 次后交回 AI 决策兜底
        self.assertFalse(self.task._direct_interact_at_target("烟舒"))
        self.task.direct_interact_last_time = 0.0
        self.assertFalse(self.task._direct_interact_at_target("烟舒"))
        self.assertEqual(len(calls), 3)

    def test_puzzle_prepare_failure_degrades_to_stale(self):
        def raise_prepare(observation, action):
            raise RuntimeError("当前机关在相同状态下连续两次没有产生预期效果，需要新的操作条件")

        self.task.puzzle = type("P", (), {"prepare": staticmethod(raise_prepare), "pending": None})()
        self.task.next_frame = lambda: None
        self.task._observe_puzzle = lambda frame, text: None
        self.task._notify_navigation_issue = lambda message: self.task.ui_logs.append(("Log", message))
        result = self.task._execute_puzzle_action("interact", "烟舒", "near_interaction")
        self.assertEqual(result, "stale")
        self.assertTrue(any("连续两次没有产生预期效果" in v for k, v in self.task.ui_logs))

    def test_navigation_stalled_triggers_after_six_seconds(self):
        from src.utils.QuestTraversalController import QuestTraversalController
        self.task.traversal = QuestTraversalController(self.task.quest_scene)
        self.task.traversal.coordinate = (100.0, 200.0, 3.0)
        self.assertFalse(self.task._navigation_stalled(None))
        self.assertFalse(self.task._navigation_stalled(None))
        self.task.stall_since -= 7
        self.assertTrue(self.task._navigation_stalled(None))
        self.assertIsNone(self.task.stall_base_coordinate)

    def test_navigation_stalled_requires_both_signals_frozen(self):
        # 任一信号出现进展（坐标位移）即重置窗口，两个信号都冻结 6 秒才绕行
        from src.utils.QuestTraversalController import QuestTraversalController
        self.task.traversal = QuestTraversalController(self.task.quest_scene)
        self.task.traversal.coordinate = (100.0, 200.0, 3.0)
        self.task._navigation_stalled(None)
        self.task.stall_since -= 7
        self.task.traversal.coordinate = (100.5, 200.0, 3.0)
        self.assertFalse(self.task._navigation_stalled(None))
        self.assertEqual(self.task.stall_base_coordinate, (100.5, 200.0, 3.0))
        # 进展后重新计时，两信号再次同时冻结满 6 秒才触发
        self.task.stall_since -= 7
        self.assertTrue(self.task._navigation_stalled(None))

    def test_navigation_stalled_resets_on_progress(self):
        from src.utils.QuestTraversalController import QuestTraversalController
        self.task.traversal = QuestTraversalController(self.task.quest_scene)
        self.task.traversal.coordinate = (100.0, 200.0, 3.0)
        self.task._navigation_stalled(None)
        self.task.stall_since -= 5
        self.task.traversal.coordinate = (103.0, 200.0, 3.0)
        self.assertFalse(self.task._navigation_stalled(None))
        self.assertEqual(self.task.stall_base_coordinate, (103.0, 200.0, 3.0))

    def test_tutorial_panel_presses_d_until_confirm(self):
        from ok.feature.Box import Box
        clicks = []
        self.task.click = lambda *args, **kwargs: clicks.append(args)
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        MARKER_Y, CONFIRM_Y, STRIP_Y = 994, 950, 864

        def ocr_page1(f, region):
            if region.y == STRIP_Y:
                return [Box(480, 895, 50, 30, name="A")]
            if region.y == MARKER_Y:
                return [Box(780, 1030, 360, 30, name="切换至最后一页后可关闭界面")]
            return []

        self.task._ocr_quest_region = ocr_page1
        self.assertTrue(self.task._handle_tutorial_panel(frame))
        self.assertIn(("send", "d"), self.task.sent_keys)

        # 最后一页：提示文案消失，A/D 字母与确认按钮仍在 → 点击确认
        self.task.sent_keys.clear()

        def ocr_last(f, region):
            if region.y == STRIP_Y:
                return [Box(1400, 895, 50, 30, name="D")]
            if region.y == CONFIRM_Y:
                return [Box(870, 1000, 180, 46, name="确认")]
            return []

        self.task._ocr_quest_region = ocr_last
        self.assertTrue(self.task._handle_tutorial_panel(frame))
        self.assertEqual(clicks[0][0].name, "确认")
        self.assertNotIn(("send", "d"), self.task.sent_keys)

    def test_tutorial_panel_not_detected_without_marker(self):
        self.task._ocr_quest_region = lambda f, region: []
        self.assertFalse(self.task._handle_tutorial_panel(np.zeros((1080, 1920, 3), dtype=np.uint8)))

    def test_tutorial_panel_stops_pressing_after_limit(self):
        from ok.feature.Box import Box
        self.task._ocr_quest_region = lambda f, region: (
            [Box(480, 895, 50, 30, name="A")] if region.y == 864
            else ([Box(780, 1030, 360, 30, name="切换至最后一页后可关闭界面")] if region.y == 994 else [])
        )
        self.task.tutorial_panel_presses = 15
        self.assertFalse(self.task._handle_tutorial_panel(np.zeros((1080, 1920, 3), dtype=np.uint8)))
        self.assertNotIn(("send", "d"), self.task.sent_keys)

    def test_companion_team_active_caches_detection(self):
        import src.task.QuestStoryTask as quest_story_module
        from src.utils.QuestVision import CompanionLabelResult, detect_companion_label as original

        results = [CompanionLabelResult(found=True, x=0, y=0, width=0, height=0, confidence=0.9)]
        quest_story_module.detect_companion_label = lambda *args, **kwargs: results[0]
        try:
            self.task._executor.frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            self.assertTrue(self.task._companion_team_active())
            self.assertTrue(self.task.companion_team_active)

            # 缓存期内即使检测翻转为False也沿用旧结果
            results[0] = CompanionLabelResult(found=False, x=0, y=0, width=0, height=0, confidence=0.1)
            self.assertTrue(self.task._companion_team_active())

            # 缓存过期后重新检测
            self.task.last_companion_check_time -= 4.0
            self.assertFalse(self.task._companion_team_active())
        finally:
            quest_story_module.detect_companion_label = original

    def test_load_chars_companion_team_builds_single_fallback(self):
        import src.task.QuestStoryTask as quest_story_module
        from src.utils.QuestVision import CompanionLabelResult, detect_companion_label as original

        quest_story_module.detect_companion_label = lambda *args, **kwargs: CompanionLabelResult(
            found=True, x=0, y=0, width=0, height=0, confidence=0.9)
        try:
            self.task._executor.frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            self.task.chars = [None, None, None]
            self.task.load_hotkey = lambda: None
            self.assertTrue(self.task.load_chars())
            self.assertEqual(len(self.task.chars), 3)
            fallback = self.task.chars[0]
            self.assertTrue(fallback.story_fallback)
            self.assertTrue(fallback.is_current_char)
            self.assertEqual(fallback.index, 0)
            # 三个槽位是同一个兜底对象，保证轮换逻辑找不到切换目标而停留在1号位
            self.assertIs(self.task.chars[1], fallback)
            self.assertIs(self.task.chars[2], fallback)
        finally:
            quest_story_module.detect_companion_label = original

    def test_load_chars_without_companion_label_keeps_strict_path(self):
        from unittest import mock

        import src.task.QuestStoryTask as quest_story_module
        from src.task.BaseCombatTask import BaseCombatTask
        from src.task.BaseWWTask import BaseWWTask
        from src.utils.QuestVision import CompanionLabelResult, detect_companion_label as original

        quest_story_module.detect_companion_label = lambda *args, **kwargs: CompanionLabelResult(
            found=False, x=0, y=0, width=0, height=0, confidence=0.1)
        recorded = {}

        def fake_base_load(self):
            recorded["called"] = True
            return True

        try:
            self.task._executor.frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            self.task.chars = [None, None, None]
            self.task.load_hotkey = lambda: None
            with mock.patch.object(BaseWWTask, "in_team", lambda self: (True, 0, 3)), \
                    mock.patch.object(BaseCombatTask, "load_chars", fake_base_load):
                self.assertTrue(self.task.load_chars())
            self.assertTrue(recorded.get("called"), "未识别同行编队时应走基础多角色加载流程")
            self.assertIsNone(self.task.chars[0])
        finally:
            quest_story_module.detect_companion_label = original


    def test_minimap_ring_hint_detects_yellow_ring(self):
        import cv2
        # 低饱和暗黄圆环（区别于高饱和玩家箭头）应被识别为范围圈
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        mh, mw = int(1080 * .24), int(1920 * .16)
        roi_hsv = np.zeros((mh, mw, 3), dtype=np.uint8)
        cv2.circle(roi_hsv, (230, 130), 40, (30, 100, 180), 12)
        frame[:mh, :mw] = cv2.cvtColor(roi_hsv, cv2.COLOR_HSV2BGR)
        self.assertTrue(self.task._minimap_ring_hint(frame))
        # 纯黑小地图不误报
        self.assertFalse(self.task._minimap_ring_hint(np.zeros((1080, 1920, 3), dtype=np.uint8)))

    def test_minimap_ring_hint_ignores_scattered_noise(self):
        import cv2
        # 半透明小地图透出的细碎黄色条纹不应判为黄圈
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        mh, mw = int(1080 * .24), int(1920 * .16)
        for x in range(240, mw, 12):
            cv2.line(frame, (x, 10), (x, mh - 20), (30, 140, 190), 2)
        self.assertFalse(self.task._minimap_ring_hint(frame))


    def test_no_minimap_with_quest_beacon_compatible(self):
        import cv2, os
        img_path = os.path.join(os.path.dirname(__file__), "images", "quest_no_minimap_with_beacon.jpg")
        if not os.path.exists(img_path):
            self.skipTest("剧情信标测试图片不存在")
        frame = cv2.imread(img_path)
        self.task._executor.frame = frame
        from src.utils.QuestAreaSearch import minimap_visible
        from src.utils.QuestVision import detect_quest_beacon
        self.assertFalse(minimap_visible(frame))
        self.assertTrue(detect_quest_beacon(frame).found)

        in_team_status, current_idx, char_count = QuestStoryTask.in_team(self.task)
        self.assertTrue(in_team_status)
        self.assertEqual(current_idx, 0)
        self.assertEqual(char_count, 1)
        self.assertTrue(self.task._can_continue_quest_input())


    def test_single_char_combat_active_in_team(self):
        self.task._executor.frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.task.current_state = QuestStoryTask.STATE_COMBAT
        in_team_status, current_idx, char_count = QuestStoryTask.in_team(self.task)
        self.assertTrue(in_team_status)
        self.assertEqual(current_idx, 0)
        self.assertEqual(char_count, 1)

    def test_perform_quest_combat_loops_until_combat_ends(self):
        call_count = 0

        class DummyChar:
            def perform(self):
                nonlocal call_count
                call_count += 1

        self.task.get_current_char = lambda: DummyChar()
        states = [True, True, False]

        def fake_in_combat():
            return states.pop(0) if states else False

        self.task.in_combat = fake_in_combat
        self.task._perform_quest_combat()
        self.assertEqual(call_count, 2)


if __name__ == "__main__":
    unittest.main()


