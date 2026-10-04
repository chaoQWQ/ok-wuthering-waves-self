import time
import unittest
import numpy as np
from ok import Logger

from src.task.QuestStoryTask import QuestStoryTask


class DummyExecutor:
    paused = False

    def sleep(self, seconds):
        pass


class TestQuestStoryTask(unittest.TestCase):

    def setUp(self):
        self.task = QuestStoryTask.__new__(QuestStoryTask)
        self.task._executor = DummyExecutor()
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
        self.task.last_frame = None
        self.task.letterbox_freeze_start_time = 0.0
        self.task.last_search_log_time = 0.0
        self.task.last_char_switch_time = 0.0
        self.task.last_nav_frame = None
        self.task.stuck_start_time = 0.0
        self.task.last_observed_distance = None
        self.task.stuck_count = 0
        self.task.logger = Logger.get_logger("test")
        self.task.ui_logs = []
        self.task.info_set = lambda k, v: self.task.ui_logs.append((k, v))
        self.task.sent_keys = []
        self.task.send_key = lambda k, **kw: self.task.sent_keys.append(("send", str(k)))
        self.task.send_key_down = lambda k: self.task.sent_keys.append(("down", k))
        self.task.send_key_up = lambda k: self.task.sent_keys.append(("up", k))
        self.task.in_team = lambda: (True, 0, 3)
        self.task.sleep = lambda s: None

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
        img_path = r"C:\Users\zc\.gemini\antigravity\brain\c0a6ae12-fc8b-477d-b1ca-5cbeb3872327\.user_uploaded\media_1791091183192.jpg"
        if not os.path.exists(img_path):
            self.skipTest("大世界信标样本图片不存在")
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
        img_path = r"C:\Users\zc\.gemini\antigravity\brain\c0a6ae12-fc8b-477d-b1ca-5cbeb3872327\.user_uploaded\media_1791091183192.jpg"
        if not os.path.exists(img_path):
            self.skipTest("大世界信标样本图片不存在")
        frame = cv2.imread(img_path)
        self.task._extract_quest_distance = lambda f, b: 12.0
        self.task._handle_world_navigation_and_interaction(frame)
        self.assertTrue(any(act == "down" and k == "w" for act, k in self.task.sent_keys))
        # 验证距离小于等于 20 米时绝对不按 shift 冲刺
        self.assertFalse(any(k == "shift" for act, k in self.task.sent_keys))
        self.assertTrue(any(k == "Log" and "慢走模式" in v for k, v in self.task.ui_logs))

    def test_world_navigation_sprints_over_twenty_meters(self):
        import cv2, os
        img_path = r"C:\Users\zc\.gemini\antigravity\brain\c0a6ae12-fc8b-477d-b1ca-5cbeb3872327\.user_uploaded\media_1791091183192.jpg"
        if not os.path.exists(img_path):
            self.skipTest("大世界信标样本图片不存在")
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
    def test_stuck_detection_and_recovery(self):
        import numpy as np
        frame = np.ones((720, 1280, 3), dtype=np.uint8) * 100
        self.task.last_nav_frame = frame.copy()
        self.task.stuck_start_time = time.time() - 2.0  # 模拟持续卡滞超过 1.2 秒
        self.task.last_observed_distance = 15.0

        recovered = self.task._check_and_handle_stuck(frame, current_distance=15.0)
        self.assertTrue(recovered)
        # 验证发送了脱困按键 's' 与侧向键
        self.assertTrue(any(k == "s" for act, k in self.task.sent_keys))
        self.assertTrue(any(k in ("a", "d") for act, k in self.task.sent_keys))
        self.assertTrue(any(k == "Log" and "受阻卡滞" in v for k, v in self.task.ui_logs))


if __name__ == "__main__":
    unittest.main()

