import unittest
import numpy as np

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
        }
        self.task.current_state = QuestStoryTask.STATE_IDLE
        self.task.last_frame = None
        self.task.letterbox_freeze_start_time = 0.0

    def test_task_states_definition(self):
        self.assertEqual(QuestStoryTask.STATE_IDLE, "IDLE")
        self.assertEqual(QuestStoryTask.STATE_DIALOG, "DIALOG")
        self.assertEqual(QuestStoryTask.STATE_LETTERBOX_CUTSCENE, "LETTERBOX_CUTSCENE")
        self.assertEqual(QuestStoryTask.STATE_COMBAT, "COMBAT")
        self.assertEqual(QuestStoryTask.STATE_NAVIGATE, "NAVIGATE")
        self.assertEqual(QuestStoryTask.STATE_DECIDE_INTERACT, "DECIDE_INTERACT")

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

        self.task._handle_letterbox_state(frame1)
        self.task.letterbox_freeze_start_time = 1000.0
        # 画面剧烈变动，重置静止计时器
        self.task._handle_letterbox_state(frame_moving)
        self.assertEqual(self.task.letterbox_freeze_start_time, 0.0)


if __name__ == "__main__":
    unittest.main()
