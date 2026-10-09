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


@dataclass
class QuestCameraAlignment:
    screen_width: Optional[int] = None
    previous_error: Optional[float] = None
    previous_delta: Optional[int] = None
    response_per_pixel: Optional[float] = None

    def reset(self):
        self.screen_width = None
        self.previous_error = None
        self.previous_delta = None
        self.response_per_pixel = None

    def next_turn(self, screen_width, center_x, sensitivity, tolerance_ratio):
        if not math.isfinite(sensitivity) or sensitivity <= 0:
            raise ValueError("镜头旋转灵敏度必须为有限的正数")
        if self.screen_width != screen_width:
            self.reset()
            self.screen_width = screen_width
        command = calculate_camera_turn(
            screen_width, beacon_center_x=center_x, camera_sensitivity=sensitivity,
            tolerance_ratio=tolerance_ratio, max_delta_x=320,
        )
        if not command.need_turn:
            self.reset()
            return command
        error = center_x - screen_width / 2
        reversed_direction = self.previous_error is not None and error * self.previous_error < 0
        if self.previous_delta is not None:
            response = (self.previous_error - error) / self.previous_delta
            if response > 0 and abs(self.previous_error - error) >= 2:
                if self.response_per_pixel is None:
                    self.response_per_pixel = response
                else:
                    # 限制单次画面变化对响应估计的影响。
                    response = max(self.response_per_pixel / 2, min(self.response_per_pixel * 2, response))
                    if reversed_direction:
                        self.response_per_pixel = max(self.response_per_pixel, response)
                    else:
                        self.response_per_pixel = (self.response_per_pixel + response) / 2
        magnitude = max(1, abs(command.delta_x_pixels))
        if self.response_per_pixel is not None:
            # 根据实际转向响应接近中心，保留距离供下一次观察确认。
            magnitude = min(magnitude, max(1, int(abs(error) / self.response_per_pixel * .8)))
        if reversed_direction:
            magnitude = min(magnitude, max(1, abs(self.previous_delta) // 2))
        delta = magnitude if error > 0 else -magnitude
        self.previous_error = error
        self.previous_delta = delta
        return CameraTurnCommand(delta, True, "right" if delta > 0 else "left")


def calculate_camera_turn(
    screen_width: int,
    beacon_center_x: Optional[int] = None,
    minimap_bearing_deg: Optional[float] = None,
    camera_sensitivity: float = 1.0,
    tolerance_ratio: float = 0.02,
    max_delta_x: int = 100,
    minimap_tolerance_deg: float = 6.0,
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

        raw_delta_x = int(diff_x * camera_sensitivity)
        delta_x = max(-max_delta_x, min(max_delta_x, raw_delta_x))
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

        if abs(angle_diff) <= minimap_tolerance_deg:
            return CameraTurnCommand(
                delta_x_pixels=0,
                need_turn=False,
                turn_direction="none"
            )

        # 估算每个角度对应的像素移动量
        pixels_per_deg = (screen_width / 180.0) * camera_sensitivity
        raw_delta_x = int(angle_diff * pixels_per_deg)
        delta_x = max(-max_delta_x, min(max_delta_x, raw_delta_x))
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

    # 偏航角度误差过大（超过 28 度）时优先原地校准朝向，避免环绕画圈
    if abs(angle_error_deg) > 28.0:
        return MovementActionCommand(
            keys=[],
            mode="wait",
            press_duration=0.0
        )

    # 达到 1 至 2 米范围内判定为已到达，停止前进并等待交互触发
    if distance_meters <= 2.0:
        return MovementActionCommand(
            keys=[],
            mode="arrive",
            press_duration=0.0
        )

    # 距离小于等于 20 米时禁止使用闪避快跑冲刺，采用慢走模式平稳接近
    if distance_meters <= 20.0:
        return MovementActionCommand(
            keys=["w"],
            mode="walk",
            press_duration=0.25
        )

    # 大于 20 米时采用远距离加速快跑前进
    return MovementActionCommand(
        keys=["w", "shift"],
        mode="sprint",
        press_duration=0.6
    )
