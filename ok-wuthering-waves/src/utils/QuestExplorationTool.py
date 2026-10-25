import cv2
import numpy as np


def detect_exploration_tool(frame: np.ndarray, tool_box: tuple[int, int, int, int]) -> bool:
    if frame is None or frame.ndim != 3 or frame.shape[2] != 3 or frame.size == 0:
        raise ValueError("探索工具画面必须为有效的 BGR 图片")
    x, y, width, height = tool_box
    if width <= 0 or height <= 0 or x < 0 or y < 0:
        raise ValueError("探索工具识别区域无效")
    if x + width > frame.shape[1] or y + height > frame.shape[0]:
        raise ValueError("探索工具识别区域超出画面")

    scale = width / 49.0
    center_x, center_y = x + width / 2, y + height / 2
    margin = int(np.ceil(width * 1.2))
    left, top = max(0, int(center_x) - margin), max(0, int(center_y) - margin)
    right = min(frame.shape[1], int(center_x) + margin + 1)
    bottom = min(frame.shape[0], int(center_y) + 2)
    hsv = cv2.cvtColor(frame[top:bottom, left:right], cv2.COLOR_BGR2HSV)
    center_x, center_y = center_x - left, center_y - top
    angles = np.linspace(np.pi + .08, 2 * np.pi - .08, 80)
    cosines, sines = np.cos(angles), np.sin(angles)
    radii = np.arange(width * .72, width * 1.08, max(.5, scale))

    # 沿工具图标上半圆比较亮度，圆环两侧用于排除背景颜色。
    for radius in radii:
        ring = []
        for offset in (-1.5, 0, 1.5):
            xs = np.rint(center_x + cosines * (radius + offset * scale)).astype(int)
            ys = np.rint(center_y + sines * (radius + offset * scale)).astype(int)
            if np.any(xs < 0) or np.any(xs >= hsv.shape[1]) or np.any(ys < 0) or np.any(ys >= hsv.shape[0]):
                return False
            ring.append(hsv[ys, xs])
        ring = np.array(ring)
        brightest = np.argmax(ring[:, :, 2], axis=0)
        rim = ring[brightest, np.arange(len(angles))].astype(float)
        neighbors = []
        for offset in (-6, 6):
            xs = np.rint(center_x + cosines * (radius + offset * scale)).astype(int)
            ys = np.rint(center_y + sines * (radius + offset * scale)).astype(int)
            if np.any(xs < 0) or np.any(xs >= hsv.shape[1]) or np.any(ys < 0) or np.any(ys >= hsv.shape[0]):
                return False
            neighbors.append(hsv[ys, xs, 2].astype(float))
        contrast = rim[:, 2] - np.maximum(neighbors[0], neighbors[1])
        hue, saturation = ring[:, :, 0], ring[:, :, 1]
        gold = (hue >= 12) & (hue <= 42) & (saturation >= 45)
        blue = (hue >= 85) & (hue <= 135) & (saturation >= 45)
        colored = np.any(gold | blue, axis=0) & (rim[:, 2] >= 195)
        highlighted = colored & (contrast >= 20)
        if np.mean(highlighted) >= .58 and all(np.mean(part) >= .35 for part in np.array_split(highlighted, 4)):
            return True
    return False


class ExplorationToolTrigger:
    def __init__(self):
        self.armed = True
        self.last_used = None
        self.unready_since = None

    def observe(self, ready: bool, now: float) -> bool:
        if not ready:
            if self.unready_since is None:
                self.unready_since = now
            if now - self.unready_since >= .5:
                self.armed = True
            return False
        self.unready_since = None
        return self.armed and (self.last_used is None or now - self.last_used >= 2.0)

    def mark_used(self, now: float):
        self.last_used = now
        self.armed = False
