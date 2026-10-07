from dataclasses import dataclass, field
from typing import Optional

from src.utils.QuestOcrPrivacy import sanitize_quest_text


class QuestSceneChangedError(RuntimeError):
    pass


@dataclass
class QuestSceneState:
    goal: str = ""
    phase: str = "unknown"
    vertical: str = "unknown"
    distance: Optional[float] = None
    bearing: Optional[float] = None
    horizontal_progress: Optional[float] = None
    height_progress: Optional[float] = None
    stamina: Optional[float] = None
    object_id: Optional[str] = None
    object_state: dict = field(default_factory=dict)
    available_actions: list = field(default_factory=list)
    attempted_directions: list = field(default_factory=list)
    waiting_for: Optional[str] = None
    revision: int = 0
    movement_actions: int = 0
    input_interruptions: int = 0

    def set_goal(self, goal: str):
        goal = sanitize_quest_text(goal)
        if goal and goal != self.goal:
            self.goal = goal
            self.vertical = "unknown"
            self.distance = None
            self.bearing = None
            self.object_id = None
            self.object_state = {}
            self.available_actions.clear()
            self.attempted_directions.clear()
            self.waiting_for = None
            self.revision += 1

    def transition(self, phase: str):
        if phase != self.phase:
            self.phase = phase
            self.revision += 1

    def context(self):
        return {
            "phase": self.phase, "vertical": self.vertical,
            "distance_meters": self.distance, "bearing_degrees": self.bearing,
            "horizontal_progress": self.horizontal_progress,
            "height_progress": self.height_progress, "stamina": self.stamina,
            "object_id": self.object_id, "object_state": self.object_state,
            "available_actions": self.available_actions,
            "attempted_directions": self.attempted_directions[-8:],
            "waiting_for": self.waiting_for,
            "movement_actions": self.movement_actions,
            "input_interruptions": self.input_interruptions,
        }
