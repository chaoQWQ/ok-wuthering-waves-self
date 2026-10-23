import time

import cv2

from src.utils.QuestVision import detect_climbing_state, detect_climbing_stamina, detect_quest_beacon
from src.utils.QuestNavigator import calculate_camera_turn
from src.workflow.input import WorkflowStopped


class RouteFollower:
    def __init__(self, task, store, workflow):
        self.task, self.store, self.workflow = task, store, workflow
        self.last_distance = None
        self.progress_at = time.monotonic()
        self.route_cache = {}
        self.orb = cv2.ORB_create(nfeatures=1000)
        self.matcher = cv2.BFMatcher(cv2.NORM_HAMMING)

    def reference_matches(self, step, frame):
        if not step.route_frames:
            return []
        gray = cv2.cvtColor(cv2.resize(frame, (640, 360)), cv2.COLOR_BGR2GRAY)
        gray[200:, 180:460] = 0
        _, current = self.orb.detectAndCompute(gray, None)
        if current is None:
            return []
        indexed = {item.frame_id: item for item in self.store.observations(self.workflow.recording_id)}
        matches = []
        for frame_id in step.route_frames:
            if frame_id not in self.route_cache:
                reference = cv2.imread(str(self.store.resolve(indexed[frame_id].screenshot)))
                if reference is None:
                    raise ValueError('道路参照截图无法读取')
                reference = cv2.cvtColor(cv2.resize(reference, (640, 360)), cv2.COLOR_BGR2GRAY)
                reference[200:, 180:460] = 0
                self.route_cache[frame_id] = self.orb.detectAndCompute(reference, None)[1]
            descriptors = self.route_cache[frame_id]
            if descriptors is not None and len(descriptors) >= 2:
                pairs = self.matcher.knnMatch(current, descriptors, k=2)
                score = sum(len(pair) == 2 and pair[0].distance < .7 * pair[1].distance for pair in pairs)
                if score >= 20:
                    matches.append(frame_id)
        return matches

    def advance(self, step, observation):
        task = self.task
        if observation.scene != 'world' or observation.combat:
            raise WorkflowStopped('当前场景不能执行剧情导航')
        frame = task.frame
        beacon = detect_quest_beacon(frame)
        if not beacon.found:
            references = self.reference_matches(step, frame)
            task.info_set('Route references', ', '.join(references))
            raise WorkflowStopped('当前道路没有可确认的任务信标，停止移动')
        distance = task._extract_quest_distance(frame, beacon)
        if distance is not None:
            if self.last_distance is None or distance < self.last_distance - .5:
                self.progress_at = time.monotonic()
            self.last_distance = distance
        if time.monotonic() - self.progress_at > 15:
            raise WorkflowStopped('导航持续没有取得可确认的距离进展')
        command = calculate_camera_turn(frame.shape[1], beacon.x + beacon.width // 2,
                                        camera_sensitivity=task.config.get('Camera Sensitivity', 1.0),
                                        tolerance_ratio=.03)
        if command.need_turn:
            task._coordinator.move_camera(command.delta_x_pixels)
            return
        climbing = detect_climbing_state(frame).is_climbing
        if climbing:
            stamina = detect_climbing_stamina(frame)
            if stamina is not None and stamina < .15:
                raise WorkflowStopped('攀爬体力不足，停止当前路线')
        task.guidance_text = observation.objective
        task._apply_movement(['w'], .15 if climbing or distance is None or distance < 10 else .3)
