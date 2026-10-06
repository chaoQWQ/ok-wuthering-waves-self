import math
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np


@dataclass
class QuestAreaObservation:
    center_x: float
    center_y: float
    radius: float
    player_x: float
    player_y: float
    heading_deg: Optional[float]
    confidence: float

    @property
    def center_distance(self) -> float:
        return math.hypot(self.center_x - self.player_x, self.center_y - self.player_y)


class QuestAreaStuckError(RuntimeError):
    """区域搜索的当前路径点被持续阻挡，应跳过该点继续搜索"""


def minimap_box(frame: np.ndarray) -> tuple[int, int, int, int]:
    height, width = frame.shape[:2]
    return 0, 0, int(width * .16), int(height * .24)


def minimap_content_circle(roi: np.ndarray):
    height, width = roi.shape[:2]
    scale = min(height, width)
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, 1, scale * .4, param1=100,
                              param2=35, minRadius=int(scale * .30), maxRadius=int(scale * .50))
    if circles is None:
        return None
    expected = (width * .425, height * .48)
    candidates = [tuple(float(value) for value in circle) for circle in circles[0]
                  if math.hypot(circle[0] - expected[0], circle[1] - expected[1]) < scale * .15]
    return min(candidates, key=lambda circle: math.hypot(circle[0] - expected[0], circle[1] - expected[1])) if candidates else None


