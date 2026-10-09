from datetime import datetime
from pathlib import Path
import itertools
import re
import unittest

import cv2

from src.utils.QuestBackgroundMotion import detect_background_motion
from src.utils.QuestNavigator import compute_movement_action
from src.utils.QuestProgressTracker import QuestProgressTracker


class TestQuestProgressTracker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lines = (Path(__file__).parent / "data" / "quest_counter_navigation.txt").read_text(encoding="utf-8-sig").splitlines()
        cls.movements = []
        frame = cv2.imread(str(Path(__file__).parent / "images" / "quest_navigation_start.png"))
        cls.stationary = detect_background_motion(frame, frame).moving
        for line in cls.lines:
            match = re.search(r"距离目标 (\d+(?:\.\d+)?) 米", line)
            if match:
                cls.movements.append((datetime.strptime(line[:23], "%Y-%m-%d %H:%M:%S,%f"), float(match.group(1))))

    def test_recorded_counter_distance_triggers_recovery(self):
        tracker = QuestProgressTracker()
        samples = [(timestamp, distance) for timestamp, distance in self.movements if distance == 5]
        self.assertGreater(len(samples), 100)
        for index, (timestamp, distance) in enumerate(samples):
            tracker.observe(distance)
            action = compute_movement_action(distance)
            tracker.record_movement(action.keys, action.press_duration, moving=self.stationary)
            if tracker.blocked:
                self.assertEqual(index, 11)
                self.assertLess((timestamp - samples[0][0]).total_seconds(), 10)
                break
        else:
            self.fail("记录中的连续前进没有触发绕行")

    def test_recorded_progress_clears_movement_time(self):
        tracker = QuestProgressTracker()
        closest_distance = None
        reductions = 0
        for _, distance in self.movements:
            if distance == 5:
                break
            tracker.observe(distance)
            if closest_distance is not None and distance <= closest_distance - 0.5:
                self.assertEqual(tracker.movement_seconds, 0)
                reductions += 1
            action = compute_movement_action(distance)
            tracker.record_movement(action.keys, action.press_duration, moving=self.stationary)
            closest_distance = distance if closest_distance is None else min(closest_distance, distance)
        self.assertGreater(reductions, 3)

    def test_recovery_continues_through_recorded_stationary_samples(self):
        tracker = QuestProgressTracker()
        stationary = [distance for _, distance in self.movements if distance == 5]
        tracker.observe(stationary[0])
        tracker.begin_recovery()
        actions = []
        for distance in stationary[:4]:
            tracker.observe(distance)
            actions.append(tracker.next_recovery_movement()[0])
        self.assertEqual(actions[0], ["s"])
        self.assertEqual(actions[1], ["d"])
        self.assertEqual(actions[2], ["w", "d"])
        self.assertEqual(actions[3], ["w"])
        self.assertIsNone(tracker.recovery_step)

    def test_persistent_obstruction_terminates_after_both_directions(self):
        tracker = QuestProgressTracker()
        sides = []
        for _, distance in itertools.cycle(self.movements):
            if distance != 5:
                continue
            tracker.observe(distance)
            if tracker.recovery_step is not None:
                keys, duration = tracker.next_recovery_movement()
                tracker.record_movement(keys, duration, moving=self.stationary)
                if keys in (["a"], ["d"]):
                    sides.append(keys[0])
            else:
                action = compute_movement_action(distance)
                tracker.record_movement(action.keys, action.press_duration, moving=self.stationary)
                if tracker.blocked:
                    if tracker.recovery_count == 8:
                        with self.assertRaisesRegex(RuntimeError, "八次短绕行"):
                            tracker.begin_recovery()
                        self.assertEqual(set(sides), {"a", "d"})
                        return
                    tracker.begin_recovery()
        self.fail("持续受阻没有终止")

    def test_recovery_side_key_overrides_alternating_direction(self):
        tracker = QuestProgressTracker()
        tracker.begin_recovery(side_key="a")
        self.assertEqual(tracker.next_recovery_movement()[0], ["s"])
        self.assertEqual(tracker.next_recovery_movement()[0], ["a"])
        for _ in range(2):
            tracker.next_recovery_movement()
        self.assertIsNone(tracker.recovery_step)
        tracker.begin_recovery(side_key="d")
        self.assertEqual(tracker.next_recovery_movement()[0], ["s"])
        self.assertEqual(tracker.next_recovery_movement()[0], ["d"])

    def test_recovery_falls_back_to_alternating_direction(self):
        tracker = QuestProgressTracker()
        tracker.begin_recovery(side_key="d")
        self.assertEqual(tracker.next_recovery_movement()[0], ["s"])
        self.assertEqual(tracker.next_recovery_movement()[0], ["d"])
        for _ in range(2):
            tracker.next_recovery_movement()
        tracker.begin_recovery(side_key="forward")
        self.assertEqual(tracker.next_recovery_movement()[0], ["s"])
        self.assertEqual(tracker.next_recovery_movement()[0], ["a"])

    def test_recorded_obstruction_limits_jump_attempts(self):
        tracker = QuestProgressTracker()
        stationary = [distance for _, distance in self.movements if distance == 5]
        for attempt in range(2):
            for distance in stationary[:12]:
                tracker.observe(distance)
                tracker.record_movement(["w"], .25, moving=self.stationary)
            self.assertTrue(tracker.blocked)
            tracker.begin_jump()
            self.assertEqual(tracker.jump_attempts, attempt + 1)
            self.assertFalse(tracker.blocked)
        with self.assertRaisesRegex(RuntimeError, "两次跳跃"):
            tracker.begin_jump()

    def test_recorded_distance_progress_allows_jumping_at_new_obstacle(self):
        tracker = QuestProgressTracker()
        previous_distance = None
        for _, distance in self.movements:
            tracker.observe(distance)
            if previous_distance is not None and distance < previous_distance - .5:
                self.assertEqual(tracker.jump_attempts, 0)
                return
            if tracker.jump_attempts == 0:
                tracker.begin_jump()
            previous_distance = distance
        self.fail("导航记录缺少距离缩减结果")


if __name__ == "__main__":
    unittest.main()
