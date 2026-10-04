import math
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class CameraTurnCommand:
    delta_x_pixels: int
    need_turn: bool
    turn_direction: str  # 'left', 'right', 'none'


@dataclass
class MovementActionCommand:
    keys: List[str]
    mode: str  # 'arrive', 'walk', 'run', 'sprint', 'wait'
    press_duration: float


def calculate_camera_turn(
    screen_width: int,
    beacon_center_x: Optional[int] = None,
    minimap_bearing_deg: Optional[float] = None,
    camera_sensitivity: float = 1.0,
    tolerance_ratio: float = 0.02
) -> CameraTurnCommand:
    if screen_width <= 0:
        raise ValueError("屏幕宽度必须大于零")

    screen_center_x = screen_width / 2.0
    tolerance_pixels = screen_width * tolerance_ratio

    if beacon_center_x is not None:
        diff_x = beacon_center_x - screen_center_x
        if abs(diff_x) <= tolerance_pixels:
            return CameraTurnCommand(
                delta_x_pixels=0,
                need_turn=False,
                turn_direction="none"
            )

        delta_x = int(diff_x * camera_sensitivity)
        direction = "right" if delta_x > 0 else "left"
        return CameraTurnCommand(
            delta_x_pixels=delta_x,
            need_turn=True,
            turn_direction=direction
        )

    if minimap_bearing_deg is not None:
        norm_deg = minimap_bearing_deg % 360.0
        # 将角度归一化到 [-180, 180] 范围
        if norm_deg > 180.0:
            angle_diff = norm_deg - 360.0
        else:
            angle_diff = norm_deg

        if abs(angle_diff) <= 5.0:
            return CameraTurnCommand(
                delta_x_pixels=0,
                need_turn=False,
                turn_direction="none"
            )

        # 估算每个角度对应的像素移动量
        pixels_per_deg = (screen_width / 90.0) * camera_sensitivity
        delta_x = int(angle_diff * pixels_per_deg)
        direction = "right" if delta_x > 0 else "left"
        return CameraTurnCommand(
            delta_x_pixels=delta_x,
            need_turn=True,
            turn_direction=direction
        )

    return CameraTurnCommand(
        delta_x_pixels=0,
        need_turn=False,
        turn_direction="none"
    )


def compute_movement_action(
    distance_meters: float,
    angle_error_deg: float = 0.0
) -> MovementActionCommand:
    if distance_meters < 0:
        raise ValueError("目标距离不能为负数")

    if abs(angle_error_deg) > 35.0:
        return MovementActionCommand(
            keys=[],
            mode="wait",
            press_duration=0.0
        )

    if distance_meters <= 1.5:
        return MovementActionCommand(
            keys=[],
            mode="arrive",
            press_duration=0.0
        )

    if distance_meters <= 3.5:
        return MovementActionCommand(
            keys=["w"],
            mode="walk",
            press_duration=0.2
        )

    if distance_meters <= 15.0:
        return MovementActionCommand(
            keys=["w"],
            mode="run",
            press_duration=0.5
        )

    return MovementActionCommand(
        keys=["w", "shift"],
        mode="sprint",
        press_duration=0.8
    )
