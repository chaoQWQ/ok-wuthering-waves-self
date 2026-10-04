from pathlib import Path
import unittest

import cv2

from src.utils.QuestBackgroundMotion import detect_background_motion
from src.utils.QuestProgressTracker import QuestProgressTracker
from src.utils.QuestVision import detect_climbing_state


class TestQuestBackgroundMotion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = Path(__file__).parent / "images"
        cls.before = cv2.imread(str(directory / "quest_motion_before.png"))
        cls.after = cv2.imread(str(directory / "quest_motion_after.png"))
        cls.climbing = cv2.imread(str(directory / "quest_climbing.png"))
        if any(frame is None for frame in (cls.before, cls.after, cls.climbing)):
            raise FileNotFoundError("移动与攀爬验证画面不存在")
        cls.motion = detect_background_motion(cls.before, cls.after)
        cls.stationary = detect_background_motion(cls.climbing, cls.climbing)

    def test_recorded_background_movement(self):
        self.assertTrue(self.motion.moving)
        self.assertGreater(self.motion.tracked_points, 20)
        self.assertGreater(abs(self.motion.vertical_pixels), 0.7)

    def test_movement_prevents_recovery_without_distance_progress(self):
        tracker = QuestProgressTracker()
        tracker.observe(5)
        for _ in range(30):
            tracker.record_movement(["w"], 0.3, moving=self.motion.moving)
            tracker.observe(5)
            self.assertFalse(tracker.blocked)
        self.assertEqual(tracker.movement_seconds, 0)

    def test_climbing_requires_continuous_stationary_attempts(self):
        self.assertTrue(detect_climbing_state(self.climbing).is_climbing)
        self.assertFalse(self.stationary.moving)
        tracker = QuestProgressTracker(require_distance=False)
        for _ in range(9):
            tracker.record_movement(["w"], 0.3, moving=self.stationary.moving)
            self.assertFalse(tracker.blocked)
        tracker.record_movement(["w"], 0.3, moving=self.stationary.moving)
        self.assertTrue(tracker.blocked)
        tracker.record_movement(["w"], 0.3, moving=self.motion.moving)
        self.assertFalse(tracker.blocked)

    def test_valid_movement_clears_failed_recovery_count(self):
        tracker = QuestProgressTracker()
        tracker.observe(5)
        tracker.begin_recovery()
        while tracker.recovery_step is not None:
            tracker.next_recovery_movement()
        tracker.record_movement(["w"], 0.3, moving=self.motion.moving)
        self.assertEqual(tracker.recovery_count, 0)


if __name__ == "__main__":
    unittest.main()
