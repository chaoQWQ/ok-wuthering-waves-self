"""Offline alignment of recorded Wuthering Waves maps to local map assets.

This module only consumes video frames and the downloaded ``assets/stitched``
feature/database files.  It has no game process, capture or input dependency.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence

import cv2
import numpy as np

from src.match_engine import SiftGzEngine
from src.match_engine.common import CoordsRef
from src.match_engine.params import ParamSet, params_to_engine_kwargs


COLLECTION_TYPE_IDS = (
    "qzx_01", "qzx_02", "qzx_03", "qzx_04",
    "sx", "sx·lgn", "sx·qq", "cx_01", "cx_02", "cx_03",
    "Play_12", "xsd", "fls", "gjd", "ych", "ylfy", "YHYC",
)

COLLECTION_CATEGORY_TYPES = {
    "chest": ("qzx_01", "qzx_02", "qzx_03", "qzx_04"),
    "sound_box": ("sx", "sx·lgn", "sx·qq"),
    "scenic_point": ("gjd",),
    "butterfly": ("xsd",),
}


def _types_for_order(category: str, order: int,
                     type_sequence: Sequence[str | None] | None) -> tuple[str, ...]:
    """Return an exact video-timeline type when one is available."""
    if type_sequence is not None and 0 < order <= len(type_sequence):
        exact = type_sequence[order - 1]
        if exact:
            return (str(exact),)
    return tuple(COLLECTION_CATEGORY_TYPES.get(category, ()))

# Read from the numbered overlay in the supplied P5 video.  The chest subtype
# varies, but the cumulative counters show this exact category order.
P5_COLLECTION_SEQUENCE = (
    *("chest",) * 4,
    "sound_box",
    "scenic_point",
    *("chest",) * 2,
    "butterfly",
    *("chest",) * 5,
    *("butterfly",) * 2,
    *("chest",) * 6,
)

P5_ROUTE_MARKER_X_RATIOS = tuple(
    value / 1280.0 for value in
    (62, 113, 140, 180, 200, 233, 366, 423, 549, 595, 733,
     766, 833, 866, 933, 1000, 1040, 1066, 1106, 1179, 1216, 1263)
)


@dataclass(frozen=True)
class MapAnchor:
    time_seconds: float
    map_id: str
    success: bool
    match_count: int
    inlier_count: int
    confidence: float
    map_scale: float
    frame_center_game: Optional[tuple[float, float]]
    cursor_video: Optional[tuple[float, float]]
    cursor_map: Optional[tuple[float, float]]
    player_game: Optional[tuple[float, float]]
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class MinimapMotion:
    success: bool
    match_count: int
    inlier_count: int
    confidence: float
    background_dx: float
    background_dy: float
    player_dx_pixels: float
    player_dy_pixels: float
    rotation_degrees: float
    scale: float


def extract_recorded_minimap(frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Extract the circular P5 minimap and a feature mask.

    Ratios are used rather than hard-coded 720p pixels so the same source can
    be analysed after transcoding.  The centre player arrow and outer labels
    are masked because they move independently from the map background.
    """
    height, width = frame.shape[:2]
    # P5 uses an enlarged minimap with author-added cardinal labels.  Keep the
    # full circular terrain area; the older 215×196 crop discarded much of the
    # outer ring and left too few descriptors for map-cache matching.
    left = 0
    top = 0
    right = round(width * 0.195)
    bottom = round(height * 0.347)
    patch = frame[top:bottom, left:right].copy()
    ph, pw = patch.shape[:2]
    centre = (round(pw * 0.47), round(ph * 0.47))
    radius = round(min(ph, pw) * 0.45)
    mask = np.zeros((ph, pw), np.uint8)
    cv2.circle(mask, centre, radius, 255, -1)
    cv2.circle(mask, centre, round(radius * 0.24), 0, -1)
    # The Bilibili channel watermark occupies the upper-left part of the map.
    mask[:round(ph * 0.29), :round(pw * 0.70)] = 0
    return patch, mask


def estimate_minimap_motion(previous: np.ndarray,
                            current: np.ndarray) -> MinimapMotion:
    """Estimate map-background motion between two nearby gameplay frames."""
    tracker = RecordedMinimapTracker()
    tracker.update(previous)
    motion = tracker.update(current)
    return motion or _failed_motion()


def _failed_motion(match_count: int = 0, inlier_count: int = 0,
                   confidence: float = 0.0) -> MinimapMotion:
    return MinimapMotion(False, match_count, inlier_count,
                         round(confidence, 4), 0.0, 0.0,
                         0.0, 0.0, 0.0, 1.0)


