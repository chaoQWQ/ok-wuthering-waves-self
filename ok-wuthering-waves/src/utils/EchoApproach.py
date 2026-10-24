import time
from dataclasses import dataclass

import numpy as np
from supervision import Detections
from trackers import ByteTrackTracker


@dataclass(frozen=True)
class EchoApproachDecision:
    keys: tuple[str, ...]
    target_id: int | None
    target_box: tuple[float, ...] | None
    visible_count: int
    reason: str


class EchoApproach:
    def __init__(self, threshold=0.5):
        self.threshold = threshold
        self.tracker = ByteTrackTracker(
            track_activation_threshold=threshold,
            high_conf_det_threshold=threshold,
            minimum_consecutive_frames=1,
            lost_track_buffer=30,
            frame_rate=15,
        )
        self.target_id = None
        self.last_seen = None
        self.filtered_x = None
        self.lateral = None
        self.last_steer_change = float('-inf')

    def update(self, boxes, width, height, timestamp=None):
        now = time.monotonic() if timestamp is None else timestamp
        if width <= 0 or height <= 0:
            raise ValueError('画面尺寸必须大于零')
        detections = Detections(
            xyxy=np.asarray([(box.x, box.y, box.x + box.width, box.y + box.height)
                             for box in boxes], dtype=np.float32).reshape(-1, 4),
            confidence=np.asarray([box.confidence for box in boxes], dtype=np.float32),
            class_id=np.zeros(len(boxes), dtype=int),
        )
        tracked = self.tracker.update(detections, timestamp=now)
        matches = np.flatnonzero(tracked.tracker_id == self.target_id)
        if self.target_id is not None and not len(matches):
            # 短暂遮挡期间保持当前目标，暂停移动并等待重新识别。
            if now - self.last_seen < 0.75:
                return EchoApproachDecision((), self.target_id, None, len(boxes), 'occluded')
            self.target_id = None
            self.filtered_x = None
            self.lateral = None
        if self.target_id is None:
            candidates = np.flatnonzero(
                (tracked.tracker_id >= 0) & (tracked.confidence >= self.threshold))
            if not len(candidates):
                return EchoApproachDecision((), None, None, len(boxes), 'searching')
            index = min(candidates, key=lambda i: self._target_cost(tracked.xyxy[i], width, height))
            self.target_id = int(tracked.tracker_id[index])
            self.filtered_x = None
            self.lateral = None
            self.last_steer_change = float('-inf')
        else:
            index = int(matches[0])
        self.last_seen = now
        target_box = tuple(float(value) for value in tracked.xyxy[index])
        center_x = (target_box[0] + target_box[2]) / (2 * width)
        self.filtered_x = center_x if self.filtered_x is None else 0.6 * center_x + 0.4 * self.filtered_x
        error = self.filtered_x - 0.5
        lateral = self.lateral
        if ((lateral == 'a' and error >= -0.03)
                or (lateral == 'd' and error <= 0.03)):
            lateral = None
        elif error < -0.075:
            lateral = 'a'
        elif error > 0.075:
            lateral = 'd'
        if self.lateral is not None and lateral is not None and lateral != self.lateral:
            lateral = None
        if lateral != self.lateral:
            if lateral is None or now - self.last_steer_change >= 0.3:
                self.lateral = lateral
                self.last_steer_change = now
        keys = ('w', self.lateral) if self.lateral else ('w',)
        return EchoApproachDecision(keys, self.target_id, target_box, len(boxes), 'approaching')

    @staticmethod
    def _target_cost(box, width, height):
        center_x = (box[0] + box[2]) / (2 * width)
        anchor_y = (box[1] * 2 + box[3]) / (3 * height)
        return abs(center_x - 0.5) + 0.15 * abs(anchor_y - 0.65)
