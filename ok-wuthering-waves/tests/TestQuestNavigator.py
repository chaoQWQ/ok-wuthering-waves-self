import unittest

from src.utils.QuestNavigator import (
    calculate_camera_turn,
    compute_movement_action,
)


class TestQuestNavigator(unittest.TestCase):

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

    def test_movement_action_sprint(self):
        cmd = compute_movement_action(distance_meters=50.0, angle_error_deg=5.0)
        self.assertEqual(cmd.mode, "sprint")
        self.assertIn("w", cmd.keys)
        self.assertIn("shift", cmd.keys)

    def test_movement_action_run(self):
        cmd = compute_movement_action(distance_meters=8.0, angle_error_deg=5.0)
        self.assertEqual(cmd.mode, "run")
        self.assertEqual(cmd.keys, ["w"])

    def test_movement_action_walk(self):
        cmd = compute_movement_action(distance_meters=2.5, angle_error_deg=5.0)
        self.assertEqual(cmd.mode, "walk")
        self.assertEqual(cmd.keys, ["w"])

    def test_movement_action_arrive(self):
        cmd = compute_movement_action(distance_meters=1.0, angle_error_deg=5.0)
        self.assertEqual(cmd.mode, "arrive")
        self.assertEqual(cmd.keys, [])

    def test_movement_action_angle_error_wait(self):
        cmd = compute_movement_action(distance_meters=50.0, angle_error_deg=60.0)
        self.assertEqual(cmd.mode, "wait")
        self.assertEqual(cmd.keys, [])

    def test_movement_action_negative_distance(self):
        with self.assertRaises(ValueError):
            compute_movement_action(distance_meters=-5.0)


if __name__ == "__main__":
    unittest.main()