class RecordedMinimapTracker:
    """Incremental minimap odometry with cached previous-frame features."""

    def __init__(self):
        self.sift = cv2.SIFT_create(
        nfeatures=350, nOctaveLayers=3, contrastThreshold=0.018,
        edgeThreshold=9, sigma=1.2)
        self.matcher = cv2.BFMatcher(cv2.NORM_L2)
        self.previous = None

    def reset(self) -> None:
        self.previous = None

    def _features(self, frame: np.ndarray):
        patch, mask = extract_recorded_minimap(frame)
        gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
        keypoints, descriptors = self.sift.detectAndCompute(gray, mask)
        return patch.shape[:2], keypoints, descriptors

    def update(self, frame: np.ndarray) -> Optional[MinimapMotion]:
        current = self._features(frame)
        if self.previous is None:
            self.previous = current
            return None
        before_shape, before_kps, before_desc = self.previous
        _, after_kps, after_desc = current
        self.previous = current
        if before_desc is None or after_desc is None:
            return _failed_motion()
        matches = self.matcher.knnMatch(before_desc, after_desc, k=2)
        good = [pair[0] for pair in matches if len(pair) == 2 and
                pair[0].distance < 0.72 * pair[1].distance]
        if len(good) < 4:
            return _failed_motion(len(good))
        source = np.float32([before_kps[item.queryIdx].pt for item in good])
        target = np.float32([after_kps[item.trainIdx].pt for item in good])
        affine, inlier_mask = cv2.estimateAffinePartial2D(
            source, target, method=cv2.RANSAC, ransacReprojThreshold=2.5,
            maxIters=1500, confidence=0.995)
        inliers = int(inlier_mask.sum()) if inlier_mask is not None else 0
        confidence = inliers / max(len(good), 1)
        if affine is None or inliers < 4 or confidence < 0.55:
            return _failed_motion(len(good), inliers, confidence)
        ph, pw = before_shape
        centre = np.asarray([pw * 0.51, ph * 0.50, 1.0])
        mapped_centre = affine @ centre
        background = mapped_centre - centre[:2]
        a, b = float(affine[0, 0]), float(affine[1, 0])
        scale = math.hypot(a, b)
        rotation = math.degrees(math.atan2(b, a))
        return MinimapMotion(
            True, len(good), inliers, round(confidence, 4),
            round(float(background[0]), 4), round(float(background[1]), 4),
            round(float(-background[0]), 4), round(float(-background[1]), 4),
            round(rotation, 4), round(scale, 6),
        )


def p5_route_marker_scores(frame: np.ndarray) -> list[float]:
    """Measure the white active-marker glow under each numbered P5 item."""
    height, width = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    cy = round(height * (710 / 720))
    half_w = max(4, round(width * (11 / 1280)))
    half_h = max(3, round(height * (9 / 720)))
    top, bottom = max(0, cy - half_h), min(height, cy + half_h + 1)
    scores = []
    for ratio in P5_ROUTE_MARKER_X_RATIOS:
        cx = round(width * ratio)
        left, right = max(0, cx - half_w), min(width, cx + half_w + 1)
        roi = hsv[top:bottom, left:right]
        white = (roi[..., 2] > 200) & (roi[..., 1] < 75)
        scores.append(round(float(np.mean(white)), 6))
    return scores


def p5_pointer_template(frame: np.ndarray) -> np.ndarray:
    """Extract an edge template around the active pointer at marker one."""
    height, width = frame.shape[:2]
    cx = round(width * P5_ROUTE_MARKER_X_RATIOS[0])
    cy = round(height * (710 / 720))
    half_w = max(8, round(width * (13 / 1280)))
    half_h = max(7, round(height * (12 / 720)))
    crop = frame[max(0, cy - half_h):min(height, cy + half_h),
                 max(0, cx - half_w):min(width, cx + half_w)]
    return cv2.Canny(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), 40, 120)


def locate_p5_progress_pointer(frame: np.ndarray,
                               template: np.ndarray) -> tuple[float, float]:
    """Return the horizontal progress-pointer position and match score."""
    height, width = frame.shape[:2]
    top = round(height * (690 / 720))
    strip = cv2.Canny(
        cv2.cvtColor(frame[top:height], cv2.COLOR_BGR2GRAY), 40, 120)
    if (strip.shape[0] < template.shape[0] or
            strip.shape[1] < template.shape[1]):
        return 0.0, 0.0
    result = cv2.matchTemplate(strip, template, cv2.TM_CCOEFF_NORMED)
    _, maximum, _, location = cv2.minMaxLoc(result)
    centre_x = location[0] + template.shape[1] / 2.0
    return round(float(centre_x), 3), round(float(maximum), 4)


