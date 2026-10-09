import time
import unittest

from src.utils.QuestMovementTimeout import QuestMovementTimeout


class TestQuestMovementTimeout(unittest.TestCase):
    goal = "尝试寻找出路"
    coordinate = (-619, -560, 5)

    def test_observation_before_first_movement_does_not_start_timeout(self):
        timer = QuestMovementTimeout()
        for _ in range(100):
            self.assertFalse(timer.observe(self.goal, 14, self.coordinate))
        self.assertFalse(timer.started)
        self.assertEqual(timer.movement_seconds, 0)

    def test_elapsed_time_without_movement_does_not_trigger_timeout(self):
        timer = QuestMovementTimeout()
        timer.begin_movement(self.goal, 14, self.coordinate)
        timer.record_movement(self.goal, .1)
        time.sleep(6.05)
        self.assertFalse(timer.observe(self.goal, 14, self.coordinate))
        self.assertEqual(timer.movement_seconds, .1)

    def test_six_seconds_of_stationary_movement_triggers_timeout(self):
        timer = QuestMovementTimeout()
        for _ in range(23):
            timer.begin_movement(self.goal, 14, self.coordinate)
            timer.record_movement(self.goal, .25)
            self.assertFalse(timer.observe(self.goal, 14, self.coordinate))
        timer.record_movement(self.goal, .25)
        self.assertTrue(timer.observe(self.goal, 14, self.coordinate))
        self.assertFalse(timer.started)

    def test_new_goal_waits_for_its_first_movement(self):
        timer = QuestMovementTimeout()
        timer.begin_movement(self.goal, 14, self.coordinate)
        timer.record_movement(self.goal, 6)
        self.assertFalse(timer.observe("跟随小狐狸", 14, self.coordinate))
        timer.record_movement("跟随小狐狸", 6)
        self.assertFalse(timer.started)
        self.assertEqual(timer.movement_seconds, 0)
        timer.begin_movement("跟随小狐狸", 14, self.coordinate)
        timer.record_movement("跟随小狐狸", .25)
        self.assertFalse(timer.observe("跟随小狐狸", 14, self.coordinate))

    def test_scene_transition_resets_same_goal(self):
        timer = QuestMovementTimeout()
        timer.begin_movement(self.goal, 14, self.coordinate)
        timer.record_movement(self.goal, 5.9)
        timer.reset(self.goal)
        self.assertFalse(timer.observe(self.goal, 14, self.coordinate))
        timer.begin_movement(self.goal, 14, self.coordinate)
        timer.record_movement(self.goal, .25)
        self.assertFalse(timer.observe(self.goal, 14, self.coordinate))
        self.assertEqual(timer.movement_seconds, .25)

    def test_distance_or_coordinate_progress_resets_timeout(self):
        for distance, coordinate in ((13.5, self.coordinate), (14.5, self.coordinate), (14, (-618, -560, 5))):
            with self.subTest(distance=distance, coordinate=coordinate):
                timer = QuestMovementTimeout()
                timer.begin_movement(self.goal, 14, self.coordinate)
                timer.record_movement(self.goal, 6)
                self.assertFalse(timer.observe(self.goal, distance, coordinate))
                self.assertEqual(timer.movement_seconds, 0)
                self.assertFalse(timer.started)

    def test_missing_or_changed_measurements_cannot_confirm_stationary_movement(self):
        for coordinate in (None, self.coordinate):
            with self.subTest(coordinate=coordinate):
                timer = QuestMovementTimeout()
                timer.begin_movement(self.goal, 14, None)
                timer.record_movement(self.goal, 6)
                self.assertFalse(timer.observe(self.goal, None, coordinate))
                self.assertFalse(timer.started)

    def test_unconfirmed_goal_and_missing_measurements_do_not_start_timeout(self):
        timer = QuestMovementTimeout()
        timer.begin_movement("", 14, self.coordinate)
        self.assertFalse(timer.started)
        timer.begin_movement(self.goal, None, None)
        self.assertFalse(timer.started)

    def test_invalid_duration_fails(self):
        for duration in (-1, float("nan"), float("inf")):
            with self.subTest(duration=duration):
                with self.assertRaises(ValueError):
                    QuestMovementTimeout().record_movement(self.goal, duration)
