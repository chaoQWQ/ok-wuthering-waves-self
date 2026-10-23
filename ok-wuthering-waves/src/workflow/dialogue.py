import hashlib
import json
import re

import cv2
import numpy as np

from src.utils.QuestOcrPrivacy import prepare_quest_ocr_frame, sanitize_quest_text
from src.utils.QuestVision import resize_quest_text_frame
from src.workflow.input import WorkflowStopped


FORBIDDEN_TARGETS = re.compile(
    r'购买|充值|唤取|抽取|兑换|分解|丢弃|删除|账号|帐号|聊天|发送|'
    r'purchase|buy|exchange|discard|delete|account|chat|send', re.I)


def ensure_allowed_target(target):
    if FORBIDDEN_TARGETS.search(target):
        raise WorkflowStopped('当前按钮不属于允许的剧情或活动操作')


class RecordedDialogue:
    def __init__(self, store, workflow, observer):
        self.store, self.workflow, self.observer = store, workflow, observer
        self.observations = store.observations(workflow.recording_id)
        self.indexed = {item.frame_id: item for item in self.observations}
        self.events = store.events(workflow.recording_id)
        self._polygons = {}
        self._pending = None

    @staticmethod
    def signature(observation):
        return observation.scene, observation.objective, observation.interaction, tuple(sorted(observation.texts))

    def text_at(self, observation, cursor):
        if not observation.screenshot or not observation.image_sha256:
            raise WorkflowStopped('对白选择缺少原始截图证据')
        if observation.frame_id not in self._polygons:
            path = self.store.resolve(observation.screenshot)
            if hashlib.sha256(path.read_bytes()).hexdigest() != observation.image_sha256:
                raise WorkflowStopped('对白选择的原始截图已经变化')
            frame = cv2.imread(str(path))
            if frame is None:
                raise WorkflowStopped('对白选择的原始截图无法解码')
            height, width = frame.shape[:2]
            text_frame, scale = resize_quest_text_frame(prepare_quest_ocr_frame(frame))
            with self.observer.lock:
                recognized = self.observer.engine.ocr(text_frame, cls=False)[0]
            self._polygons[observation.frame_id] = [
                (sanitize_quest_text(text), np.asarray(points, dtype=np.float32) / np.array(
                    [scale * width, scale * height], dtype=np.float32))
                for points, (text, confidence) in recognized if confidence >= .8]
        candidates = [text for text, polygon in self._polygons[observation.frame_id]
                      if cv2.pointPolygonTest(polygon, tuple(cursor), False) >= 0]
        if len(candidates) != 1:
            return None
        target = candidates[0]
        recorded = [control for control in observation.controls if control.text == target and control.confidence >= .8]
        return target if len(recorded) == 1 else None

    def choice(self, step, observation):
        if observation.gap or observation.scene not in ('dialog', 'menu', 'cutscene'):
            return None
        before, after = self.indexed[step.evidence_before], self.indexed[step.evidence_after]
        interval = [item for item in self.observations if before.timestamp <= item.timestamp <= after.timestamp]
        if any(item.gap for item in interval):
            raise WorkflowStopped('对白选择的录制区间存在中断')
        signature = self.signature(observation)
        choices = set()
        for event in self.events:
            if event['kind'] != 'input' or not before.timestamp <= event['timestamp'] <= after.timestamp:
                continue
            payload = json.loads(event['payload'])
            if not payload.get('mouse', {}).get('button_flags', 0) & 1 or not payload.get('cursor_visible'):
                continue
            earlier = [item for item in interval if item.timestamp <= event['timestamp']]
            later = [item for item in interval if item.timestamp >= event['timestamp']]
            if not earlier or not later or later[0].timestamp - earlier[-1].timestamp > 2:
                continue
            recorded = earlier[-1]
            if (self.signature(recorded) != signature or not recorded.foreground
                    or not later[0].foreground or later[0].gap):
                continue
            pictures = [item for item in earlier if item.screenshot and self.signature(item) == signature
                        and event['timestamp'] - item.timestamp <= 2]
            if not pictures:
                continue
            cursor = payload.get('cursor')
            if cursor is None or len(cursor) != 2 or any(not 0 <= value <= 1 for value in cursor):
                raise WorkflowStopped('对白选择的光标位置证据不完整')
            target = self.text_at(pictures[-1], cursor)
            if target is not None:
                choices.add(target)
        if not choices:
            return None
        if len(choices) != 1:
            raise WorkflowStopped('相同对白画面记录了不同选项，无法确认当前分支')
        target = choices.pop()
        ensure_allowed_target(target)
        controls = [control for control in observation.controls if control.text == target and control.confidence >= .8]
        if len(controls) != 1:
            raise WorkflowStopped('当前对白选项无法唯一确定')
        return controls[0]

    def advance(self, task, step, observation):
        signature = step.step_id, self.signature(observation)
        if self._pending == signature:
            return True
        self._pending = None
        control = self.choice(step, observation)
        if control is None:
            return False
        task._coordinator.handoff('dialog')
        task.click(control.x, control.y)
        self._pending = signature
        return True