def decode_linear_progress(times: Sequence[float],
                           pointer_x: Sequence[float],
                           pointer_scores: Sequence[float],
                           frame_width: int,
                           minimum_score: float = 0.24) -> dict:
    """Fit the editor's linear timeline pointer and decode marker timestamps."""
    observations = [
        (float(t), float(x), float(score))
        for t, x, score in zip(times, pointer_x, pointer_scores)
        if float(score) >= minimum_score
    ]
    if len(observations) < 20:
        return {"status": "insufficient_pointer_samples", "transitions": []}
    time_values = np.asarray([item[0] for item in observations])
    x_values = np.asarray([item[1] for item in observations])
    # A normal least-squares seed is easily captured by recurring HUD shapes.
    # Try widely separated pairs and retain the line supported by the most
    # observations, then refine it with least squares.
    candidate_indices = np.arange(0, len(observations),
                                  max(1, len(observations) // 70))
    best_inliers = np.zeros(len(observations), dtype=bool)
    best_error = float("inf")
    for left_pos, left in enumerate(candidate_indices):
        for right in candidate_indices[left_pos + 1:]:
            delta_t = time_values[right] - time_values[left]
            if delta_t < 90:
                continue
            candidate_slope = (x_values[right] - x_values[left]) / delta_t
            if not 0.5 < candidate_slope < 5.0:
                continue
            candidate_intercept = x_values[left] - candidate_slope * time_values[left]
            errors = np.abs(
                x_values - (candidate_slope * time_values + candidate_intercept))
            candidate_inliers = errors <= 14.0
            count = int(np.count_nonzero(candidate_inliers))
            error = float(np.median(errors[candidate_inliers])) if count else float("inf")
            best_count = int(np.count_nonzero(best_inliers))
            if count > best_count or (count == best_count and error < best_error):
                best_inliers = candidate_inliers
                best_error = error
    if int(np.count_nonzero(best_inliers)) < 12:
        return {"status": "linear_fit_unreliable", "transitions": []}
    slope, intercept = np.polyfit(
        time_values[best_inliers], x_values[best_inliers], 1)
    residuals = np.abs(x_values - (slope * time_values + intercept))
    inliers = residuals <= 12.0
    if int(np.count_nonzero(inliers)) >= 12:
        slope, intercept = np.polyfit(time_values[inliers], x_values[inliers], 1)
    fitted = slope * time_values[inliers] + intercept
    actual = x_values[inliers]
    total_variance = float(np.sum((actual - np.mean(actual)) ** 2))
    residual_variance = float(np.sum((actual - fitted) ** 2))
    r_squared = (1.0 - residual_variance / total_variance
                 if total_variance > 0 else 0.0)
    marker_x = np.asarray(P5_ROUTE_MARKER_X_RATIOS) * float(frame_width)
    transitions = []
    if abs(slope) > 1e-6:
        for order, (x, category) in enumerate(
                zip(marker_x, P5_COLLECTION_SEQUENCE), 1):
            transitions.append({
                "order": order,
                "category": category,
                "time_seconds": round(float((x - intercept) / slope), 3),
                "marker_x": round(float(x), 2),
            })
    inlier_ratio = float(np.count_nonzero(inliers)) / len(observations)
    accepted = (0.5 < slope < 5.0 and r_squared >= 0.96 and
                inlier_ratio >= 0.45 and len(transitions) == 22)
    return {
        "status": "decoded" if accepted else "linear_fit_unreliable",
        "method": "linear_video_pointer",
        "slope_pixels_per_second": round(float(slope), 6),
        "intercept_pixels": round(float(intercept), 3),
        "r_squared": round(r_squared, 6),
        "observations": len(observations),
        "inliers": int(np.count_nonzero(inliers)),
        "inlier_ratio": round(inlier_ratio, 4),
        "transitions": transitions,
    }


def decode_monotonic_progress(times: Sequence[float],
                              scores: Sequence[Sequence[float]]) -> dict:
    """Decode a start-to-finish numbered overlay with a monotonic HMM."""
    values = np.asarray(scores, dtype=np.float64)
    if values.ndim != 2 or not len(times) or values.shape[0] != len(times):
        return {"status": "invalid", "transitions": []}
    state_count = values.shape[1]
    if state_count == 0 or values.shape[0] < state_count:
        return {"status": "insufficient_samples", "transitions": []}
    low = np.percentile(values, 30, axis=0)
    high = np.percentile(values, 96, axis=0)
    signal = np.clip((values - low) / np.maximum(high - low, 0.06), 0, 1)
    frame_count = values.shape[0]
    negative = -1e18
    dp = np.full((frame_count, state_count), negative, np.float64)
    previous = np.full((frame_count, state_count), -1, np.int16)
    dp[0, 0] = signal[0, 0] * 2.0
    for frame_index in range(1, frame_count):
        # A weak chronology prior prevents permanently bright HUD regions from
        # swallowing several states, while visual evidence remains dominant.
        expected = (frame_index / max(frame_count - 1, 1)) * (state_count - 1)
        for state in range(state_count):
            emission = signal[frame_index, state] * 2.0
            emission -= abs(state - expected) * 0.08
            stay = dp[frame_index - 1, state]
            advance = (dp[frame_index - 1, state - 1]
                       if state > 0 else negative)
            if advance > stay:
                dp[frame_index, state] = advance + emission
                previous[frame_index, state] = state - 1
            else:
                dp[frame_index, state] = stay + emission
                previous[frame_index, state] = state
    state = state_count - 1
    path = [state]
    for frame_index in range(frame_count - 1, 0, -1):
        state = int(previous[frame_index, state])
        if state < 0:
            return {"status": "decode_failed", "transitions": []}
        path.append(state)
    path.reverse()
    transitions = []
    last = -1
    for frame_index, state in enumerate(path):
        if state != last:
            transitions.append({
                "order": state + 1,
                "time_seconds": round(float(times[frame_index]), 3),
                "marker_score": round(float(values[frame_index, state]), 4),
                "normalised_score": round(float(signal[frame_index, state]), 4),
            })
            last = state
    mean_score = (sum(item["normalised_score"] for item in transitions) /
                  max(len(transitions), 1))
    complete = len(transitions) == state_count
    status = ("decoded" if complete and mean_score >= 0.45 else
              "decoded_low_confidence" if complete else "partial")
    return {
        "status": status,
        "state_count": state_count,
        "mean_normalised_score": round(mean_score, 4),
        "transitions": transitions,
    }


def detect_map_cursor(frame: np.ndarray) -> Optional[tuple[float, float]]:
    """Locate the yellow player cursor near the centre of a world-map frame."""
    height, width = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (17, 100, 170), (40, 255, 255))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                            np.ones((2, 2), np.uint8))
    count, _, stats, centroids = cv2.connectedComponentsWithStats(mask)
    centre = np.asarray((width / 2.0, height / 2.0))
    maximum_dx = width * 0.30
    maximum_dy = height * 0.32
    candidates = []
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        x, y = centroids[label]
        if not 24 <= area <= 2200:
            continue
        if abs(x - centre[0]) > maximum_dx or abs(y - centre[1]) > maximum_dy:
            continue
        distance = float(np.linalg.norm(centroids[label] - centre))
        candidates.append((distance - min(area, 500) * 0.025, x, y))
    if not candidates:
        return None
    _, x, y = min(candidates)
    return round(float(x), 3), round(float(y), 3)


