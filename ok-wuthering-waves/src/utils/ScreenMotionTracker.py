from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class ScreenTransform:
    scale: float
    dx: float
    dy: float
    support: int
    error: float

    def project(self, x, y):
        return x * self.scale + self.dx, y * self.scale + self.dy


class ScreenMotionTracker:
    def __init__(self, minimap=False, max_age=2.0):
        self.minimap = minimap
        self.max_age = max_age
        self.reset()

    def reset(self):
        self.reference_gray = None
        self.points = None
        self.reference_shape = None
        self.reference_time = None
        self.ratio = 1.0

    def _prepare(self, frame):
        h, w = frame.shape[:2]
        ratio = min(1.0, 480.0 / max(h, w))
        image = cv2.resize(frame, (round(w * ratio), round(h * ratio)), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        gray = cv2.GaussianBlur(gray, (3, 3), 0.7)
        mask = np.zeros(gray.shape, np.uint8)
        h, w = gray.shape
        if self.minimap:
            cv2.circle(mask, (w // 2, h // 2), round(min(w, h) * 0.44), 255, -1)
            cv2.circle(mask, (w // 2, h // 2), round(min(w, h) * 0.18), 0, -1)
        else:
            mask[12:-12, 12:-12] = 255
        if image.ndim == 3:
            high, low = image.max(axis=2), image.min(axis=2)
            dynamic = np.uint8((high > 210) | ((high > 110) & (high.astype(int) - low > 65))) * 255
            dynamic = cv2.dilate(dynamic, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
            mask[dynamic != 0] = 0
        return gray, mask, ratio

    def anchor(self, frame, timestamp):
        self.reset()
        if frame is None or frame.size == 0:
            return False
        gray, mask, ratio = self._prepare(frame)
        points = cv2.goodFeaturesToTrack(gray, 160, 0.005, max(4, min(gray.shape) / 30), mask=mask)
        if points is None or len(points) < 12:
            return False
        self.reference_gray = gray
        self.points = points
        self.reference_shape = frame.shape[:2]
        self.reference_time = timestamp
        self.ratio = ratio
        return True

    def track(self, frame, timestamp):
        if (self.reference_gray is None or frame is None or
                frame.shape[:2] != self.reference_shape or
                timestamp - self.reference_time > self.max_age):
            return None
        gray, mask, _ = self._prepare(frame)
        if np.array_equal(gray, self.reference_gray):
            return ScreenTransform(1.0, 0.0, 0.0, len(self.points), 0.0)
        current, status, _ = cv2.calcOpticalFlowPyrLK(
            self.reference_gray, gray, self.points, None, winSize=(21, 21), maxLevel=3,
            criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 25, 0.01))
        reverse, back_status, _ = cv2.calcOpticalFlowPyrLK(
            gray, self.reference_gray, current, None, winSize=(21, 21), maxLevel=3)
        original = self.points[:, 0]
        observed = current[:, 0]
        valid = ((status[:, 0] != 0) & (back_status[:, 0] != 0) &
                 (np.linalg.norm(reverse[:, 0] - original, axis=1) < 0.8))
        indexes = np.rint(observed).astype(int)
        valid &= ((indexes[:, 0] >= 0) & (indexes[:, 0] < gray.shape[1]) &
                  (indexes[:, 1] >= 0) & (indexes[:, 1] < gray.shape[0]))
        clipped = np.clip(indexes, (0, 0), (gray.shape[1] - 1, gray.shape[0] - 1))
        valid &= mask[clipped[:, 1], clipped[:, 0]] != 0
        if np.count_nonzero(valid) < 12:
            return None
        source, target = original[valid], observed[valid]
        matrix, inliers = cv2.estimateAffinePartial2D(
            source, target, method=cv2.RANSAC, ransacReprojThreshold=0.9,
            maxIters=500, confidence=0.99, refineIters=10)
        if matrix is None:
            return None
        selected = inliers[:, 0] != 0
        support = int(np.count_nonzero(selected))
        scale = float(np.hypot(matrix[0, 0], matrix[1, 0]))
        rotation = abs(float(np.arctan2(matrix[1, 0], matrix[0, 0])))
        if support < 12 or support / len(source) < 0.65 or rotation > 0.015 or not 0.65 < scale < 1.5:
            return None
        # 北向地图只接受统一比例与平移，多个区域共同提供地形证据。
        cells = np.unique((source[selected] / (np.array(gray.shape[::-1]) / 3)).astype(int), axis=0)
        if len(cells) < 3:
            return None
        translation = np.median(target[selected] - source[selected] * scale, axis=0)
        error = float(np.median(np.linalg.norm(source[selected] * scale + translation - target[selected], axis=1)))
        if error > 0.6:
            return None
        if abs(scale - 1) < 0.0003:
            scale = 1.0
        dx, dy = translation / self.ratio
        if scale == 1 and np.hypot(dx, dy) < 0.12:
            dx = dy = 0.0
        return ScreenTransform(scale, float(dx), float(dy), support, error / self.ratio)
