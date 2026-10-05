from dataclasses import dataclass
from typing import Optional


@dataclass
class QuestTargetSearch:
    turn_count: int = 0
    forward_distance: Optional[float] = None
    forward_probes: int = 0

    def reset(self):
        self.turn_count = 0
        self.forward_distance = None
        self.forward_probes = 0

    def remember_forward_target(self, distance: Optional[float]):
        self.forward_distance = distance
        self.forward_probes = 3 if distance is not None and distance > 2 else 0

    def next_forward_probe(self, distance: Optional[float]) -> float:
        if self.forward_probes == 0 or distance is None or distance <= 2:
            return 0.0
        if self.forward_distance is None or distance > self.forward_distance + .5:
            self.forward_probes = 0
            return 0.0
        self.forward_probes -= 1
        return .15

    def next_turn(self) -> int:
        self.forward_probes = 0
        if self.turn_count >= 32:
            raise RuntimeError("旋转搜索后仍未识别到任务指引，已停止移动，请检查当前任务要求")
        self.turn_count += 1
        return 120