def project_point(homography: np.ndarray, point: Sequence[float],
                  coords: CoordsRef) -> tuple[tuple[float, float],
                                              tuple[float, float]]:
    source = np.asarray([[[float(point[0]), float(point[1])]]],
                        dtype=np.float64)
    mapped = cv2.perspectiveTransform(source, homography)[0, 0]
    map_point = float(mapped[0]), float(mapped[1])
    game_point = coords.pixel_to_game(*map_point)
    return map_point, (float(game_point[0]), float(game_point[1]))


class MapFeatureLocator:
    """Match recorded world-map frames against one downloaded map cache."""

    def __init__(self, assets_dir: str | Path, map_id: str | int):
        self.assets_dir = Path(assets_dir)
        self.map_id = str(map_id)
        self.coords = self._load_coords()
        self.engine = self._load_engine()
        self.engine.coords = self.coords

    def _load_coords(self) -> CoordsRef:
        path = self.assets_dir / "map_coords.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        entry = payload[self.map_id]
        return CoordsRef(
            offset=(float(entry["offset"][0]), float(entry["offset"][1])),
            scale=(float(entry["scale"][0]), float(entry["scale"][1])),
            min_xy=(float(entry.get("min", [0, 0])[0]),
                    float(entry.get("min", [0, 0])[1])),
            max_xy=(float(entry.get("max", [0, 0])[0]),
                    float(entry.get("max", [0, 0])[1])),
        )

    def _load_engine(self) -> SiftGzEngine:
        settings_path = self.assets_dir / "setting.json"
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        config = settings.get("siftgz", {})
        name = config.get("maps", {}).get(
            self.map_id, config.get("default"))
        if not name:
            raise ValueError("地图资源缺少 SIFTGZ 参数")
        kwargs = params_to_engine_kwargs(ParamSet.from_name(name))
        # Matching thresholds are runtime policy and need not mirror the cache
        # extraction name.  The conservative engine default ratio is retained.
        kwargs.pop("ratio", None)
        kwargs.pop("max_dist", None)
        feature_path = self.assets_dir / f"{self.map_id}_siftgz.npz"
        if not feature_path.is_file():
            raise FileNotFoundError(f"地图特征不存在：{feature_path}")
        return SiftGzEngine(
            map_id=self.map_id,
            map_path=str(self.assets_dir / f"{self.map_id}.png"),
            assets_dir=str(self.assets_dir),
            **kwargs,
        )

    def locate(self, frame: np.ndarray, time_seconds: float) -> MapAnchor:
        result = self.engine.match_array(frame, crop_size=0)
        accepted = (
            result.success and result.H is not None and
            result.match_count >= 10 and result.inlier_count >= 8 and
            result.confidence >= 0.70 and 0.20 <= result.map_scale <= 5.0
        )
        if not accepted:
            return MapAnchor(
                round(float(time_seconds), 3), self.map_id, False,
                result.match_count, result.inlier_count,
                round(result.confidence, 4), round(result.map_scale, 4),
                result.game_center, None, None, None,
                "特征匹配未通过置信度/内点/尺度校验",
            )
        cursor = detect_map_cursor(frame)
        if cursor is None:
            return MapAnchor(
                round(float(time_seconds), 3), self.map_id, False,
                result.match_count, result.inlier_count,
                round(result.confidence, 4), round(result.map_scale, 4),
                result.game_center, None, None, None,
                "地图匹配成功，但未找到黄色玩家游标",
            )
        map_point, game_point = project_point(result.H, cursor, self.coords)
        if not self.coords.contains(*game_point):
            return MapAnchor(
                round(float(time_seconds), 3), self.map_id, False,
                result.match_count, result.inlier_count,
                round(result.confidence, 4), round(result.map_scale, 4),
                result.game_center, cursor, map_point, game_point,
                "玩家游标投影超出地图坐标范围",
            )
        return MapAnchor(
            round(float(time_seconds), 3), self.map_id, True,
            result.match_count, result.inlier_count,
            round(result.confidence, 4), round(result.map_scale, 4),
            result.game_center, cursor, map_point, game_point,
        )

    def nearby_collections(self, game_position: Sequence[float],
                           radius: float = 5000.0,
                           limit: int = 20) -> list[dict]:
        database = self.assets_dir / "map_items.db"
        if not database.is_file():
            return []
        x, y = float(game_position[0]), float(game_position[1])
        placeholders = ",".join("?" for _ in COLLECTION_TYPE_IDS)
        query = f"""
            SELECT l.id, l.item_id, i.name, l.type_id, l.x, l.y,
                   l.description
            FROM location l
            JOIN item i ON i.id = l.item_id
            WHERE l.state_id = ?
              AND l.type_id IN ({placeholders})
              AND l.x BETWEEN ? AND ?
              AND l.y BETWEEN ? AND ?
        """
        params = [int(self.map_id), *COLLECTION_TYPE_IDS,
                  x - radius, x + radius, y - radius, y + radius]
        with sqlite3.connect(str(database)) as connection:
            rows = connection.execute(query, params).fetchall()
        output = []
        for location_id, item_id, name, type_id, px, py, description in rows:
            distance = math.hypot(float(px) - x, float(py) - y)
            if distance <= radius:
                output.append({
                    "location_id": str(location_id),
                    "item_id": str(item_id),
                    "name": str(name),
                    "type_id": str(type_id),
                    "x": float(px),
                    "y": float(py),
                    "distance": round(distance, 1),
                    "description": str(description or ""),
                })
        output.sort(key=lambda row: row["distance"])
        return output[:max(0, int(limit))]