def detect_quest_area(frame: np.ndarray, search_box=None, expected_circle=None) -> Optional[QuestAreaObservation]:
    if frame is None or frame.size == 0:
        raise ValueError("黄色圈识别画面不能为空")
    mx, my, mw, mh = minimap_box(frame) if search_box is None else search_box
    roi = frame[my:my + mh, mx:mx + mw]
    map_circle = minimap_content_circle(roi)
    if map_circle is None:
        return None
    map_x, map_y, map_radius = map_circle
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    scale = min(mw, mh)

    # 玩家箭头的饱和度高于任务区域填充颜色。
    player_mask = cv2.inRange(hsv, (15, 120, 155), (38, 255, 255))
    kernel_size = max(3, round(scale * .025) | 1)
    player_mask = cv2.morphologyEx(player_mask, cv2.MORPH_CLOSE, np.ones((kernel_size, kernel_size), np.uint8))
    contours, _ = cv2.findContours(player_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    expected = (mw * .425, mh * .48)
    players = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if not scale * scale * .0008 <= area <= scale * scale * .025:
            continue
        moments = cv2.moments(contour)
        cx, cy = moments["m10"] / moments["m00"], moments["m01"] / moments["m00"]
        distance = math.hypot(cx - expected[0], cy - expected[1])
        if distance > scale * .18:
            continue
        hull = cv2.convexHull(contour)
        points = None
        for epsilon in (.06, .08, .10, .12):
            polygon = cv2.approxPolyDP(hull, cv2.arcLength(hull, True) * epsilon, True)[:, 0].astype(float)
            if len(polygon) == 3:
                points = polygon
                break
        heading = None
        if points is not None:
            angles = []
            for index, point in enumerate(points):
                before, after = points[index - 1] - point, points[(index + 1) % 3] - point
                angles.append(math.acos(float(np.clip(np.dot(before, after) / (np.linalg.norm(before) * np.linalg.norm(after)), -1, 1))))
            tip = points[int(np.argmin(angles))]
            heading = math.degrees(math.atan2(tip[0] - cx, cy - tip[1]))
        players.append((distance, cx, cy, heading, hull))
    if not players:
        return None
    _, px, py, heading, player_hull = min(players, key=lambda item: item[0])

    player_region = np.zeros((mh, mw), np.uint8)
    cv2.drawContours(player_region, [player_hull], -1, 255, -1)

    mask = cv2.inRange(hsv, (32, 35, 75), (46, 120, 205))
    mask[player_region > 0] = 0
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        area = cv2.contourArea(contour)
        x, y, w, h = cv2.boundingRect(contour)
        radius = (w + h) / 4
        if not scale * .035 <= radius <= scale * .28 or not .75 <= w / h <= 1.33:
            continue
        if area < .55 * math.pi * radius * radius:
            continue
        cx, cy = x + (w - 1) / 2, y + (h - 1) / 2
        if math.hypot(cx - map_x, cy - map_y) + radius > map_radius - 2:
            continue
        candidates.append((cx, cy, radius))
    circles = cv2.HoughCircles(mask, cv2.HOUGH_GRADIENT, 1, max(5, scale * .025),
                              param1=100, param2=8, minRadius=max(5, int(scale * .035)), maxRadius=int(scale * .28))
    if circles is not None:
        candidates.extend(tuple(float(value) for value in circle) for circle in circles[0])
    if expected_circle is not None:
        candidates.append(expected_circle)
    areas = []
    yy, xx = np.ogrid[:mh, :mw]
    angles = np.linspace(0, 2 * np.pi, 64, endpoint=False)
    for cx, cy, radius in candidates:
        if math.hypot(cx - map_x, cy - map_y) + radius > map_radius - 2:
            continue
        interior = ((xx - cx) ** 2 + (yy - cy) ** 2 <= (radius * .8) ** 2) & (player_region == 0)
        if np.count_nonzero(interior) < math.pi * radius * radius * .12:
            continue
        coverage = float(np.mean(mask[interior] > 0))
        if coverage < .80:
            continue
        squared_distance = (xx - cx) ** 2 + (yy - cy) ** 2
        exterior = (squared_distance >= (radius * 1.15) ** 2) & (squared_distance <= (radius * 1.45) ** 2) & (player_region == 0)
        if not np.any(exterior) or np.mean(mask[exterior] > 0) > .35:
            continue
        ring = np.zeros(64, dtype=bool)
        for delta in (-1.5, 0, 1.5):
            xs = np.clip(np.rint(cx + (radius + delta) * np.cos(angles)).astype(int), 0, mw - 1)
            ys = np.clip(np.rint(cy + (radius + delta) * np.sin(angles)).astype(int), 0, mh - 1)
            ring |= (hsv[ys, xs, 1] < 100) & (hsv[ys, xs, 2] > 155) & (player_region[ys, xs] == 0)
        ring_coverage = float(np.mean(ring))
        if ring_coverage >= .15:
            score = coverage * .6 + ring_coverage * .4
            if expected_circle is not None:
                ex, ey, er = expected_circle
                score -= min(.3, math.hypot(cx - ex, cy - ey) / er * .2 + abs(radius / er - 1) * .2)
            areas.append((score, cx, cy, radius))
    if not areas:
        return None
    confidence, cx, cy, radius = max(areas)
    return QuestAreaObservation(cx + mx, cy + my, radius, px + mx, py + my, heading, confidence)


def detect_minimap_transform(previous: np.ndarray, current: np.ndarray) -> np.ndarray:
    if previous.shape != current.shape:
        raise ValueError("小地图尺寸改变，区域搜索需要重新定位")
    height, width = current.shape[:2]
    gray_a, gray_b = cv2.cvtColor(previous, cv2.COLOR_BGR2GRAY), cv2.cvtColor(current, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(previous, cv2.COLOR_BGR2HSV)
    mask = np.zeros((height, width), np.uint8)
    cv2.circle(mask, (int(width * .425), int(height * .48)), int(min(height, width) * .37), 255, -1)
    mask[(hsv[:, :, 0] < 50) & (hsv[:, :, 1] > 35)] = 0
    points = cv2.goodFeaturesToTrack(gray_a, 150, .01, 4, mask=mask)
    if points is None or len(points) < 12:
        raise RuntimeError("小地图缺少可跟踪的道路特征，停止区域搜索")
    following, status, _ = cv2.calcOpticalFlowPyrLK(gray_a, gray_b, points, None, winSize=(21, 21), maxLevel=3)
    returning, reverse_status, _ = cv2.calcOpticalFlowPyrLK(gray_b, gray_a, following, None, winSize=(21, 21), maxLevel=3)
    valid = (status[:, 0] == 1) & (reverse_status[:, 0] == 1) & (np.linalg.norm(returning - points, axis=2)[:, 0] < 1)
    if np.count_nonzero(valid) < 10:
        raise RuntimeError("小地图道路跟踪中断，停止区域搜索")
    transform, inliers = cv2.estimateAffinePartial2D(points[valid], following[valid], method=cv2.RANSAC, ransacReprojThreshold=1.5)
    if transform is None or np.mean(inliers) < .65:
        raise RuntimeError("小地图道路变化无法确认，停止区域搜索")
    scale = math.hypot(transform[0, 0], transform[1, 0])
    if not .95 <= scale <= 1.05:
        raise RuntimeError("小地图缩放发生变化，停止区域搜索")
    return transform


def detect_minimap_rotation(previous: np.ndarray, current: np.ndarray) -> float:
    transform = detect_minimap_transform(previous, current)
    return math.atan2(transform[1, 0], transform[0, 0])


def minimap_visible(frame: np.ndarray) -> bool:
    """判断画面左上角是否存在圆形小地图（开放世界 HUD 特征）。

    主线剧情使用试用角色时队伍可能只有一人，右侧换人栏为空，队伍栏模板全部
    缺失；小地图在地图界面、传送加载与黑边过场中都会消失，可用来区分。
    """
    if frame is None or frame.size == 0:
        return False
    try:
        mx, my, mw, mh = minimap_box(frame)
        return minimap_content_circle(frame[my:my + mh, mx:mx + mw]) is not None
    except Exception:
        return False


class QuestAreaSearch:
    def __init__(self):
        self.phase = "center"
        self.index = 0
        self.rotation = 0.0
        self.completed_rings = 0
        self.waypoints = [(0.0, 0.0)]
        self.waypoint_rings = [0]
        theta = .5
        while theta <= 8 * math.pi:
            radius = .9 * theta / (8 * math.pi)
            self.waypoints.append((radius * math.cos(theta), radius * math.sin(theta)))
            self.waypoint_rings.append(int(theta / (2 * math.pi)))
            theta += min(.5, .18 / max(radius, .1))
        self.observation = None
        self.previous_map = None
        self.missing_frames = 0
        self.movement_attempts = 0
        self.best_waypoint_distance = math.inf
        self.arrived_at = None
        self.camera_heading = None
        self.pending_keys = None
        self.calibration_attempts = 0
        self.movement_keys = ["w"]
        self.map_reference = None
        self.pending_transform = None
        self.movement_speed = None
        self.pending_duration = None
        self.motion_keys = None
        self.motion_displacement = np.zeros(2)
        self.motion_duration = 0.0
        self.last_displacement = 0.0
        self.stationary_seconds = 0.0

    def find_observation(self, frame: np.ndarray) -> Optional[QuestAreaObservation]:
        mx, my, mw, mh = minimap_box(frame)
        minimap = frame[my:my + mh, mx:mx + mw]
        self.pending_transform = detect_minimap_transform(self.previous_map, minimap)
        center = self.pending_transform @ np.array([self.observation.center_x, self.observation.center_y, 1])
        expected_circle = (float(center[0]), float(center[1]), self.observation.radius)
        return detect_quest_area(frame, expected_circle=expected_circle)

    def observe(self, observation: QuestAreaObservation, minimap: np.ndarray):
        if self.previous_map is not None:
            transform = self.pending_transform if self.pending_transform is not None else detect_minimap_transform(self.previous_map, minimap)
            self.pending_transform = None
            expected_center = transform @ np.array([self.observation.center_x, self.observation.center_y, 1])
            if math.hypot(observation.center_x - expected_center[0], observation.center_y - expected_center[1]) > max(3, self.observation.radius * .4) or abs(observation.radius / self.observation.radius - 1) > .2:
                return False
            rotation = math.atan2(transform[1, 0], transform[0, 0])
            self.rotation += rotation
            if self.camera_heading is not None:
                self.camera_heading += math.degrees(rotation)
            if self.pending_keys is not None:
                expected_reference = transform @ self.map_reference
                displacement = self.map_reference[:2] - expected_reference
                self.last_displacement = float(np.linalg.norm(displacement))
                if self.motion_keys != self.pending_keys:
                    self.motion_keys = self.pending_keys
                    self.motion_displacement = np.zeros(2)
                    self.motion_duration = 0.0
                self.motion_displacement = transform[:, :2] @ self.motion_displacement + displacement
                self.motion_duration += self.pending_duration
                if self.last_displacement < .2:
                    self.stationary_seconds += self.pending_duration
                else:
                    self.stationary_seconds = 0.0
                if np.linalg.norm(self.motion_displacement) >= 1.2:
                    forward = int("w" in self.pending_keys) - int("s" in self.pending_keys)
                    right = int("d" in self.pending_keys) - int("a" in self.pending_keys)
                    key_bearing = math.degrees(math.atan2(right, forward))
                    motion_bearing = math.degrees(math.atan2(self.motion_displacement[0], -self.motion_displacement[1]))
                    observed_heading = motion_bearing - key_bearing
                    difference = 0 if self.camera_heading is None else (observed_heading - self.camera_heading + 180) % 360 - 180
                    if self.camera_heading is None or abs(difference) <= 35:
                        self.camera_heading = observed_heading if self.camera_heading is None else self.camera_heading + difference * .3
                        speed = float(np.linalg.norm(self.motion_displacement)) / self.motion_duration
                        self.movement_speed = speed if self.movement_speed is None else self.movement_speed * .7 + speed * .3
                    self.calibration_attempts = 0
                    self.motion_displacement = np.zeros(2)
                    self.motion_duration = 0.0
                self.pending_keys = None
                self.pending_duration = None
        else:
            self.map_reference = np.array([observation.player_x, observation.player_y, 1])
        self.observation = observation
        self.previous_map = minimap.copy()
        self.missing_frames = 0
        return True

    def lose_observation(self):
        self.missing_frames += 1
        if self.missing_frames >= 8:
            raise RuntimeError("连续无法确认黄色圈或玩家位置，停止区域搜索")

    def target(self) -> tuple[float, float, float]:
        if self.observation is None:
            raise RuntimeError("区域搜索尚未识别到黄色圈")
        if self.index >= len(self.waypoints):
            raise RuntimeError("黄色圈内搜索已经完成，任务仍未推进")
        area = self.observation
        x, y = self.waypoints[self.index]
        dx = (x * math.cos(self.rotation) - y * math.sin(self.rotation)) * area.radius
        dy = (x * math.sin(self.rotation) + y * math.cos(self.rotation)) * area.radius
        return area.center_x + dx, area.center_y + dy, max(1.5, area.radius * .10)

    def navigate(self, now: float) -> tuple[str, float, float]:
        if self.index >= len(self.waypoints):
            return "complete", 0.0, 0.0
        tx, ty, tolerance = self.target()
        area = self.observation
        distance = math.hypot(tx - area.player_x, ty - area.player_y)
        if distance <= tolerance:
            self.movement_attempts = 0
            self.best_waypoint_distance = math.inf
            if self.index == 0:
                if self.arrived_at is None:
                    self.arrived_at = now
                self.phase = "wait"
                return "center", 0.0, distance
            self.completed_rings = self.waypoint_rings[self.index]
            self.index += 1
            return "waypoint", 0.0, distance
        bearing = math.degrees(math.atan2(tx - area.player_x, area.player_y - ty))
        if self.camera_heading is None:
            self.calibration_attempts += 1
            if self.calibration_attempts > 8:
                raise RuntimeError("短步移动仍无法确认行进方向，停止区域搜索")
            probe_keys = ("w", "s", "a", "d")
            self.movement_keys = [probe_keys[(self.calibration_attempts - 1) // 2]]
            return "calibrate", 0.0, distance
        relative = (bearing - self.camera_heading + 180) % 360 - 180
        directions = (["w"], ["w", "d"], ["d"], ["s", "d"], ["s"], ["s", "a"], ["a"], ["w", "a"])
        self.movement_keys = list(directions[round(relative / 45) % 8])
        if distance < self.best_waypoint_distance - .5:
            self.best_waypoint_distance = distance
            self.movement_attempts = 0
        else:
            self.movement_attempts += 1
            if self.movement_attempts >= 28:
                raise QuestAreaStuckError("持续移动未能接近区域搜索点，跳过该搜索点")
        return "walk", relative, distance

    def record_movement(self, keys: list[str], duration: float):
        if self.pending_keys is not None:
            raise RuntimeError("前一次区域移动尚未取得新的小地图画面")
        self.pending_keys = tuple(keys)
        self.pending_duration = duration

    @property
    def blocked(self) -> bool:
        return self.camera_heading is not None and self.stationary_seconds >= 1.5

    def movement_duration(self, distance: float) -> float:
        if self.movement_speed is None:
            raise RuntimeError("区域移动速度尚未取得实际观察结果")
        return min(.20, max(.04, distance / self.movement_speed * .7))

    def start_search(self):
        if self.phase != "wait":
            raise RuntimeError("到达黄色圈中心之后才能开始区域搜索")
        self.phase = "search"
        self.index = 1
        self.arrived_at = None

    @property
    def search_fraction(self) -> float:
        return min(1.0, max(0, self.index - 1) / (len(self.waypoints) - 1))

    def context(self) -> dict:
        area = self.observation
        return {
            "phase": self.phase,
            "inside_area": area.center_distance <= area.radius,
            "center_distance_pixels": round(area.center_distance, 1),
            "radius_pixels": round(area.radius, 1),
            "search_fraction": round(self.search_fraction, 2),
            "waypoint_index": self.index,
            "camera_heading_observed": self.camera_heading is not None,
            "last_movement_pixels": round(self.last_displacement, 2),
            "stationary_movement_seconds": round(self.stationary_seconds, 2),
        }
