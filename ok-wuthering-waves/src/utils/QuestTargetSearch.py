from dataclasses import dataclass


@dataclass
class QuestTargetSearch:
    turn_count: int = 0

    def reset(self):
        self.turn_count = 0

    def next_turn(self) -> int:
        if self.turn_count >= 32:
            raise RuntimeError("旋转搜索后仍未识别到任务指引，已停止移动，请检查当前任务要求")
        self.turn_count += 1
        return 120