class RecordedMinimapMapLocator:
    """Match the fixed-centre recorded minimap directly to world-map assets.

    Unlike incremental odometry, every accepted sample has an absolute game
    coordinate.  This makes the result immune to video cuts and lets later
    stages associate each numbered collection event with nearby database
    points.  The class is offline-only and has no game process dependency.
    """

    def __init__(self, assets_dir: str | Path, map_id: str | int):
        self.feature_locator = MapFeatureLocator(assets_dir, map_id)
        self.map_id = str(map_id)

    @staticmethod
    def prepare(frame: np.ndarray) -> tuple[np.ndarray, tuple[float, float]]:
        patch, mask = extract_recorded_minimap(frame)
        masked = cv2.bitwise_and(patch, patch, mask=mask)
        gray = cv2.cvtColor(masked, cv2.COLOR_BGR2GRAY)
        height, width = gray.shape[:2]
        return gray, (width * 0.47, height * 0.47)

    def locate(self, frame: np.ndarray, time_seconds: float,
               region: tuple[int, int, int, int] | None = None) -> MapAnchor:
        gray, centre = self.prepare(frame)
        result = self.feature_locator.engine.match_array(
            gray, region=region, crop_size=0, constrained=True)
        map_point = None
        game_point = None
        if result.H is not None:
            map_point, game_point = project_point(
                result.H, centre, self.feature_locator.coords)
        accepted = (
            result.success and result.H is not None and
            result.match_count >= 8 and result.inlier_count >= 6 and
            result.confidence >= 0.60 and 0.20 <= result.map_scale <= 20.0
        )
        if not accepted:
            return MapAnchor(
                round(float(time_seconds), 3), self.map_id, False,
                result.match_count, result.inlier_count,
                round(result.confidence, 4), round(result.map_scale, 4),
                result.game_center, centre, map_point, game_point,
                "小地图特征匹配未通过置信度/内点/尺度校验",
            )
        if not self.feature_locator.coords.contains(*game_point):
            return MapAnchor(
                round(float(time_seconds), 3), self.map_id, False,
                result.match_count, result.inlier_count,
                round(result.confidence, 4), round(result.map_scale, 4),
                result.game_center, centre, map_point, game_point,
                "小地图中心投影超出地图坐标范围",
            )
        return MapAnchor(
            round(float(time_seconds), 3), self.map_id, True,
            result.match_count, result.inlier_count,
            round(result.confidence, 4), round(result.map_scale, 4),
            result.game_center, centre, map_point, game_point,
        )

    def locate_near(self, frame: np.ndarray, time_seconds: float,
                    game_position: Sequence[float],
                    region_size: int = 1000) -> MapAnchor:
        """Match within a square map region centred on a candidate point."""
        pixel_x, pixel_y = self.feature_locator.coords.game_to_pixel(
            float(game_position[0]), float(game_position[1]))
        size = max(200, int(region_size))
        region = (round(pixel_x - size / 2), round(pixel_y - size / 2),
                  size, size)
        return self.locate(frame, time_seconds, region=region)

def map_assets_ready(assets_dir: str | Path, map_id: str | int) -> bool:
    folder = Path(assets_dir)
    return all(path.is_file() for path in (
        folder / "map_coords.json",
        folder / "setting.json",
        folder / f"{map_id}_siftgz.npz",
    ))


