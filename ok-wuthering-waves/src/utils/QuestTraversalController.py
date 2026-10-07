import math
from dataclasses import dataclass
from typing import Optional

from src.utils.QuestSceneState import QuestSceneState


@dataclass
class TraversalDecision:
    action: str
    revision: int
    distance: Optional[float]
    coordinate: Optional[tuple]


class QuestTraversalController:
    def __init__(self, scene: QuestSceneState):
        self.scene = scene
        self.decision = None
        self.coordinate = None
        self.last_coordinate_time = None
        self.stationary_since = None
        self.camera_error = None
        self.camera_failures = 0
        self.observation_attempts = 0
        self.jump_attempts = 0
        self.failed_actions = {}
        self.location_anchor = None
        self.map_progress = 0.0
        self.stationary_seconds = 0.0

    def invalidate(self):
        self.decision = None
        self.camera_error = None
        self.camera_failures = 0

    def reset_goal(self):
        self.invalidate()
        self.coordinate = None
        self.last_coordinate_time = None
        self.stationary_since = None
        self.jump_attempts = 0
        self.failed_actions.clear()
        self.observation_attempts = 0
        self.location_anchor = None
        self.map_progress = 0.0
        self.stationary_seconds = 0.0

    def remember(self, action: str):
        if action not in ("walk", "climb", "drop", "detour", "jump", "left", "right", "back"):
            raise ValueError("地形决策包含无法执行的动作")
        if self.failed_actions.get(action, 0) >= 2:
            raise RuntimeError("相同位置的地形操作连续两次没有进展，需要新的通路证据")
        self.decision = TraversalDecision(action, self.scene.revision, self.scene.distance, self.coordinate)
        self.observation_attempts = 0

    def current(self):
        if self.decision is None:
            return None
        if self.decision.revision != self.scene.revision:
            self.invalidate()
            return None
        if (self.coordinate is not None and self.decision.coordinate is not None
                and math.dist(self.coordinate, self.decision.coordinate) >= 8):
            self.invalidate()
            return None
        if (self.scene.distance is not None and self.decision.distance is not None
                and abs(self.scene.distance - self.decision.distance) >= 5):
            self.invalidate()
            return None
        return self.decision.action

    def record_coordinate(self, coordinate: Optional[tuple], now: float):
        if coordinate is None:
            self.scene.horizontal_progress = None
            self.scene.height_progress = None
            return
        if self.coordinate is not None:
            self.scene.horizontal_progress = math.dist(coordinate[:2], self.coordinate[:2])
            self.scene.height_progress = coordinate[2] - self.coordinate[2]
            moving = self.scene.horizontal_progress >= 1 or abs(self.scene.height_progress) >= 1
            if moving:
                self.record_progress(True, now)
        if self.location_anchor is None:
            self.location_anchor = coordinate
        elif math.dist(coordinate, self.location_anchor) >= 3:
            self.moved_to_new_location()
            self.location_anchor = coordinate
        self.coordinate = coordinate
        self.last_coordinate_time = now

    def record_map_progress(self, displacement: float):
        self.map_progress += max(0.0, displacement)
        if self.map_progress >= 3:
            self.moved_to_new_location()
            self.map_progress = 0.0

    def record_progress(self, moving: Optional[bool], now: float, duration: Optional[float] = None):
        if moving is True:
            self.stationary_since = None
            self.stationary_seconds = 0.0
        elif moving is False:
            if self.stationary_since is None:
                self.stationary_since = now
            self.stationary_seconds += duration or 0.0
            blocked = self.stationary_seconds >= 3 if duration is not None else now - self.stationary_since >= 3
            if blocked:
                action = self.current()
                if action:
                    self.failed_actions[action] = self.failed_actions.get(action, 0) + 1
                self.invalidate()
                self.stationary_seconds = 0.0
        else:
            self.stationary_since = None
            self.stationary_seconds = 0.0

    def observe_camera(self, error: float):
        error = abs(error)
        if self.camera_error is not None:
            self.camera_failures = self.camera_failures + 1 if error >= self.camera_error - 1 else 0
        self.camera_error = error
        if self.camera_failures >= 2:
            self.camera_error = None
            self.camera_failures = 0
            self.observation_attempts += 1
            if self.observation_attempts >= 3:
                raise RuntimeError("镜头调整持续没有改善目标位置，无法确认通行方向")
            return False
        return True

    def begin_jump(self):
        if self.jump_attempts >= 2:
            raise RuntimeError("同一位置已经尝试两次跳跃，需要其他通路证据")
        self.jump_attempts += 1

    def moved_to_new_location(self):
        self.jump_attempts = 0
        self.failed_actions.clear()
        self.observation_attempts = 0
