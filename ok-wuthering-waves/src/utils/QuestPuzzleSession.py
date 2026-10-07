import re
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

from src.utils.QuestOcrPrivacy import is_named_quest_interaction, sanitize_quest_text


@dataclass
class PuzzleObservation:
    goal: str
    interaction: str
    texts: tuple
    position: Optional[tuple] = None
    phase: str = "world"
    visual_state: str = "unknown"
    appearance: Optional[np.ndarray] = field(default=None, repr=False)
    controls: tuple = ()
    state_texts: Optional[tuple] = None
    frame: Optional[np.ndarray] = field(default=None, repr=False)

    @property
    def state(self):
        patterns = r"\d+\s*[/／]\s*\d+|已开启|已激活|已启动|已完成|已拾取|已放置|正在携带|持有中|未激活|已关闭|已连接|已断开|朝向[东西南北]|activated|completed|carrying|picked up|placed"
        values = set()
        for text in self.texts if self.state_texts is None else self.state_texts:
            values.update(match.group(0).lower() for match in re.finditer(patterns, text, re.IGNORECASE))
        return tuple(sorted(values))


@dataclass
class PuzzleStep:
    object_id: str
    kind: str
    action: str
    key: Optional[str]
    preconditions: dict
    expected_effect: tuple
    timeout: float = 3.0
    duration: float = .1
    target: Optional[tuple] = None


class PuzzleHandler:
    def matches(self, observation: PuzzleObservation) -> bool:
        raise NotImplementedError

    def prepare(self, observation: PuzzleObservation, object_id: str) -> PuzzleStep:
        raise NotImplementedError

    def effect(self, before: PuzzleObservation, after: PuzzleObservation) -> Optional[str]:
        if after.goal and after.goal != before.goal:
            return "quest_changed"
        if after.phase in ("dialog", "cutscene", "combat", "panel") and after.phase != before.phase:
            return after.phase + "_started"
        if after.state and after.state != before.state:
            return "mechanism_state_changed"
        if after.visual_state == "changed":
            return "mechanism_visual_changed"
        return None


class InteractionHandler(PuzzleHandler):
    def matches(self, observation):
        return bool(observation.interaction)

    def prepare(self, observation, object_id):
        text = observation.interaction
        kind = "dialog" if is_named_quest_interaction(observation.goal, text) else "investigate"
        for category, pattern in (
            ("carry", r"拾取|搬运|拿起|携带|pick up|carry"),
            ("place", r"放置|放下|place|put down"),
            ("rotate", r"旋转|转动|rotate|turn"),
            ("switch", r"启动|开启|激活|开关|activate|switch|start|open"),
        ):
            if re.search(pattern, text, re.IGNORECASE):
                kind = category
                break
        return PuzzleStep(object_id, kind, "interact", "f",
                          {"interaction": text, "phase": observation.phase},
                          ("quest_changed", "dialog_started", "cutscene_started", "panel_started", "mechanism_state_changed", "mechanism_visual_changed"))


class ExplicitControlHandler(PuzzleHandler):
    CONTROL = re.compile(r"(?:按下|按住|点击|释放|按|Press|Hold|Tap|Release)\s*[\[（(]?\s*([eEtTqQfF])(?![A-Za-z0-9_])", re.IGNORECASE)

    def matches(self, observation):
        return any(self.CONTROL.search(text) for text in observation.texts)

    def prepare(self, observation, object_id):
        for text in observation.texts:
            match = self.CONTROL.search(text)
            if match:
                holding = bool(re.search(r"按住|hold", text, re.IGNORECASE))
                seconds = re.search(r"(\d+(?:\.\d+)?)\s*(?:秒|seconds?\b)", text, re.IGNORECASE)
                duration = float(seconds[1]) if holding and seconds else .6 if holding else .1
                if not 0 < duration <= 60:
                    raise ValueError("工具操作提示中的持续时间不在允许范围内")
                return PuzzleStep(object_id, "tool", "skill", match[1].lower(),
                                  {"instruction": text, "phase": observation.phase},
                                  ("mechanism_state_changed", "mechanism_visual_changed", "quest_changed"),
                                  duration=duration)
        raise ValueError("当前画面没有明确的工具操作按键")