def infer_greedy_collection_route(
        assets_dir: str | Path, map_id: str | int,
        start_position: Sequence[float],
        sequence: Sequence[str] = P5_COLLECTION_SEQUENCE,
        type_sequence: Sequence[str | None] | None = None,
        maximum_step: float = 40000.0) -> dict:
    """Build a deterministic, explicitly unverified point-order hypothesis.

    This does not claim that nearest-neighbour order is the video's route.  It
    narrows the database search to one candidate per visible category so the
    video/odometry validator has a concrete chain to accept or reject.
    """
    database = Path(assets_dir) / "map_items.db"
    if not database.is_file():
        return {"status": "missing_database", "nodes": []}
    all_types = sorted({item for category in sequence
                        for item in COLLECTION_CATEGORY_TYPES.get(category, ())})
    if not all_types:
        return {"status": "empty_sequence", "nodes": []}
    placeholders = ",".join("?" for _ in all_types)
    query = f"""
        SELECT l.id, l.item_id, i.name, l.type_id, l.x, l.y,
               l.description
        FROM location l
        JOIN item i ON i.id = l.item_id
        WHERE l.state_id = ? AND l.type_id IN ({placeholders})
    """
    with sqlite3.connect(str(database)) as connection:
        rows = connection.execute(
            query, [int(map_id), *all_types]).fetchall()
    position = float(start_position[0]), float(start_position[1])
    used: set[str] = set()
    nodes = []
    total_distance = 0.0
    for order, category in enumerate(sequence, 1):
        accepted_types = _types_for_order(category, order, type_sequence)
        candidates = []
        for row in rows:
            location_id, _, _, type_id, x, y, _ = row
            if str(location_id) in used or type_id not in accepted_types:
                continue
            distance = math.hypot(float(x) - position[0],
                                  float(y) - position[1])
            if distance <= maximum_step:
                candidates.append((distance, row))
        if not candidates:
            return {
                "status": "incomplete",
                "start_position": list(start_position),
                "expected_nodes": len(sequence),
                "nodes": nodes,
                "failed_order": order,
                "failed_category": category,
            }
        distance, row = min(candidates, key=lambda item: item[0])
        location_id, item_id, name, type_id, x, y, description = row
        node = {
            "order": order,
            "category": category,
            "location_id": str(location_id),
            "item_id": str(item_id),
            "name": str(name),
            "type_id": str(type_id),
            "x": float(x),
            "y": float(y),
            "distance_from_previous": round(distance, 1),
            "description": str(description or ""),
            "assignment": "nearest_unvisited_of_expected_category",
            "verified": False,
        }
        nodes.append(node)
        used.add(str(location_id))
        total_distance += distance
        position = float(x), float(y)
    return {
        "status": "candidate_unverified",
        "start_position": [float(start_position[0]),
                           float(start_position[1])],
        "expected_nodes": len(sequence),
        "total_distance": round(total_distance, 1),
        "nodes": nodes,
    }


def summarise_motion_intervals(points: Sequence[dict],
                               transitions: Sequence[dict],
                               start_time: float) -> list[dict]:
    """Aggregate accepted minimap odometry between numbered collection times."""
    output = []
    previous_time = float(start_time)
    for transition in transitions:
        end_time = float(transition["time_seconds"])
        selected = [point for point in points
                    if previous_time < float(point["time_seconds"]) <= end_time]
        dx = sum(float(point["dx_pixels"]) for point in selected)
        dy = sum(float(point["dy_pixels"]) for point in selected)
        path_length = sum(math.hypot(float(point["dx_pixels"]),
                                     float(point["dy_pixels"]))
                          for point in selected)
        net = math.hypot(dx, dy)
        duration = max(0.001, end_time - previous_time)
        segment_count = len({int(point["segment"]) for point in selected})
        coverage = len(selected) / duration
        straightness = net / max(path_length, 1e-6)
        # A tracker reset means an edited cut, a black transition or a lost
        # minimap interval.  Summed pixels across that boundary have no shared
        # origin, so their direction must not influence point assignment.
        reliable = (len(selected) >= 4 and coverage >= 0.45 and
                    path_length >= 2.0 and net >= 2.5 and
                    segment_count == 1)
        output.append({
            "order": int(transition["order"]),
            "category": transition.get("category"),
            "start_seconds": round(previous_time, 3),
            "end_seconds": round(end_time, 3),
            "sample_count": len(selected),
            "coverage": round(coverage, 4),
            "segment_count": segment_count,
            "dx_pixels": round(dx, 4),
            "dy_pixels": round(dy, 4),
            "net_pixels": round(net, 4),
            "path_pixels": round(path_length, 4),
            "straightness": round(straightness, 4),
            "reliable_direction": reliable,
        })
        previous_time = end_time
    return output


