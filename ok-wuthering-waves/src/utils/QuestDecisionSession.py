import json
from collections import deque

from src.utils.QuestOcrPrivacy import sanitize_quest_text


class QuestDecisionSession:
    def __init__(self):
        self.goal = ""
        self.history = deque(maxlen=8)
        self.pending = None
        self.seen_states = set()
        self.recent_calls = deque()
        self.last_call = None

    def observe(self, goal: str, interaction: str, now: float) -> bool:
        goal = sanitize_quest_text(goal)
        changed = bool(self.goal and goal and goal != self.goal)
        if self.pending is not None:
            if changed:
                self.pending["outcome"] = "quest_changed"
                self.pending = None
            elif interaction != self.pending["interaction"]:
                self.pending["outcome"] = "interaction_changed"
                self.pending = None
            elif now - self.pending["time"] >= 2:
                self.pending["outcome"] = "no_observed_progress"
                self.pending = None
        if changed:
            self.seen_states.clear()
        if goal:
            self.goal = goal
        return changed

    def can_call(self, event: str, interaction: str, progress: str, now: float) -> bool:
        signature = json.dumps((self.goal, event, sanitize_quest_text(interaction), progress), ensure_ascii=False)
        if signature in self.seen_states or (self.last_call is not None and now - self.last_call < 2):
            return False
        while self.recent_calls and now - self.recent_calls[0] >= 60:
            self.recent_calls.popleft()
        if len(self.recent_calls) >= 12:
            raise RuntimeError("一分钟内任务决策次数达到限制，停止重复调用")
        self.seen_states.add(signature)
        self.recent_calls.append(now)
        self.last_call = now
        return True

    def failed_actions(self, interaction: str, location: str) -> set[str]:
        counts = {}
        for item in self.history:
            if item["goal"] == self.goal and item["interaction"] == interaction and item["location"] == location and item["outcome"] == "no_observed_progress":
                counts[item["action"]] = counts.get(item["action"], 0) + 1
        return {action for action, count in counts.items() if count >= 2}

    def record(self, action: str, interaction: str, location: str, now: float):
        if self.pending is not None:
            raise RuntimeError("前一次任务动作尚未完成结果检查")
        item = {
            "goal": self.goal,
            "interaction": sanitize_quest_text(interaction),
            "location": location,
            "action": action,
            "time": now,
            "outcome": "pending",
        }
        self.history.append(item)
        self.pending = item

    def mark_transition(self, outcome: str):
        if self.pending is not None:
            self.pending["outcome"] = outcome
            self.pending = None

    def context(self) -> list[dict]:
        return [{key: value for key, value in item.items() if key != "time"} for item in self.history]
