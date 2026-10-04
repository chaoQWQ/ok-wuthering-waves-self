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

    def observe(self, distance: Optional[float]):
        if distance is None or self.recovery_step is not None:
            return
        if self.best_distance is None:
            self.best_distance = distance
        elif distance <= self.best_distance - 0.5:
            self.best_distance = distance
            self.movement_seconds = 0.0
            self.recovery_count = 0

    def record_movement(self, keys: list[str], duration: float, moving: Optional[bool] = None):
        if self.recovery_step is None and "w" in keys:
            self.stationary_observed = moving is False
            if moving is False:
                self.movement_seconds += duration
            else:
                self.movement_seconds = 0.0
                if moving is True:
                    self.recovery_count = 0

    @property
    def blocked(self) -> bool:
        distance_known = self.best_distance is not None or not self.require_distance
        return distance_known and self.stationary_observed and self.movement_seconds >= 3.0 - 1e-6

    def begin_recovery(self):
        if self.recovery_step is not None:
            return
        if self.recovery_count >= 4:
            raise RuntimeError("任务距离持续没有缩减，四次绕行均未通过，请检查任务目标附近的道路")
        self.recovery_count += 1
        self.recovery_step = 0
        self.movement_seconds = 0.0

    def next_recovery_movement(self) -> tuple[list[str], float]:
        if self.recovery_step is None:
            raise RuntimeError("绕行尚未开始")
        side_key = "a" if self.recovery_count % 2 else "d"
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
