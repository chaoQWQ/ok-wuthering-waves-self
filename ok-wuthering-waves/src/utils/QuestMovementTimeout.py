from dataclasses import dataclass
import math
from typing import Optional


@dataclass
class QuestMovementTimeout:
    goal: str = ""
    movement_seconds: float = 0.0
    distance: Optional[float] = None
    coordinate: Optional[tuple] = None
    started: bool = False

    def reset(self, goal: str = ""):
        self.goal = goal
        self.movement_seconds = 0.0
        self.distance = None
        self.coordinate = None
        self.started = False

    def begin_movement(self, goal: str, distance: Optional[float], coordinate: Optional[tuple]):
        if goal != self.goal:
            self.reset(goal)
        if not goal or (distance is None and coordinate is None):
            self.reset(goal)
            return
        if not self.started:
            self.distance = distance
            self.coordinate = coordinate
            self.started = True

    def record_movement(self, goal: str, duration: float):
        if not math.isfinite(duration) or duration < 0:
            raise ValueError("有效移动时间必须为有限的非负数")
        if goal != self.goal:
            self.reset(goal)
        elif self.started:
            self.movement_seconds += duration

    def observe(self, goal: str, distance: Optional[float], coordinate: Optional[tuple]) -> bool:
        if goal != self.goal:
            self.reset(goal)
            return False
        if not self.started:
            return False
        distance_known = distance is not None and self.distance is not None
        coordinate_known = coordinate is not None and self.coordinate is not None
        if not distance_known and not coordinate_known:
            self.reset(goal)
            return False
        if ((distance_known and abs(distance - self.distance) >= .5)
                or (coordinate_known and math.dist(coordinate, self.coordinate) >= .5)):
            self.reset(goal)
            return False
        if self.movement_seconds >= 6:
            self.reset(goal)
            return True
        return False