class SequenceHandler(InteractionHandler):
    def __init__(self, session):
        self.session = session

    def matches(self, observation):
        return bool(observation.interaction and re.search(r"依次|顺序|in order|sequence", observation.goal, re.IGNORECASE))

    def prepare(self, observation, object_id):
        hint = re.search(r"(?:依次|顺序为|in order)\s*[:：]?\s*(?:激活|启动|开启|activate)?\s*((?:\d+\s*[、，,→]\s*)+\d+)",
                         " ".join((observation.goal,) + observation.texts), re.IGNORECASE)
        if hint is None:
            raise RuntimeError("顺序机关缺少可识别的操作顺序提示")
        order = re.findall(r"\d+", hint[1])
        completed = sum(item.get("kind") == "sequence" for item in self.session.completed_steps)
        if completed >= len(order):
            raise RuntimeError("可见顺序机关已经执行完成，需要确认后续任务条件")
        number = re.search(r"\d+", observation.interaction)
        if number is None or number[0] != order[completed]:
            raise RuntimeError(f"当前顺序机关需要操作编号 {order[completed]} 的对象")
        step = super().prepare(observation, object_id)
        step.kind = "sequence"
        step.preconditions["sequence_index"] = completed
        return step


class PanelHandler(PuzzleHandler):
    def matches(self, observation):
        return observation.phase == "panel" and bool(observation.controls)

    def prepare(self, observation, object_id):
        # 只使用任务提示中明确出现的按钮名称。
        for text, center in observation.controls:
            if not 1 <= len(text) <= 12 or re.search(r"按下|按住|点击|选择|click|select", text, re.IGNORECASE):
                continue
            if text in observation.goal or any(text in line and re.search(r"点击|选择|click|select", line, re.IGNORECASE) for line in observation.texts):
                return PuzzleStep(object_id, "panel", "panel_select", None,
                                  {"phase": "panel", "control": text},
                                  ("quest_changed", "mechanism_state_changed", "panel_changed"), target=center)
        raise RuntimeError("机关界面缺少明确的按钮选择提示")

    def effect(self, before, after):
        effect = super().effect(before, after)
        if effect:
            return effect
        if (set(text for text, _ in before.controls) != set(text for text, _ in after.controls)
                or (before.phase == "panel" and after.phase == "world")):
            return "panel_changed"
        return None


class AttackHandler(PuzzleHandler):
    def matches(self, observation):
        return bool(re.search(r"攻击|击碎|破坏|摧毁|attack|destroy|break", observation.goal, re.IGNORECASE))

    def prepare(self, observation, object_id):
        return PuzzleStep(object_id, "attack", "attack", "left",
                          {"goal": observation.goal, "phase": observation.phase},
                          ("mechanism_visual_changed", "mechanism_state_changed", "quest_changed"))


@dataclass
class PuzzleObject:
    object_id: str
    name: str
    position: Optional[tuple]
    appearance: Optional[np.ndarray]
    state: tuple = ()
    version: int = 0
    failures: dict = field(default_factory=dict)


