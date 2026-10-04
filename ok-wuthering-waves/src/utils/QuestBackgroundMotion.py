from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np


@dataclass
class BackgroundMotion:
    moving: Optional[bool]
    displacement_pixels: float = 0.0
    vertical_pixels: float = 0.0
    tracked_points: int = 0


def detect_background_motion(previous: np.ndarray, current: np.ndarray) -> BackgroundMotion:
    if previous is None or current is None or previous.size == 0 or current.size == 0:
        raise ValueError("移动判断画面不能为空")
    if previous.shape != current.shape:
        raise ValueError("移动判断画面尺寸必须一致")
    height, width = previous.shape[:2]
    size = (640, max(1, round(height * 640 / width)))
    before = cv2.cvtColor(cv2.resize(previous, size), cv2.COLOR_BGR2GRAY)
    after = cv2.cvtColor(cv2.resize(current, size), cv2.COLOR_BGR2GRAY)
    sh, sw = before.shape
    mask = np.zeros_like(before)
    mask[int(sh * 0.20):int(sh * 0.76), int(sw * 0.25):int(sw * 0.84)] = 255
    # 排除角色、头顶标记和固定界面。
    mask[int(sh * 0.30):, int(sw * 0.39):int(sw * 0.65)] = 0
    points = cv2.goodFeaturesToTrack(before, 240, 0.02, 6, mask=mask)
    if points is None or len(points) < 20:
        return BackgroundMotion(None)
    following, status, _ = cv2.calcOpticalFlowPyrLK(before, after, points, None, winSize=(21, 21), maxLevel=3)
    if following is None:
        return BackgroundMotion(None)
    returning, back_status, _ = cv2.calcOpticalFlowPyrLK(after, before, following, None, winSize=(21, 21), maxLevel=3)
    if returning is None:
        return BackgroundMotion(None)
    valid = (status[:, 0] == 1) & (back_status[:, 0] == 1)
    valid &= np.linalg.norm(returning[:, 0] - points[:, 0], axis=1) < 1.0
    count = int(np.count_nonzero(valid))
    if count < 20 or count < len(points) * 0.5:
        return BackgroundMotion(None, tracked_points=count)
    displacement = following[valid, 0] - points[valid, 0]
    magnitude = float(np.median(np.linalg.norm(displacement, axis=1)))
    vertical = float(np.median(displacement[:, 1]))
    return BackgroundMotion(magnitude >= 0.7, magnitude, vertical, count)