def infer_motion_guided_collection_route(
        assets_dir: str | Path, map_id: str | int,
        start_position: Sequence[float], motion_intervals: Sequence[dict],
        sequence: Sequence[str] = P5_COLLECTION_SEQUENCE,
        type_sequence: Sequence[str | None] | None = None,
        maximum_step: float = 40000.0, beam_width: int = 120,
        fixed_location_ids: Mapping[int, str] | None = None) -> dict:
    """Search point orders whose directions and distances agree with video."""
    if len(motion_intervals) != len(sequence):
        return {"status": "motion_count_mismatch", "nodes": []}
    database = Path(assets_dir) / "map_items.db"
    all_types = sorted({item for category in sequence
                        for item in COLLECTION_CATEGORY_TYPES.get(category, ())})
    placeholders = ",".join("?" for _ in all_types)
    query = f"""
        SELECT l.id, l.item_id, i.name, l.type_id, l.x, l.y,
               l.description
        FROM location l JOIN item i ON i.id = l.item_id
        WHERE l.state_id = ? AND l.type_id IN ({placeholders})
    """
    with sqlite3.connect(str(database)) as connection:
        rows = connection.execute(query, [int(map_id), *all_types]).fetchall()
    fixed_location_ids = {
        int(order): str(location_id)
        for order, location_id in (fixed_location_ids or {}).items()
    }

    # score, position, used ids, nodes, per-step scale estimates, cosines
    beams = [(0.0, (float(start_position[0]), float(start_position[1])),
              frozenset(), [], [], [])]
    for order, (category, motion) in enumerate(
            zip(sequence, motion_intervals), 1):
        accepted_types = _types_for_order(category, order, type_sequence)
        expanded = []
        for score, position, used, nodes, scales, cosines in beams:
            baseline_scale = float(np.median(scales)) if scales else 100.0
            for row in rows:
                location_id, item_id, name, type_id, x, y, description = row
                location_id = str(location_id)
                if location_id in used or type_id not in accepted_types:
                    continue
                fixed_id = fixed_location_ids.get(order)
                if fixed_id is not None and location_id != fixed_id:
                    continue
                wx = float(x) - position[0]
                wy = float(y) - position[1]
                distance = math.hypot(wx, wy)
                if distance > maximum_step:
                    continue
                mx = float(motion["dx_pixels"])
                my = float(motion["dy_pixels"])
                net = math.hypot(mx, my)
                path = max(float(motion["path_pixels"]), 0.25)
                cosine = 0.0
                direction_cost = 0.0
                weight = 0.0
                if motion.get("reliable_direction") and distance > 1 and net > 1:
                    cosine = max(-1.0, min(1.0,
                        (wx * mx + wy * my) / (distance * net)))
                    weight = max(0.25, float(motion.get("straightness", 0.0)))
                    direction_cost = (1.0 - cosine) * 5.0 * weight
                candidate_scale = distance / path
                scale_cost = abs(math.log(max(candidate_scale, 1e-6) /
                                          max(baseline_scale, 1e-6))) * 0.7
                if not 25.0 <= candidate_scale <= 400.0:
                    scale_cost += 1.5
                distance_cost = distance / maximum_step * 0.18
                next_score = score + direction_cost + scale_cost + distance_cost
                node = {
                    "order": order,
                    "category": category,
                    "location_id": location_id,
                    "item_id": str(item_id),
                    "name": str(name),
                    "type_id": str(type_id),
                    "x": float(x),
                    "y": float(y),
                    "distance_from_previous": round(distance, 1),
                    "description": str(description or ""),
                    "assignment": "video_motion_beam_search",
                    "verified": fixed_location_ids.get(order) == location_id,
                    "motion_evidence": {
                        "direction_cosine": round(cosine, 4),
                        "direction_weight": round(weight, 4),
                        "world_units_per_path_pixel": round(candidate_scale, 3),
                        "interval": motion,
                    },
                }
                next_scales = scales + ([candidate_scale]
                                        if motion.get("reliable_direction") else [])
                next_cosines = cosines + ([cosine]
                                          if motion.get("reliable_direction") else [])
                expanded.append((next_score, (float(x), float(y)),
                                 used | {location_id}, nodes + [node],
                                 next_scales, next_cosines))
        if not expanded:
            return {"status": "incomplete", "failed_order": order,
                    "nodes": beams[0][3] if beams else []}
        expanded.sort(key=lambda item: item[0])
        beams = expanded[:max(1, int(beam_width))]
    score, _, _, nodes, scales, cosines = beams[0]
    total_distance = sum(node["distance_from_previous"] for node in nodes)
    return {
        "status": "candidate_motion_guided",
        "automation_ready": False,
        "start_position": [float(start_position[0]), float(start_position[1])],
        "expected_nodes": len(sequence),
        "score": round(score, 4),
        "total_distance": round(total_distance, 1),
        "median_world_units_per_path_pixel": (
            round(float(np.median(scales)), 3) if scales else None),
        "median_direction_cosine": (
            round(float(np.median(cosines)), 4) if cosines else None),
        "nodes": nodes,
        "warning": "视频运动约束已参与点位搜索，但仍需关键帧语义和实机闭环验证",
    }


def fit_motion_affine(start_position: Sequence[float],
                      motion_a: Sequence[float], world_a: Sequence[float],
                      motion_b: Sequence[float], world_b: Sequence[float]
                      ) -> Optional[np.ndarray]:
    """Fit a 2×2 pixel-to-world transform from two anchored events."""
    motion = np.column_stack((np.asarray(motion_a, dtype=np.float64),
                              np.asarray(motion_b, dtype=np.float64)))
    if abs(float(np.linalg.det(motion))) < 1e-6:
        return None
    start = np.asarray(start_position, dtype=np.float64)
    world = np.column_stack((np.asarray(world_a, dtype=np.float64) - start,
                             np.asarray(world_b, dtype=np.float64) - start))
    return world @ np.linalg.inv(motion)


