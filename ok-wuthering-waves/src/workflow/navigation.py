import math
import time
from dataclasses import dataclass

import cv2
import numpy as np

from src.utils.QuestBackgroundMotion import detect_background_motion
from src.utils.QuestNavigator import QuestCameraAlignment, calculate_camera_turn
from src.utils.QuestVision import (
    detect_climbing_state, detect_climbing_stamina, detect_minimap_quest_arrow, detect_quest_beacon,
)
from src.workflow.input import WorkflowStopped


@dataclass(frozen=True)
class RouteReference:
    frame_id: str
    inliers: int
    reprojection_error: float
    target_x: float | None
    target_y: float | None
    center_shift_x: float
    center_shift_y: float
    area_ratio: float
    coverage: float


@dataclass(frozen=True)
class NavigationCommand:
    action: str
    horizontal: int = 0
    vertical: int = 0
    duration: float = 0
    reason: str = ''


class RouteFeatureMatcher:
    def __init__(self):
        self.orb = cv2.ORB_create(nfeatures=1600)
        self.matcher = cv2.BFMatcher(cv2.NORM_HAMMING)

    def describe(self, frame):
        gray = cv2.cvtColor(cv2.resize(frame, (640, 360)), cv2.COLOR_BGR2GRAY)
        mask = np.zeros_like(gray)
        mask[55:295, 115:560] = 255
        mask[125:, 245:420] = 0
        points, descriptors = self.orb.detectAndCompute(gray, mask)
        beacon = detect_quest_beacon(frame)
        target = None
        if beacon.found:
            target = ((beacon.x + beacon.width / 2) * 640 / frame.shape[1],
                      (beacon.y + beacon.height / 2) * 360 / frame.shape[0])
        return points, descriptors, target

    def align(self, frame_id, reference, current):
        before_points, before, target = reference
        after_points, after, _ = current
        if before is None or after is None or min(len(before), len(after)) < 24:
            return None
        forward = self.matcher.knnMatch(before, after, k=2)
        backward = self.matcher.knnMatch(after, before, k=2)
        reverse = {pair[0].queryIdx: pair[0].trainIdx for pair in backward
                   if len(pair) == 2 and pair[0].distance < .7 * pair[1].distance}
        accepted = [pair[0] for pair in forward if len(pair) == 2
                    and pair[0].distance < .7 * pair[1].distance
                    and reverse.get(pair[0].trainIdx) == pair[0].queryIdx]
        if len(accepted) < 24:
            return None
        source = np.float32([before_points[item.queryIdx].pt for item in accepted])
        destination = np.float32([after_points[item.trainIdx].pt for item in accepted])
        transform, mask = cv2.findHomography(source, destination, cv2.RANSAC, 2.5)
        if transform is None or mask is None or not np.isfinite(transform).all():
            return None
        valid = mask[:, 0] != 0
        count = int(np.count_nonzero(valid))
        if count < 24 or count / len(accepted) < .65:
            return None
        source, destination = source[valid], destination[valid]
        # 多处环境特征必须共同支持朝向，固定文字及局部小物体不能独立作为依据。
        if np.ptp(source[:, 0]) < 160 or np.ptp(source[:, 1]) < 65:
            return None
        projected = cv2.perspectiveTransform(source[None], transform)[0]
        error = float(np.median(np.linalg.norm(projected - destination, axis=1)))
        corners = np.float32([[[115, 55], [560, 55], [560, 295], [115, 295]]])
        polygon = cv2.perspectiveTransform(corners, transform)[0]
        if (error > 2 or not np.isfinite(polygon).all()
                or not cv2.isContourConvex(polygon)
                or not .55 <= cv2.contourArea(polygon) / (445 * 240) <= 1.8
                or np.max(np.abs(polygon - corners[0])) > 200):
            return None
        center = cv2.perspectiveTransform(np.float32([[[320, 180]]]), transform)[0, 0]
        coverage = float(cv2.contourArea(cv2.convexHull(source)) / (445 * 240))
        target_x = target_y = None
        if target is not None:
            point = cv2.perspectiveTransform(np.float32([[target]]), transform)[0, 0]
            if not np.isfinite(point).all() or not (-160 <= point[0] <= 800 and -90 <= point[1] <= 450):
                return None
            target_x, target_y = float(point[0] / 640), float(point[1] / 360)
        return RouteReference(frame_id, count, error, target_x, target_y,
                              float((center[0] - 320) / 640), float((center[1] - 180) / 360),
                              float(cv2.contourArea(polygon) / (445 * 240)), coverage)


