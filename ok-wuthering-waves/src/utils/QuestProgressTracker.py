from dataclasses import dataclass
from typing import Optional


@dataclass
class QuestProgressTracker:
    best_distance: Optional[float] = None
    movement_seconds: float = 0.0
    recovery_count: int = 0
    recovery_step: Optional[int] = None
    require_distance: bool = True
    stationary_observed: bool = False
    jump_attempts: int = 0
    recovery_decision_waits: int = 0
    recovery_side_key: Optional[str] = None
    escape_route: bool = False

    def observe(self, distance: Optional[float]):
        if distance is None or self.recovery_step is not None:
            return
        if self.best_distance is None:
            self.best_distance = distance
        elif distance <= self.best_distance - 0.5:
            self.best_distance = distance
            self.movement_seconds = 0.0
            self.recovery_count = 0
            self.jump_attempts = 0
            self.recovery_decision_waits = 0

    def record_movement(self, keys: list[str], duration: float, moving: Optional[bool] = None):
        if self.recovery_step is None and "w" in keys:
            self.stationary_observed = moving is False
            if moving is False:
                self.movement_seconds += duration
            else:
                self.movement_seconds = 0.0

    @property
    def blocked(self) -> bool:
        distance_known = self.best_distance is not None or not self.require_distance
        return distance_known and self.stationary_observed and self.movement_seconds >= 3.0 - 1e-6

    def begin_recovery(self, side_key: Optional[str] = None, escape_route: bool = False):
        if self.recovery_step is not None:
            return
        if self.recovery_count >= 4:
            raise RuntimeError("任务距离持续没有缩减，四次绕行均未通过，请检查任务目标附近的道路")
        self.recovery_count += 1
        self.recovery_step = 0
        self.recovery_side_key = side_key if side_key in ("a", "d", "s") else None
        self.escape_route = escape_route
        self.movement_seconds = 0.0
        self.recovery_decision_waits = 0

    def begin_jump(self):
        if self.jump_attempts >= 2:
            raise RuntimeError("同一处受阻已经尝试两次跳跃，需要检查其他通行方式")
        self.jump_attempts += 1
        self.movement_seconds = 0.0
        self.stationary_observed = False

    def next_recovery_movement(self) -> tuple[list[str], float]:
        if self.recovery_step is None:
            raise RuntimeError("绕行尚未开始")
        side_key = self.recovery_side_key or ("a" if self.recovery_count % 2 else "d")
        if self.recovery_side_key == "s":
            movements = ((["s"], .3), (["s"], .3), (["s"], .3))
        elif self.escape_route:
            # 离开头顶遮挡后重新观察通路。
            if self.recovery_side_key:
                movements = (
                    (["s"], 0.3),
                    ([side_key], 0.3),
                    (["s", side_key], 0.3),
                )
            else:
                movements = (
                    (["s"], 0.3),
                    (["s"], 0.3),
                    (["s"], 0.3),
                )
        else:
            movements = (
                (["s"], 0.4),
                ([side_key], 0.5),
                ([side_key], 0.5),
                (["w", side_key], 0.4),
                (["w", side_key], 0.4),
                (["w"], 0.3),
            )
        movement = movements[self.recovery_step]
        self.recovery_step += 1
        if self.recovery_step == len(movements):
            self.recovery_step = None
        return movement