def infer_affine_anchored_collection_route(
        assets_dir: str | Path, map_id: str | int,
        start_position: Sequence[float], motion_intervals: Sequence[dict],
        sequence: Sequence[str] = P5_COLLECTION_SEQUENCE,
        type_sequence: Sequence[str | None] | None = None,
        fixed_location_ids: Mapping[int, str] | None = None,
        search_radius: float = 40000.0) -> dict:
    """Project all video events using the sound-box/scenic-point anchors."""
    if len(motion_intervals) != len(sequence):
        return {"status": "motion_count_mismatch", "nodes": []}
    database = Path(assets_dir) / "map_items.db"
    all_types = sorted({item for category in sequence
                        for item in COLLECTION_CATEGORY_TYPES[category]})
    placeholders = ",".join("?" for _ in all_types)
    query = f"""
        SELECT l.id, l.item_id, i.name, l.type_id, l.x, l.y,
               l.description
        FROM location l JOIN item i ON i.id = l.item_id
        WHERE l.state_id = ? AND l.type_id IN ({placeholders})
    """
    with sqlite3.connect(str(database)) as connection:
        raw_rows = connection.execute(query, [int(map_id), *all_types]).fetchall()
    start = np.asarray(start_position, dtype=np.float64)
    rows = [row for row in raw_rows
            if math.hypot(float(row[4]) - start[0],
                          float(row[5]) - start[1]) <= search_radius]
    cumulative = []
    current = np.zeros(2, dtype=np.float64)
    for interval in motion_intervals:
        current = current + np.asarray(
            [interval["dx_pixels"], interval["dy_pixels"]], dtype=np.float64)
        cumulative.append(current.copy())

    sound_index = sequence.index("sound_box")
    scenic_index = sequence.index("scenic_point")
    sound_rows = [row for row in rows if row[3] in COLLECTION_CATEGORY_TYPES["sound_box"]]
    scenic_rows = [row for row in rows if row[3] in COLLECTION_CATEGORY_TYPES["scenic_point"]]
    fixed_location_ids = {
        int(order): str(location_id)
        for order, location_id in (fixed_location_ids or {}).items()
    }
    if fixed_location_ids.get(sound_index + 1):
        sound_rows = [row for row in sound_rows
                      if str(row[0]) == fixed_location_ids[sound_index + 1]]
    if fixed_location_ids.get(scenic_index + 1):
        scenic_rows = [row for row in scenic_rows
                       if str(row[0]) == fixed_location_ids[scenic_index + 1]]
    solutions = []
    for sound in sound_rows:
        for scenic in scenic_rows:
            transform = fit_motion_affine(
                start, cumulative[sound_index], (sound[4], sound[5]),
                cumulative[scenic_index], (scenic[4], scenic[5]))
            if transform is None:
                continue
            singular = np.linalg.svd(transform)[1]
            condition = float(singular[0] / max(singular[-1], 1e-9))
            determinant = float(np.linalg.det(transform))
            if (determinant <= 0 or condition > 4.0 or
                    singular[-1] < 20 or singular[0] > 450):
                continue
            projected = [start + transform @ point for point in cumulative]
            used: set[str] = set()
            nodes = []
            errors = []
            valid = True
            for index, (category, prediction) in enumerate(zip(sequence, projected)):
                if index == sound_index:
                    candidates = [sound]
                elif index == scenic_index:
                    candidates = [scenic]
                else:
                    accepted = _types_for_order(
                        category, index + 1, type_sequence)
                    candidates = [row for row in rows
                                  if row[3] in accepted and str(row[0]) not in used]
                if not candidates:
                    valid = False
                    break
                selected = min(candidates, key=lambda row: math.hypot(
                    float(row[4]) - prediction[0],
                    float(row[5]) - prediction[1]))
                location_id, item_id, name, type_id, x, y, description = selected
                location_id = str(location_id)
                error = math.hypot(float(x) - prediction[0],
                                   float(y) - prediction[1])
                previous = start if not nodes else np.asarray(
                    [nodes[-1]["x"], nodes[-1]["y"]])
                distance = math.hypot(float(x) - previous[0],
                                      float(y) - previous[1])
                anchored = index in (sound_index, scenic_index)
                node = {
                    "order": index + 1,
                    "category": category,
                    "location_id": location_id,
                    "item_id": str(item_id),
                    "name": str(name),
                    "type_id": str(type_id),
                    "x": float(x),
                    "y": float(y),
                    "distance_from_previous": round(distance, 1),
                    "description": str(description or ""),
                    "assignment": "video_affine_anchor_projection",
                    "verified": anchored,
                    "projection": {
                        "x": round(float(prediction[0]), 1),
                        "y": round(float(prediction[1]), 1),
                        "error": round(error, 1),
                        "semantic_anchor": anchored,
                    },
                    "motion_evidence": {"interval": motion_intervals[index]},
                }
                nodes.append(node)
                used.add(location_id)
                errors.append(error)
            if not valid:
                continue
            median_error = float(np.median(errors))
            mean_error = float(np.mean(errors))
            score = median_error + mean_error * 0.35 + condition * 120.0
            solutions.append((score, nodes, errors, transform, singular,
                              condition, sound, scenic))
    if not solutions:
        return {"status": "no_valid_anchor_transform", "nodes": []}
    score, nodes, errors, transform, singular, condition, sound, scenic = min(
        solutions, key=lambda item: item[0])
    # Only semantic anchors are fully verified at this stage.  Projection
    # proximity is recorded separately and becomes a gate for later review.
    close_count = sum(error <= 2500.0 for error in errors)
    return {
        "status": "candidate_affine_anchored",
        "automation_ready": False,
        "expected_nodes": len(sequence),
        "score": round(float(score), 3),
        "median_projection_error": round(float(np.median(errors)), 1),
        "mean_projection_error": round(float(np.mean(errors)), 1),
        "max_projection_error": round(float(np.max(errors)), 1),
        "nodes_within_2500": close_count,
        "transform": [[round(float(value), 6) for value in row]
                      for row in transform],
        "singular_values": [round(float(value), 4) for value in singular],
        "condition_number": round(condition, 4),
        "sound_anchor_id": str(sound[0]),
        "scenic_anchor_id": str(scenic[0]),
        "nodes": nodes,
        "warning": "二维轨迹已由声匣和观景点锚定；其余节点仍需画面语义或实机验证",
    }
