import unittest

from src.utils.QuestNavigator import (
    QuestCameraAlignment,
    calculate_camera_turn,
    compute_movement_action,
)
from src.utils.QuestSceneState import QuestSceneState
from src.utils.QuestTraversalController import QuestTraversalController


class TestQuestNavigator(unittest.TestCase):

    def test_alignment_keeps_large_turn_for_distant_target(self):
        alignment = QuestCameraAlignment()
        self.assertEqual(alignment.next_turn(1920, 1600, 1, .02).delta_x_pixels, 320)

    def test_alignment_uses_observed_response_after_crossing_center(self):
        alignment = QuestCameraAlignment()
        first = alignment.next_turn(1920, 1110, 1, .02)
        second = alignment.next_turn(1920, 900, 1, .02)
        self.assertEqual(first.delta_x_pixels, 150)
        self.assertEqual(second.delta_x_pixels, -34)
        self.assertLessEqual(abs(second.delta_x_pixels), abs(first.delta_x_pixels) / 2)
        centered = alignment.next_turn(1920, 964, 1, .02)
        self.assertFalse(centered.need_turn)

    def test_alignment_reduces_each_reversed_turn(self):
        alignment = QuestCameraAlignment()
        previous_delta = None
        for center in (1080, 840, 1080, 840):
            command = alignment.next_turn(1920, center, 4, .02)
            if previous_delta is not None:
                self.assertLessEqual(abs(command.delta_x_pixels), abs(previous_delta) / 2)
                self.assertLess(command.delta_x_pixels * previous_delta, 0)
            previous_delta = command.delta_x_pixels

    def test_alignment_reset_discards_previous_view_response(self):
        alignment = QuestCameraAlignment()
        alignment.next_turn(1920, 1110, 1, .02)
        alignment.next_turn(1920, 900, 1, .02)
        alignment.reset()
        self.assertEqual(alignment.next_turn(1920, 1110, 1, .02).delta_x_pixels, 150)
        alignment.next_turn(1280, 900, 1, .02)
        self.assertIsNone(alignment.response_per_pixel)

    def test_single_large_target_change_has_limited_effect_on_response(self):
        alignment = QuestCameraAlignment()
        alignment.next_turn(1920, 1110, 1, .02)
        alignment.next_turn(1920, 900, 1, .02)
        previous_response = alignment.response_per_pixel
        command = alignment.next_turn(1920, 1470, 1, .02)
        self.assertLessEqual(alignment.response_per_pixel, previous_response * 2)
        self.assertGreater(command.delta_x_pixels, 1)

    def test_alignment_invalid_sensitivity_fails(self):
        for sensitivity in (0, -1, float("nan"), float("inf")):
            with self.subTest(sensitivity=sensitivity):
                with self.assertRaises(ValueError):
                    QuestCameraAlignment().next_turn(1920, 1200, sensitivity, .02)

    def test_camera_centering_clears_previous_failure_counts(self):
        controller = QuestTraversalController(QuestSceneState())
        controller.observe_camera(10)
        controller.observe_camera(10)
        self.assertFalse(controller.observe_camera(10))
        self.assertEqual(controller.observation_attempts, 1)
        self.assertTrue(controller.observe_camera(1.5, tolerance=1.8))
        self.assertEqual(controller.camera_failures, 0)
        self.assertEqual(controller.observation_attempts, 0)

    def test_small_camera_improvement_clears_previous_failure_counts(self):
        controller = QuestTraversalController(QuestSceneState())
        controller.observe_camera(2.4)
        controller.observe_camera(2.4)
        self.assertTrue(controller.observe_camera(2.1, tolerance=1.8))
        self.assertEqual(controller.camera_failures, 0)

    def test_unchanged_camera_still_requests_new_observation(self):
        controller = QuestTraversalController(QuestSceneState())
        self.assertTrue(controller.observe_camera(10, tolerance=1.8))
        self.assertTrue(controller.observe_camera(10, tolerance=1.8))
        self.assertFalse(controller.observe_camera(10, tolerance=1.8))

    def test_camera_turn_with_beacon_centered(self):
        screen_width = 1920
        # 信标正处于屏幕中央 960
        cmd = calculate_camera_turn(screen_width, beacon_center_x=960)
        self.assertFalse(cmd.need_turn)
        self.assertEqual(cmd.delta_x_pixels, 0)
        self.assertEqual(cmd.turn_direction, "none")

    def test_camera_turn_with_beacon_right(self):
        screen_width = 1920
        # 信标在屏幕右侧 1400
        cmd = calculate_camera_turn(screen_width, beacon_center_x=1400, camera_sensitivity=1.0)
        self.assertTrue(cmd.need_turn)
        self.assertGreater(cmd.delta_x_pixels, 0)
        self.assertEqual(cmd.turn_direction, "right")

    def test_camera_turn_with_beacon_left(self):
        screen_width = 1920
        # 信标在屏幕左侧 400
        cmd = calculate_camera_turn(screen_width, beacon_center_x=400, camera_sensitivity=1.0)
        self.assertTrue(cmd.need_turn)
        self.assertLess(cmd.delta_x_pixels, 0)
        self.assertEqual(cmd.turn_direction, "left")

    def test_camera_turn_with_minimap_angle(self):
        screen_width = 1920
        # 箭头朝向顺时针 45 度（右上方）
        cmd_right = calculate_camera_turn(screen_width, minimap_bearing_deg=45.0)
        self.assertTrue(cmd_right.need_turn)
        self.assertGreater(cmd_right.delta_x_pixels, 0)
        self.assertEqual(cmd_right.turn_direction, "right")

        # 箭头朝向顺时针 315 度（左上方，等价于 -45 度）
        cmd_left = calculate_camera_turn(screen_width, minimap_bearing_deg=315.0)
        self.assertTrue(cmd_left.need_turn)
        self.assertLess(cmd_left.delta_x_pixels, 0)
        self.assertEqual(cmd_left.turn_direction, "left")

        # 箭头朝向 0 度（正前方）
        cmd_straight = calculate_camera_turn(screen_width, minimap_bearing_deg=1.0)
        self.assertFalse(cmd_straight.need_turn)
        self.assertEqual(cmd_straight.delta_x_pixels, 0)

    def test_camera_turn_invalid_width(self):
        with self.assertRaises(ValueError):
            calculate_camera_turn(0, beacon_center_x=100)

    def test_camera_turn_respects_minimap_limit(self):
        for bearing in (30.0, 90.0, 180.0, 270.0):
            with self.subTest(bearing=bearing):
                cmd = calculate_camera_turn(1920, minimap_bearing_deg=bearing, max_delta_x=120)
                self.assertTrue(cmd.need_turn)
                self.assertLessEqual(abs(cmd.delta_x_pixels), 120)

    def test_movement_action_sprint(self):
        cmd = compute_movement_action(distance_meters=50.0, angle_error_deg=5.0)
        self.assertEqual(cmd.mode, "sprint")
        self.assertIn("w", cmd.keys)
        self.assertIn("shift", cmd.keys)

    def test_movement_action_walk_under_twenty_meters(self):
        # 距离小于等于 20 米时禁止使用闪避快跑，采用慢走模式
        cmd_8m = compute_movement_action(distance_meters=8.0, angle_error_deg=5.0)
        self.assertEqual(cmd_8m.mode, "walk")
        self.assertEqual(cmd_8m.keys, ["w"])

        cmd_20m = compute_movement_action(distance_meters=20.0, angle_error_deg=5.0)
        self.assertEqual(cmd_20m.mode, "walk")
        self.assertEqual(cmd_20m.keys, ["w"])

    def test_movement_action_walk(self):
        cmd = compute_movement_action(distance_meters=2.5, angle_error_deg=5.0)
        self.assertEqual(cmd.mode, "walk")
        self.assertEqual(cmd.keys, ["w"])

    def test_movement_action_arrive(self):
        cmd_1m = compute_movement_action(distance_meters=1.0, angle_error_deg=5.0)
        self.assertEqual(cmd_1m.mode, "arrive")
        self.assertEqual(cmd_1m.keys, [])

        cmd_2m = compute_movement_action(distance_meters=2.0, angle_error_deg=5.0)
        self.assertEqual(cmd_2m.mode, "arrive")
        self.assertEqual(cmd_2m.keys, [])

    def test_movement_action_angle_error_wait(self):
        cmd = compute_movement_action(distance_meters=50.0, angle_error_deg=60.0)
        self.assertEqual(cmd.mode, "wait")
        self.assertEqual(cmd.keys, [])

    def test_movement_action_negative_distance(self):
        with self.assertRaises(ValueError):
            compute_movement_action(distance_meters=-5.0)


if __name__ == "__main__":
    unittest.main()