def geometric_reference(reference, current):
    matcher = RouteFeatureMatcher()
    return matcher.align('', matcher.describe(reference), matcher.describe(current))


class NavigationController:
    SEARCH_OFFSETS = (160, 160, 160, -160, -160, -160, -160, -160, -160, 160, 160, 160)

    def __init__(self, sensitivity=1.0):
        if not math.isfinite(sensitivity) or sensitivity <= 0:
            raise ValueError('镜头旋转灵敏度必须为有限的正数')
        self.sensitivity = sensitivity
        self.alignment = QuestCameraAlignment()
        self.step_id = None
        self.reset()

    def reset(self):
        self.progress_at = None
        self.best_distance = None
        self.pending_frame = None
        self.pending_duration = 0
        self.stationary_duration = 0
        self.search_started = None
        self.search_attempts = 0
        self.search_direction = 1
        self.alignment.reset()

    def decide(self, frame, distance, references=(), *, now=None):
        now = time.monotonic() if now is None else now
        if self.progress_at is None:
            self.progress_at = now
        if self.pending_frame is not None:
            if self.pending_frame.shape != frame.shape:
                raise WorkflowStopped('导航画面尺寸变化，无法确认移动结果')
            motion = detect_background_motion(self.pending_frame, frame)
            self.pending_frame = None
            if motion.moving is False:
                self.stationary_duration += self.pending_duration
            elif motion.moving is True:
                self.stationary_duration = 0
            if self.stationary_duration >= 1.2:
                raise WorkflowStopped('连续前进后环境画面没有移动，当前路线存在阻挡，需要人工确认道路')
        if distance is not None:
            if not math.isfinite(distance) or distance < 0:
                raise ValueError('任务距离必须为有限的非负数')
            if self.best_distance is None or distance < self.best_distance - .5:
                self.progress_at = now
                self.best_distance = distance
        if now - self.progress_at > 15:
            raise WorkflowStopped('导航持续没有取得可确认的距离进展')
        beacon = detect_quest_beacon(frame)
        if not beacon.found:
            self.alignment.reset()
            return self._search(frame, references, now)
        self.search_attempts = 0
        self.search_started = None
        center_x = beacon.x + beacon.width / 2
        self.search_direction = 1 if center_x >= frame.shape[1] / 2 else -1
        turn = self.alignment.next_turn(frame.shape[1], center_x, self.sensitivity, .03)
        if turn.need_turn:
            return NavigationCommand('turn', horizontal=turn.delta_x_pixels, reason='任务信标朝向')
        if distance is not None and distance <= 2:
            return NavigationCommand('wait', reason='已接近信标，等待交互或任务状态变化')
        climbing = detect_climbing_state(frame).is_climbing
        if climbing:
            stamina = detect_climbing_stamina(frame)
            if stamina is None or stamina < .15:
                raise WorkflowStopped('攀爬体力无法确认或已经不足，停止当前路线')
        duration = .15 if climbing or distance is None or distance < 10 else .3
        self.pending_frame, self.pending_duration = frame.copy(), duration
        return NavigationCommand('move', duration=duration, reason='当前信标已位于画面中央')

    def _search(self, frame, references, now):
        if self.search_started is None:
            self.search_started = now
        if self.search_attempts >= len(self.SEARCH_OFFSETS) or now - self.search_started > 12:
            raise WorkflowStopped('原地搜索视角后仍无法确认任务信标，停止当前路线')
        attempt = self.search_attempts
        self.search_attempts += 1
        minimap = detect_minimap_quest_arrow(frame)
        if minimap.found:
            turn = calculate_camera_turn(frame.shape[1], minimap_bearing_deg=minimap.bearing_deg,
                                         camera_sensitivity=self.sensitivity)
            if turn.need_turn:
                return NavigationCommand('turn', horizontal=turn.delta_x_pixels, reason='小地图任务方向')
        reference_turn = self.reference_turn(frame.shape[1], references)
        if reference_turn is not None:
            return reference_turn
        return NavigationCommand('turn', horizontal=self.SEARCH_OFFSETS[attempt] * self.search_direction,
                                 reason='原地搜索任务信标')

    def reference_turn(self, width, references):
        targets = [item for item in references if item.target_x is not None]
        if targets:
            horizontal = [item.target_x for item in targets]
            vertical = [item.target_y for item in targets]
            # 多张参照截图给出的目标方向必须一致。
            if max(horizontal) - min(horizontal) <= .12 and max(vertical) - min(vertical) <= .15:
                target = max(targets, key=lambda item: item.inliers)
                turn = calculate_camera_turn(width, round(target.target_x * width),
                                             camera_sensitivity=self.sensitivity, tolerance_ratio=.03)
                if turn.need_turn:
                    return NavigationCommand('turn', horizontal=turn.delta_x_pixels,
                                             reason=f'道路参照截图 {target.frame_id}')
                if target.target_y < .15 or target.target_y > .8:
                    return NavigationCommand('turn', vertical=-80 if target.target_y < .15 else 80,
                                             reason=f'道路参照截图 {target.frame_id}')
        return None


