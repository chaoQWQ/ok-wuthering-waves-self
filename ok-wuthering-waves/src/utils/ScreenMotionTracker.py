from __future__ import annotations

import math
from typing import Optional, Tuple

import cv2
import numpy as np


class ScreenMotionTracker:
    def __init__(self, minimap: bool = False, max_age: float = 2.0):
        self.minimap = minimap
        self.max_age = max_age
        self.reference_gray: Optional[np.ndarray] = None
        self.reference_shape: Optional[Tuple[int, int]] = None
        self.reference_time: Optional[float] = None
        self.scale: float = 1.0

    def reset(self) -> None:
        self.reference_gray = None
        self.reference_shape = None
        self.reference_time = None
        self.scale = 1.0

    def anchor(self, frame: np.ndarray, timestamp: float) -> None:
        if frame is None or frame.size == 0:
            self.reset()
            return
        h, w = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if len(frame.shape) == 3 else frame.copy()
        if not self.minimap:
            y1, y2 = int(h * 0.2), int(h * 0.8)
            x1, x2 = int(w * 0.2), int(w * 0.8)
            crop = gray[y1:y2, x1:x2]
        else:
            crop = gray
        scale = min(1.0, 360.0 / max(crop.shape[:2]))
        if scale < 1.0:
            crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        self.scale = scale
        self.reference_gray = np.float32(crop)
        self.reference_shape = (h, w)
        self.reference_time = timestamp

    def track(self, frame: np.ndarray, timestamp: float) -> Optional[Tuple[float, float]]:
        if (
            self.reference_gray is None
            or frame is None
            or frame.size == 0
            or frame.shape[:2] != self.reference_shape
        ):
            return None
        if self.reference_time is not None and timestamp - self.reference_time > self.max_age:
            return None
        h, w = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if len(frame.shape) == 3 else frame
        if not self.minimap:
            y1, y2 = int(h * 0.2), int(h * 0.8)
            x1, x2 = int(w * 0.2), int(w * 0.8)
            crop = gray[y1:y2, x1:x2]
        else:
            crop = gray
        if self.scale < 1.0:
            crop = cv2.resize(crop, None, fx=self.scale, fy=self.scale, interpolation=cv2.INTER_AREA)
        curr_float = np.float32(crop)
        (shift_x, shift_y), response = cv2.phaseCorrelate(self.reference_gray, curr_float)
        if response < 0.12 or not math.isfinite(shift_x) or not math.isfinite(shift_y):
            return None
        dx = float(shift_x) / self.scale
        dy = float(shift_y) / self.scale
        return dx, dy
