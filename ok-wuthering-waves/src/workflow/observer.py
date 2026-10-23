import re
import threading
import uuid

import cv2
import numpy as np
from ok import Box
from ok.feature.FeatureSet import FeatureSet
from ok.util.color import find_color_rectangles

from src.combat.CombatCheck import enemy_health_color_red, boss_health_color
from src.utils.QuestAreaSearch import minimap_visible
from src.utils.QuestOcrPrivacy import prepare_quest_ocr_frame, quest_goal_from_lines, sanitize_quest_text
from src.utils.QuestVision import (
    detect_dialog_advance_indicator, detect_interact_action, detect_letterbox,
    detect_quest_beacon, resize_quest_text_frame,
)
from src.workflow.models import Control, Observation


def workflow_combat_visible(frame):
    height, width = frame.shape[:2]
    return bool(find_color_rectangles(frame, enemy_health_color_red, width * .025, height * .0042,
                                     max_height=height * .025)
                or find_color_rectangles(frame, boss_health_color, width * .025, height * .0055,
                                         max_height=height * .025,
                                         box=Box(width * .33, height * .027, width * .33, height * .066)))


class SceneObserver:
    def __init__(self, project_root, matching, ocr_params):
        from onnxocr.onnx_paddleocr import ONNXPaddleOcr
        self.engine = ONNXPaddleOcr(use_angle_cls=False, **ocr_params)
        self.features = FeatureSet(
            False, str(project_root / 'assets/coco_annotations.json'),
            matching['default_horizontal_variance'], matching['default_vertical_variance'],
            feature_processor=matching['feature_processor'],
            hcenter_features=matching['hcenter_features'], vcenter_features=matching['vcenter_features'])
        self.lock = threading.Lock()
        self.previous = None

    def observe(self, frame, timestamp, foreground=False):
        if frame is None or frame.size == 0:
            raise ValueError('无法观察空白游戏画面')
        with self.lock:
            return self._observe(frame, timestamp, foreground)

    def _observe(self, frame, timestamp, foreground):
        height, width = frame.shape[:2]
        protected = prepare_quest_ocr_frame(frame)
        text_frame, scale = resize_quest_text_frame(protected)
        boxes = []
        redactions = []
        for points, (name, confidence) in self.engine.ocr(text_frame, cls=False)[0]:
            xs, ys = zip(*points)
            if '@' in name or re.search(r'\d{7,}|特征码|特徵碼|\bUID\b', name, re.I):
                redactions.append([float(min(xs) / scale / width), float(min(ys) / scale / height),
                                   float(max(xs) / scale / width), float(max(ys) / scale / height)])
                continue
            text = sanitize_quest_text(name)
            if not text or confidence < .65:
                continue
            boxes.append(Box(int(min(xs) / scale), int(min(ys) / scale),
                             max(1, int((max(xs) - min(xs)) / scale)),
                             max(1, int((max(ys) - min(ys)) / scale)),
                             name=text, confidence=float(confidence)))
        texts = [box.name for box in boxes]
        goal_boxes = [box for box in boxes if box.x < width * .05 and height * .23 < box.y < height * .43]
        objective = quest_goal_from_lines([box.name for box in sorted(goal_boxes, key=lambda item: item.y)])
        interaction_box = Box(width * .55, height * .38, width * .37, height * .32)
        keys = self.features.find_one_feature(frame, 'pick_up_f_hcenter_vcenter', box=interaction_box, threshold=.8)
        key = max(keys, key=lambda item: item.confidence) if keys else None
        key_box = (key.x, key.y, key.width, key.height) if key else None
        sx, sy = int(width * .55), int(height * .38)
        roi = protected[sy:round(height * .70), sx:round(width * .92)]
        interaction_boxes = []
        for points, (name, confidence) in self.engine.ocr(roi, cls=False)[0]:
            text = sanitize_quest_text(name)
            if text and confidence >= .65:
                xs, ys = zip(*points)
                interaction_boxes.append(Box(sx + round(min(xs)), sy + round(min(ys)),
                                             max(1, round(max(xs) - min(xs))),
                                             max(1, round(max(ys) - min(ys))),
                                             name=text, confidence=float(confidence)))
        interaction = detect_interact_action(frame, ocr_boxes=interaction_boxes, key_box=key_box)
        combat = workflow_combat_visible(frame)
        all_text = '\n'.join(texts)
        login = bool(re.search(r'账号登录|帐号登录|验证码|密码登录|扫码登录|切换账号|点击连接|请输入.*(?:账号|手机号|邮箱)|Sign In|Log In|Click to Connect|Verification Code|Password', all_text, re.I))
        login = login or any(re.fullmatch(r'登录|登入|登錄|Sign In|Log In', text, re.I) for text in texts)
        result = bool(re.search(r'挑战成功|挑战失败|挑战完成|任务完成|Challenge (?:Complete|Failed|Success)', all_text, re.I))
        timer = bool(re.search(r'\b\d{1,2}[:：]\d{2}\b|剩余时间|倒计时|限时|计时|Time Remaining|Time Left', all_text, re.I))
        world = minimap_visible(frame) or detect_quest_beacon(frame).found
        if login:
            scene = 'login'
        elif result:
            scene = 'result'
        elif detect_dialog_advance_indicator(frame).found:
            scene = 'dialog'
        elif detect_letterbox(frame).is_letterbox and not world:
            scene = 'cutscene'
        elif combat:
            scene = 'combat'
        elif world:
            scene = 'world'
        elif float(np.mean(frame)) < 8:
            scene = 'loading'
        elif len(texts) >= 2:
            scene = 'menu'
        else:
            scene = 'unknown'
        thumbnail = cv2.resize(protected, (320, 180))
        changed = self.previous is None or float(np.mean(cv2.absdiff(thumbnail, self.previous))) > 1.5
        self.previous = thumbnail
        if login:
            return Observation(frame_id=uuid.uuid4().hex, timestamp=float(timestamp), scene='login',
                               foreground=bool(foreground), changed=changed)
        return Observation(
            frame_id=uuid.uuid4().hex, timestamp=float(timestamp), scene=scene,
            objective=objective, texts=texts,
            controls=[Control(text=box.name, x=float((box.x + box.width / 2) / width),
                              y=float((box.y + box.height / 2) / height), confidence=float(box.confidence))
                      for box in boxes],
            interaction=interaction.action_text if interaction.has_f else '',
            combat=combat, timer_visible=timer, foreground=bool(foreground), changed=changed, redactions=redactions)


def protect_observation_frame(frame, observation):
    protected = prepare_quest_ocr_frame(frame)
    height, width = frame.shape[:2]
    for left, top, right, bottom in observation.redactions:
        protected[max(0, int(top * height) - 8):min(height, int(bottom * height) + 8),
                  max(0, int(left * width) - 8):min(width, int(right * width) + 8)] = 0
    return protected


class IdleTracker:
    def __init__(self, threshold=30.0):
        self.threshold = threshold
        self.since = None
        self.signature = None

    def observe(self, observation, input_active=False):
        signature = (observation.scene, observation.objective, tuple(observation.texts), observation.interaction)
        eligible = (observation.scene == 'world' and not observation.combat
                    and not observation.timer_visible and not observation.gap
                    and not observation.changed and not input_active)
        if not eligible or signature != self.signature:
            self.since = observation.timestamp if eligible else None
        elif self.since is None:
            self.since = observation.timestamp
        self.signature = signature
        return bool(self.since is not None and observation.timestamp - self.since >= self.threshold)