class RouteFollower:
    def __init__(self, task, store, workflow):
        self.task, self.store, self.workflow = task, store, workflow
        self.route_cache = {}
        self.features = RouteFeatureMatcher()
        self.controller = NavigationController(task.config.get('Camera Sensitivity', 1.0))

    def reference_matches(self, step, frame):
        if not step.route_frames:
            return []
        current = self.features.describe(frame)
        indexed = {item.frame_id: item for item in self.store.observations(self.workflow.recording_id)}
        matches = []
        for frame_id in step.route_frames:
            if frame_id not in indexed:
                raise ValueError('道路参照截图不属于当前录制')
            if frame_id not in self.route_cache:
                reference = cv2.imread(str(self.store.resolve(indexed[frame_id].screenshot)))
                if reference is None:
                    raise ValueError('道路参照截图无法读取')
                self.route_cache[frame_id] = self.features.describe(reference)
            alignment = self.features.align(frame_id, self.route_cache[frame_id], current)
            if alignment is not None:
                matches.append(alignment)
        return matches

    def advance(self, step, observation):
        task = self.task
        if observation.gap or not observation.foreground or observation.scene != 'world' or observation.combat:
            raise WorkflowStopped('当前场景不能执行剧情导航')
        if self.controller.step_id != step.step_id:
            self.controller.reset()
            self.controller.step_id = step.step_id
        frame = task.frame
        beacon = detect_quest_beacon(frame)
        references = self.reference_matches(step, frame) if not beacon.found else []
        task.info_set('Route references', ', '.join(item.frame_id for item in references))
        distance = task._extract_quest_distance(frame, beacon) if beacon.found else None
        command = self.controller.decide(frame, distance, references)
        task.info_set('Route navigation', command.reason)
        if command.action == 'turn':
            task._coordinator.move_camera(command.horizontal, command.vertical)
        elif command.action == 'move':
            task.send_key_down('w')
            try:
                time.sleep(command.duration)
            finally:
                task.send_key_up('w')