class QuestPuzzleSession:
    def __init__(self):
        self.handlers = [SequenceHandler(self), PanelHandler(), InteractionHandler(), ExplicitControlHandler(), AttackHandler()]
        self.objects = {}
        self.pending = None
        self.completed_steps = []
        self.carrying = None
        self.environment_version = 0
        self.last_state = None

    def register(self, handler: PuzzleHandler):
        if not isinstance(handler, PuzzleHandler):
            raise TypeError("机关处理器必须继承 PuzzleHandler")
        self.handlers.insert(0, handler)

    def reset_goal(self):
        self.objects.clear()
        self.pending = None
        self.completed_steps.clear()
        self.carrying = None
        self.environment_version = 0
        self.last_state = None

    def identify(self, observation: PuzzleObservation):
        name = sanitize_quest_text(observation.interaction) or "quest_target"
        candidates = []
        unlocated = []
        for obj in self.objects.values():
            if obj.name != name:
                continue
            if observation.position is not None and obj.position is not None:
                if np.linalg.norm(np.array(observation.position) - np.array(obj.position)) <= 3:
                    candidates.append(obj)
            elif observation.appearance is not None and obj.appearance is not None:
                similarity = cv2.matchTemplate(observation.appearance, obj.appearance, cv2.TM_CCOEFF_NORMED)[0, 0]
                if similarity >= .92 or same_object_view(observation.appearance, obj.appearance):
                    candidates.append(obj)
                else:
                    unlocated.append(obj)
        if len(candidates) > 1:
            candidates = [obj for obj in candidates if observation.appearance is not None and obj.appearance is not None
                          and cv2.matchTemplate(observation.appearance, obj.appearance, cv2.TM_CCOEFF_NORMED)[0, 0] >= .92]
            if len(candidates) != 1:
                raise RuntimeError("多个同名机关的位置接近，当前画面无法确认具体对象")
        if candidates:
            obj = candidates[0]
        else:
            if unlocated:
                raise RuntimeError("镜头变化后无法重新确认机关身份，需要位置或对象外观证据")
            object_id = "object_" + str(len(self.objects) + 1)
            obj = PuzzleObject(object_id, name, observation.position, observation.appearance)
            self.objects[object_id] = obj
        if obj.state != observation.state:
            obj.state = observation.state
            obj.version += 1
        if obj.position is None and observation.position is not None:
            obj.position = observation.position
        self.observe_environment(observation.state)
        return obj

    def observe_environment(self, state):
        if self.last_state is not None and state != self.last_state:
            self.environment_version += 1
        self.last_state = state

    def prepare(self, observation: PuzzleObservation, action: str):
        obj = self.identify(observation)
        for handler in self.handlers:
            if handler.matches(observation):
                step = handler.prepare(observation, obj.object_id)
                if step.action == action:
                    key = (obj.version, self.environment_version, action)
                    if obj.failures.get(key, 0) >= 2:
                        raise RuntimeError("当前机关在相同状态下连续两次没有产生预期效果，需要新的操作条件")
                    return step, handler
                if isinstance(handler, SequenceHandler):
                    break
        raise RuntimeError("当前机关缺少可执行规则或明确的操作提示")

    def begin(self, step: PuzzleStep, handler: PuzzleHandler, observation: PuzzleObservation, now: float):
        if self.pending is not None:
            raise RuntimeError("前一次机关操作尚未完成验证")
        obj = self.objects[step.object_id]
        self.pending = {"step": step, "handler": handler, "before": observation, "time": now,
                        "failure_key": (obj.version, self.environment_version, step.action),
                        "animated": False}

    def verify(self, observation: PuzzleObservation, now: float, animation: bool = False):
        if self.pending is None:
            return None
        pending = self.pending
        step = pending["step"]
        effect = pending["handler"].effect(pending["before"], observation)
        if effect not in step.expected_effect:
            effect = None
        if effect == "mechanism_state_changed" and (not observation.state or not set(observation.state) - set(pending["before"].state)):
            effect = None
        if effect:
            self.completed_steps.append({"object_id": step.object_id, "kind": step.kind, "effect": effect})
            self.completed_steps = self.completed_steps[-32:]
            if step.kind == "carry" and effect in ("mechanism_state_changed", "mechanism_visual_changed", "quest_changed"):
                self.carrying = step.object_id
            elif step.kind == "place" and effect in ("mechanism_state_changed", "mechanism_visual_changed", "quest_changed"):
                self.carrying = None
            self.environment_version += 1
            self.pending = None
            return effect
        pending["animated"] |= animation
        timeout = max(step.timeout, 8.0) if pending["animated"] else step.timeout
        if now - pending["time"] >= timeout:
            obj = self.objects[step.object_id]
            key = pending["failure_key"]
            obj.failures[key] = obj.failures.get(key, 0) + 1
            self.pending = None
            return "no_observed_progress"
        return "pending"

    def interrupt(self, reason: str):
        if self.pending is not None:
            self.completed_steps.append({"object_id": self.pending["step"].object_id, "effect": reason})
            self.pending = None

    def context(self):
        return {"carrying": self.carrying, "environment_version": self.environment_version,
                "objects": [{"id": obj.object_id, "name": obj.name, "state": obj.state,
                             "version": obj.version} for obj in list(self.objects.values())[-12:]],
                "recent_steps": self.completed_steps[-8:],
                "pending": None if self.pending is None else self.pending["step"].kind}


def object_appearance(frame: np.ndarray, box: tuple):
    x, y, width, height = box
    crop = frame[max(0, y):y + height, max(0, x):x + width]
    if crop.size == 0:
        raise ValueError("机关观察区域不能为空")
    return cv2.resize(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), (256, 192))


def same_object_view(before, after):
    detector = cv2.ORB_create(nfeatures=250)
    before_points, before_descriptors = detector.detectAndCompute(before, None)
    after_points, after_descriptors = detector.detectAndCompute(after, None)
    if before_descriptors is None or after_descriptors is None:
        return False
    matches = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(before_descriptors, after_descriptors, k=2)
    reliable = [pair[0] for pair in matches if len(pair) == 2 and pair[0].distance < pair[1].distance * .7]
    if len(reliable) < 12:
        return False
    source = np.float32([before_points[item.queryIdx].pt for item in reliable])
    target = np.float32([after_points[item.trainIdx].pt for item in reliable])
    transform, inliers = cv2.estimateAffinePartial2D(source, target, method=cv2.RANSAC, ransacReprojThreshold=2)
    return transform is not None and inliers is not None and float(np.mean(inliers)) >= .7
