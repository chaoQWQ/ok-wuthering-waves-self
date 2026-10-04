from datetime import datetime
from pathlib import Path
import re
import unittest

from src.utils.QuestNavigator import compute_movement_action
from src.utils.QuestProgressTracker import QuestProgressTracker


class TestQuestProgressTracker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lines = (Path(__file__).parent / "data" / "quest_counter_navigation.txt").read_text(encoding="utf-8-sig").splitlines()
        cls.movements = []
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
            tracker.record_movement(action.keys, action.press_duration)
            if tracker.blocked:
                self.assertEqual(index, 5)
                self.assertLess((timestamp - samples[0][0]).total_seconds(), 5)
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
            tracker.record_movement(action.keys, action.press_duration)
            closest_distance = distance if closest_distance is None else min(closest_distance, distance)
        self.assertGreater(reductions, 3)

    def test_recovery_continues_through_recorded_stationary_samples(self):
        tracker = QuestProgressTracker()
        stationary = [distance for _, distance in self.movements if distance == 5]
        tracker.observe(stationary[0])
        tracker.begin_recovery()
        actions = []
        for distance in stationary[:6]:
            tracker.observe(distance)
            actions.append(tracker.next_recovery_movement()[0])
        self.assertEqual(actions[0], ["s"])
        self.assertEqual(actions[1:3], [["a"], ["a"]])
        self.assertEqual(actions[3:5], [["w", "a"], ["w", "a"]])
        self.assertEqual(actions[5], ["w"])
        self.assertIsNone(tracker.recovery_step)

    def test_persistent_obstruction_terminates_after_both_directions(self):
        tracker = QuestProgressTracker()
        sides = []
        for _, distance in self.movements:
            if distance != 5:
                continue
            tracker.observe(distance)
            if tracker.recovery_step is not None:
                keys, duration = tracker.next_recovery_movement()
                tracker.record_movement(keys, duration)
                if keys in (["a"], ["d"]):
                    sides.append(keys[0])
            else:
                action = compute_movement_action(distance)
                tracker.record_movement(action.keys, action.press_duration)
                if tracker.blocked:
                    if tracker.recovery_count == 4:
                        with self.assertRaisesRegex(RuntimeError, "四次绕行"):
                            tracker.begin_recovery()
                        self.assertEqual(set(sides), {"a", "d"})
                        return
                    tracker.begin_recovery()
        self.fail("持续受阻没有终止")


if __name__ == "__main__":
    unittest.main()
