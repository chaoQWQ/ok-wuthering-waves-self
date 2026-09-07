"""Offline validator for turning a collection video into route evidence.

The validator deliberately does not interact with the game.  It reads a local
video, samples visual signals that are useful for route calibration and writes
derived reports/keyframes.  A draft is only marked automation-ready when a
reliable absolute coordinate trace exists; compass-only timelines are never
treated as world-coordinate routes.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

import cv2
import numpy as np


BIG_MAP_COLOR_CHECKS = (
    ((0.030, 0.094), (98, 150, 166)),
    ((0.890, 0.059), (249, 249, 238)),
    ((0.939, 0.931), (255, 255, 255)),
    ((0.035, 0.891), (236, 237, 235)),
)
BIG_MAP_COLOR_TOLERANCE = 18


@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    fps: float
    frame_count: int
    duration_seconds: float


@dataclass(frozen=True)
class CoordinateProbe:
    time_seconds: float
    raw_text: str
    coordinate: Optional[tuple[int, int, int]]
    image: str = ""


@dataclass(frozen=True)
class FrameEvidence:
    time_seconds: float
    scene_score: float
    motion_score: float
    reward_score: float
    big_map_score: float
    compass_heading: Optional[float]
    compass_confidence: float


def inspect_video(path: str | Path) -> VideoInfo:
    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            raise ValueError(f"无法打开视频：{path}")
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = frame_count / fps if fps > 0 else 0.0
        if width <= 0 or height <= 0 or duration <= 0:
            raise ValueError(f"视频元数据无效：{path}")
        return VideoInfo(width, height, fps, frame_count, duration)
    finally:
        capture.release()


def coordinate_crop(frame: np.ndarray) -> np.ndarray:
    """Return the same lower-left coordinate strip used by PositionDetector."""
    height, width = frame.shape[:2]
    scale = height / 1080.0
    right = min(width, max(1, round(240 * scale)))
    upper = min(height - 1, max(0, round(1050 * scale)))
    return frame[upper:height, 0:right]


def parse_coordinate_text(text: str) -> Optional[tuple[int, int, int]]:
    """Parse a conservative comma-separated three-dimensional coordinate."""
    if not text:
        return None
    normalised = text.replace("，", ",").replace(" ", "")
    parts = normalised.split(",")
    if len(parts) != 3:
        return None
    if not all(part and part.lstrip("-").isdigit() for part in parts):
        return None
    values = tuple(int(part) for part in parts)
    if any(abs(value) > 99999 for value in values):
        return None
    return values


def big_map_score(frame: np.ndarray) -> float:
    height, width = frame.shape[:2]
    matched = 0
    for (rx, ry), expected in BIG_MAP_COLOR_CHECKS:
        pixel = frame[min(height - 1, round(ry * height)),
                      min(width - 1, round(rx * width))]
        if np.all(np.abs(pixel.astype(np.int16) - np.asarray(expected))
                  <= BIG_MAP_COLOR_TOLERANCE):
            matched += 1
    return matched / len(BIG_MAP_COLOR_CHECKS)


def recorded_big_map_score(frame: np.ndarray,
                           compass_confidence: float) -> float:
    """Recognise both current OK-WW maps and older cropped video maps.

    The P5 source predates the current live-map colour checks and omits most
    map UI chrome.  In that layout the gameplay minimap/arrow and right-hand
    party portraits both disappear while a moderately bright textured map
    remains.  Requiring all three conditions keeps black transitions out.
    """
    reference = big_map_score(frame)
    if reference >= 0.75 or compass_confidence > 0:
        return reference
    height, width = frame.shape[:2]
    party_strip = frame[round(height * 0.16):round(height * 0.62),
                        round(width * 0.90):width]
    gray = cv2.cvtColor(party_strip, cv2.COLOR_BGR2GRAY)
    mean = float(np.mean(gray))
    deviation = float(np.std(gray))
    edges = float(np.mean(cv2.Canny(gray, 60, 140) > 0))
    if 32 <= mean <= 190 and deviation < 30 and 0.018 <= edges <= 0.10:
        return 0.75
    return reference


def compass_heading(frame: np.ndarray) -> tuple[Optional[float], float]:
    """Estimate the yellow player-arrow direction in the upper-left minimap.

    This is calibration evidence, not a world heading.  The component nearest
    the expected minimap centre is selected and the more acute end of its major
    PCA axis is treated as the arrow tip.
    """
    height, width = frame.shape[:2]
    crop = frame[:max(1, round(height * 0.23)),
                 :max(1, round(width * 0.17))]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (17, 105, 170), (40, 255, 255))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                            np.ones((2, 2), np.uint8))
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask)
    # In the 16:9 recording layout the minimap centre is near (9.8%, 16.5%)
    # of the full frame.  Expressed relative to the conservative crop above,
    # that is approximately (57.5%, 72%).
    expected = np.asarray((crop.shape[1] * 0.575, crop.shape[0] * 0.72))
    candidates: list[tuple[float, int]] = []
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if 18 <= area <= 1600:
            distance = float(np.linalg.norm(centroids[label] - expected))
            if distance <= min(crop.shape[:2]) * 0.34:
                candidates.append((distance - min(area, 300) * 0.03, label))
    if not candidates:
        return None, 0.0
    label = min(candidates)[1]
    ys, xs = np.nonzero(labels == label)
    points = np.column_stack((xs, ys)).astype(np.float32)
    if len(points) < 5:
        return None, 0.0
    mean, eigenvectors, eigenvalues = cv2.PCACompute2(points, mean=None)
    centre = mean[0]
    axis = eigenvectors[0]
    projections = (points - centre) @ axis
    positive = float(np.max(projections))
    negative = float(-np.min(projections))
    if negative > positive:
        axis = -axis
    # Prefer the end containing fewer pixels: an arrow tip narrows, while the
    # tail is wider.  This also resolves many symmetric PCA ambiguities.
    signed = (points - centre) @ axis
    span = max(abs(float(signed.min())), abs(float(signed.max())), 1.0)
    pos_count = int(np.count_nonzero(signed > span * 0.45))
    neg_count = int(np.count_nonzero(signed < -span * 0.45))
    if pos_count > neg_count * 1.35:
        axis = -axis
    heading = math.degrees(math.atan2(float(axis[0]), -float(axis[1]))) % 360
    elongation = float(eigenvalues[0, 0] /
                       max(eigenvalues[1, 0], 1e-6))
    selected_area = float(stats[label, cv2.CC_STAT_AREA])
    selected_distance = float(np.linalg.norm(centroids[label] - expected))
    maximum_distance = max(1.0, min(crop.shape[:2]) * 0.34)
    proximity = max(0.0, 1.0 - selected_distance / maximum_distance)
    area_quality = min(1.0, selected_area / 240.0)
    shape_quality = min(1.0, max(0.25, (elongation - 0.9) / 1.2))
    confidence = proximity * (0.55 + 0.30 * area_quality +
                              0.15 * shape_quality)
    return round(heading, 2), round(confidence, 3)


def _small_gray(frame: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, (160, 90), interpolation=cv2.INTER_AREA)


def reward_score(frame: np.ndarray) -> float:
    """Measure UI-like bright detail in the reward-toast region."""
    height, width = frame.shape[:2]
    roi = frame[round(height * 0.56):round(height * 0.91),
                round(width * 0.70):round(width * 0.985)]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    bright = np.count_nonzero(hsv[..., 2] > 205) / hsv[..., 2].size
    yellow = np.count_nonzero(
        (hsv[..., 0] >= 16) & (hsv[..., 0] <= 40) &
        (hsv[..., 1] > 80) & (hsv[..., 2] > 155)
    ) / hsv[..., 0].size
    edges = cv2.Canny(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY), 80, 180)
    edge_ratio = np.count_nonzero(edges) / edges.size
    return round(float(bright + yellow * 2.2 + edge_ratio * 0.45), 6)


def create_onnx_coordinate_ocr() -> Callable[[np.ndarray], str]:
    """Create the same local OCR backend used by OK-WW, without the app.

    The import and model startup are intentionally lazy so tests and callers
    that only need visual metrics do not pay the OCR startup cost.
    """
    from onnxocr.onnx_paddleocr import ONNXPaddleOcr

    engine = ONNXPaddleOcr(
        use_openvino=True, use_npu=False, use_angle_cls=False)

    def recognise(crop: np.ndarray) -> str:
        enlarged = cv2.resize(crop, None, fx=3.0, fy=3.0,
                              interpolation=cv2.INTER_LANCZOS4)
        result = engine.ocr(enlarged, det=False, rec=True, cls=False)
        rows = result[0] if result else []
        return "".join(str(row[0]) for row in rows if row)

    return recognise


def write_image(path: str | Path, image: np.ndarray) -> bool:
    """Write an OpenCV image to a Unicode path on Windows."""
    target = Path(path)
    suffix = target.suffix or ".jpg"
    ok, encoded = cv2.imencode(suffix, image)
    if not ok:
        return False
    encoded.tofile(str(target))
    return True


def robust_threshold(values: Sequence[float], factor: float = 3.0) -> float:
    if not values:
        return float("inf")
    median = statistics.median(values)
    deviations = [abs(value - median) for value in values]
    mad = statistics.median(deviations)
    return median + max(0.002, factor * mad)


def select_peaks(evidence: Sequence[FrameEvidence], field: str,
                 threshold: float, minimum_gap: float,
                 limit: int) -> list[FrameEvidence]:
    eligible = [item for item in evidence
                if float(getattr(item, field)) >= threshold]
    selected: list[FrameEvidence] = []
    for item in sorted(eligible, key=lambda row: getattr(row, field),
                       reverse=True):
        if all(abs(item.time_seconds - prior.time_seconds) >= minimum_gap
               for prior in selected):
            selected.append(item)
            if len(selected) >= limit:
                break
    return sorted(selected, key=lambda row: row.time_seconds)


class VideoRouteValidator:
    def __init__(self, video_path: str | Path, manifest: dict,
                 output_dir: str | Path, sample_interval: float = 0.5,
                 coordinate_ocr: Optional[Callable[[np.ndarray], str]] = None,
                 map_assets_dir: Optional[str | Path] = None):
        self.video_path = Path(video_path)
        self.manifest = manifest
        self.output_dir = Path(output_dir)
        self.sample_interval = max(0.2, float(sample_interval))
        self.coordinate_ocr = coordinate_ocr
        self.map_assets_dir = (Path(map_assets_dir)
                               if map_assets_dir is not None else None)

    def run(self) -> dict:
        info = inspect_video(self.video_path)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        keyframe_dir = self.output_dir / "关键帧"
        coordinate_dir = self.output_dir / "坐标探针"
        keyframe_dir.mkdir(exist_ok=True)
        coordinate_dir.mkdir(exist_ok=True)

        probes = self._probe_coordinates(info, coordinate_dir)
        evidence = self._scan_frames(info)
        scene_threshold = robust_threshold(
            [row.scene_score for row in evidence], 4.0)
        reward_threshold = robust_threshold(
            [row.reward_score for row in evidence], 3.5)
        scene_peaks = select_peaks(evidence, "scene_score", scene_threshold,
                                   5.0, 18)
        reward_peaks = select_peaks(evidence, "reward_score", reward_threshold,
                                    6.0, 28)
        map_peaks = select_peaks(evidence, "big_map_score", 0.75, 3.0, 20)
        map_anchors = self._align_map_anchors(map_peaks)
        minimap_trace = self._scan_minimap_trace(info, evidence, map_anchors)
        event_frames = self._write_progress_event_frames(
            minimap_trace.get("progress_timeline", {}))
        minimap_trace["progress_timeline"]["event_frames"] = event_frames
        route_hypothesis = self._build_route_hypothesis(
            map_anchors, minimap_trace)
        chosen = self._combined_keyframes(scene_peaks, reward_peaks, map_peaks)
        keyframes = self._write_keyframes(chosen, keyframe_dir)
        overview = self._write_contact_sheet(keyframes)
        coordinate_hits = [probe for probe in probes if probe.coordinate]
        compass_hits = [row for row in evidence
                        if row.compass_heading is not None and
                        row.compass_confidence >= 0.15]

        reasons = []
        if len(coordinate_hits) < 2:
            reasons.append("视频未显示可连续识别的三维坐标，无法恢复绝对世界位置")
        if not map_peaks:
            reasons.append("未检出足够可靠的大地图画面，无法直接把视频轨迹锚定到地图")
        successful_anchors = [anchor for anchor in map_anchors
                              if anchor.get("success")]
        if map_peaks and not successful_anchors:
            reasons.append("检出大地图画面，但尚未得到通过校验的世界坐标锚点")
        coordinate_source_available = len(coordinate_hits) >= 3
        # This first-stage validator only probes 12 frames.  Even successful
        # probes are not yet a continuous, closed-loop route and must never be
        # presented as safe to execute automatically.
        automation_ready = False
        status = ("coordinate_source_detected" if coordinate_source_available
                  else "needs_map_calibration")
        if coordinate_source_available:
            reasons.append("当前仅完成坐标抽样，尚未生成连续且经过地图校验的坐标轨迹")
        report = {
            "schema_version": 1,
            "validator": "p5_video_route_validator",
            "source_manifest": self.manifest,
            "video": {"path": str(self.video_path), **asdict(info)},
            "sampling": {
                "interval_seconds": self.sample_interval,
                "frame_samples": len(evidence),
                "coordinate_probes": len(probes),
                "coordinate_hits": len(coordinate_hits),
                "coordinate_ocr_enabled": self.coordinate_ocr is not None,
                "compass_hits": len(compass_hits),
            },
            "candidates": {
                "scene_changes": [self._candidate(row, "scene_score")
                                  for row in scene_peaks],
                "reward_or_interaction": [self._candidate(row, "reward_score")
                                          for row in reward_peaks],
                "big_map": [self._candidate(row, "big_map_score")
                            for row in map_peaks],
                "map_anchors": map_anchors,
                "keyframes": keyframes,
                "overview": overview,
            },
            "coordinate_probes": [asdict(probe) for probe in probes],
            "minimap_odometry": minimap_trace,
            "route_hypothesis": route_hypothesis,
            "conversion": {
                "status": status,
                "automation_ready": automation_ready,
                "coordinate_source_available": coordinate_source_available,
                "absolute_anchor_available": bool(successful_anchors),
                "reasons": reasons,
                "next_step": (
                    "对全片做连续坐标 OCR，再关联地图点位" if coordinate_source_available else
                    "从视频小地图恢复锚点之间的轨迹，并把领取时刻关联到附近地图点位"
                    if successful_anchors else
                    "用关键帧把领取时刻与地图点位配对，再用一次游戏内跟随录制补齐世界坐标"
                ),
            },
            "compass_timeline": [
                {"time_seconds": row.time_seconds,
                 "heading_degrees": row.compass_heading,
                 "confidence": row.compass_confidence,
                 "motion_score": row.motion_score}
                for row in compass_hits
            ],
        }
        point_review = self._build_point_review(report)
        report["point_review"] = point_review
        route_draft = self._route_draft(report)
        from src.utils.CollectionRoutePlan import preflight_collection_route
        preflight = preflight_collection_route(
            route_draft,
            path=self.output_dir / "P5路线草稿.json",
            expected_video_id=str(self.manifest.get("video_id") or ""),
            expected_part=int(self.manifest.get("part") or 0),
            expected_map_state_id=int(
                self.manifest.get("map", {}).get("state_id") or 0),
        )
        report["execution_preflight"] = preflight.as_dict()
        self._write_json(self.output_dir / "P5转换验证报告.json", report)
        self._write_json(self.output_dir / "P5路线草稿.json", route_draft)
        self._write_json(self.output_dir / "P5点位复核清单.json",
                         {"items": point_review})
        (self.output_dir / "P5转换验证报告.md").write_text(
            self._markdown(report), encoding="utf-8")
        (self.output_dir / "P5点位复核清单.md").write_text(
            self._point_review_markdown(point_review), encoding="utf-8")
        return report

    def _probe_coordinates(self, info: VideoInfo,
                           coordinate_dir: Path) -> list[CoordinateProbe]:
        times = np.linspace(3.0, max(3.0, info.duration_seconds - 3.0), 12)
        capture = cv2.VideoCapture(str(self.video_path))
        probes: list[CoordinateProbe] = []
        try:
            for index, seconds in enumerate(times, 1):
                capture.set(cv2.CAP_PROP_POS_MSEC, float(seconds * 1000))
                ok, frame = capture.read()
                if not ok:
                    continue
                crop = coordinate_crop(frame)
                image_name = f"坐标探针_{index:02}_{seconds:07.2f}s.jpg"
                write_image(coordinate_dir / image_name, crop)
                raw = self.coordinate_ocr(crop) if self.coordinate_ocr else ""
                coordinate = parse_coordinate_text(raw)
                probes.append(CoordinateProbe(
                    round(float(seconds), 3), raw, coordinate,
                    f"坐标探针/{image_name}"))
        finally:
            capture.release()
        return probes

    def _scan_frames(self, info: VideoInfo) -> list[FrameEvidence]:
        capture = cv2.VideoCapture(str(self.video_path))
        evidence: list[FrameEvidence] = []
        previous: Optional[np.ndarray] = None
        frame_index = 0
        sample_every = max(1, round(self.sample_interval * info.fps))
        try:
            # Decode the stream once and only materialise sampled frames.  A
            # random seek per sample is much slower on long-GOP web videos.
            while capture.grab():
                if frame_index % sample_every:
                    frame_index += 1
                    continue
                ok, frame = capture.retrieve()
                if not ok:
                    frame_index += 1
                    continue
                seconds = frame_index / info.fps
                gray = _small_gray(frame)
                difference = (float(np.mean(cv2.absdiff(gray, previous))) / 255.0
                              if previous is not None else 0.0)
                heading, confidence = compass_heading(frame)
                evidence.append(FrameEvidence(
                    round(seconds, 3), round(difference, 6),
                    round(difference, 6), reward_score(frame),
                    recorded_big_map_score(frame, confidence),
                    heading, confidence))
                previous = gray
                frame_index += 1
        finally:
            capture.release()
        return evidence

    def _align_map_anchors(self, candidates: Sequence[FrameEvidence]) -> list[dict]:
        if not candidates or self.map_assets_dir is None:
            return []
        map_info = self.manifest.get("map", {})
        map_id = map_info.get("state_id")
        if map_id is None:
            return []
        try:
            from src.utils.VideoMapCalibrator import (
                MapFeatureLocator, map_assets_ready,
            )
            if not map_assets_ready(self.map_assets_dir, map_id):
                return []
            locator = MapFeatureLocator(self.map_assets_dir, map_id)
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            return []
        capture = cv2.VideoCapture(str(self.video_path))
        anchors = []
        try:
            for candidate in candidates:
                capture.set(cv2.CAP_PROP_POS_MSEC,
                            candidate.time_seconds * 1000)
                ok, frame = capture.read()
                if not ok:
                    continue
                anchor = locator.locate(frame, candidate.time_seconds)
                payload = anchor.to_dict()
                if anchor.success and anchor.player_game:
                    payload["nearby_collections"] = locator.nearby_collections(
                        anchor.player_game)
                else:
                    payload["nearby_collections"] = []
                anchors.append(payload)
        finally:
            capture.release()
        return anchors

    def _scan_minimap_trace(self, info: VideoInfo,
                            evidence: Sequence[FrameEvidence],
                            anchors: Sequence[dict]) -> dict:
        from src.utils.VideoMapCalibrator import (
            RecordedMinimapTracker, decode_linear_progress,
            decode_monotonic_progress, locate_p5_progress_pointer,
            p5_pointer_template, p5_route_marker_scores,
        )

        interval = max(1.0, self.sample_interval)
        sample_every = max(1, round(interval * info.fps))
        evidence_by_time = {round(row.time_seconds, 1): row
                            for row in evidence}
        valid_anchors = sorted(
            (anchor for anchor in anchors if anchor.get("success")),
            key=lambda row: row["time_seconds"])
        tracker = RecordedMinimapTracker()
        capture = cv2.VideoCapture(str(self.video_path))
        frame_index = 0
        previous_time: Optional[float] = None
        segment = 0
        segment_open = False
        failure_streak = 0
        cumulative_x = cumulative_y = 0.0
        attempts = successes = 0
        points = []
        segments: dict[int, dict] = {}
        marker_times = []
        marker_scores = []
        pointer_x = []
        pointer_scores = []
        pointer_template = self._p5_pointer_template(
            info, p5_pointer_template)
        try:
            while capture.grab():
                if frame_index % sample_every:
                    frame_index += 1
                    continue
                ok, frame = capture.retrieve()
                if not ok:
                    frame_index += 1
                    continue
                seconds = frame_index / info.fps
                frame_evidence = evidence_by_time.get(round(seconds, 1))
                is_map = (frame_evidence is not None and
                          frame_evidence.big_map_score >= 0.75)
                has_gameplay_compass = (frame_evidence is not None and
                                        frame_evidence.compass_heading is not None)
                if is_map or not has_gameplay_compass:
                    tracker.reset()
                    previous_time = None
                    segment_open = False
                    failure_streak = 0
                    frame_index += 1
                    continue
                marker_times.append(seconds)
                marker_scores.append(p5_route_marker_scores(frame))
                px, pointer_score = locate_p5_progress_pointer(
                    frame, pointer_template)
                pointer_x.append(px)
                pointer_scores.append(pointer_score)
                motion = tracker.update(frame)
                if motion is None:
                    previous_time = seconds
                    frame_index += 1
                    continue
                attempts += 1
                displacement = math.hypot(
                    motion.player_dx_pixels, motion.player_dy_pixels)
                accepted = (
                    motion.success and 0.90 <= motion.scale <= 1.10 and
                    abs(motion.rotation_degrees) <= 10.0 and
                    displacement <= 30.0
                )
                if not accepted:
                    failure_streak += 1
                    scene_cut = (frame_evidence is not None and
                                 frame_evidence.scene_score >= 0.32)
                    if failure_streak >= 3 or scene_cut:
                        segment_open = False
                    previous_time = seconds
                    frame_index += 1
                    continue
                failure_streak = 0
                if not segment_open:
                    segment += 1
                    cumulative_x = cumulative_y = 0.0
                    anchor = next((item for item in reversed(valid_anchors)
                                   if item["time_seconds"] <= seconds and
                                   seconds - item["time_seconds"] <= 12.0), None)
                    segments[segment] = {
                        "segment": segment,
                        "start_seconds": round(float(previous_time or seconds), 3),
                        "end_seconds": round(float(seconds), 3),
                        "start_anchor": (anchor.get("player_game")
                                         if anchor else None),
                        "point_count": 0,
                    }
                    segment_open = True
                cumulative_x += motion.player_dx_pixels
                cumulative_y += motion.player_dy_pixels
                successes += 1
                point = {
                    "time_seconds": round(float(seconds), 3),
                    "segment": segment,
                    "dx_pixels": motion.player_dx_pixels,
                    "dy_pixels": motion.player_dy_pixels,
                    "x_pixels": round(cumulative_x, 4),
                    "y_pixels": round(cumulative_y, 4),
                    "confidence": motion.confidence,
                }
                points.append(point)
                segments[segment]["end_seconds"] = point["time_seconds"]
                segments[segment]["point_count"] += 1
                previous_time = seconds
                frame_index += 1
        finally:
            capture.release()
        progress = decode_linear_progress(
            marker_times, pointer_x, pointer_scores, info.width)
        if progress.get("status") != "decoded":
            fallback = decode_monotonic_progress(marker_times, marker_scores)
            progress["fallback"] = fallback
        return {
            "status": "pixel_trace_only",
            "interval_seconds": interval,
            "attempts": attempts,
            "successes": successes,
            "success_rate": round(successes / max(attempts, 1), 4),
            "scale_calibrated": False,
            "segments": list(segments.values()),
            "points": points,
            "progress_timeline": progress,
        }

    def _p5_pointer_template(self, info: VideoInfo,
                             template_function) -> np.ndarray:
        """Read P5's first active marker at the stable 30-second reference."""
        capture = cv2.VideoCapture(str(self.video_path))
        try:
            # Use a duration ratio so a constant-rate transcode remains valid.
            seconds = min(info.duration_seconds - 1.0,
                          info.duration_seconds * (30.0 / 568.287))
            capture.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
            ok, best_frame = capture.read()
        finally:
            capture.release()
        if not ok or best_frame is None:
            raise ValueError("无法提取 P5 进度光标模板")
        return template_function(best_frame)

    def _build_route_hypothesis(self, anchors: Sequence[dict],
                                minimap_trace: dict) -> dict:
        valid = [anchor for anchor in anchors
                 if anchor.get("success") and anchor.get("player_game")]
        if not valid or self.map_assets_dir is None:
            return {"status": "missing_anchor", "nodes": []}
        map_id = self.manifest.get("map", {}).get("state_id")
        if map_id is None:
            return {"status": "missing_map_id", "nodes": []}
        from src.utils.VideoMapCalibrator import (
            P5_COLLECTION_SEQUENCE,
            infer_affine_anchored_collection_route,
            infer_greedy_collection_route, infer_motion_guided_collection_route,
            summarise_motion_intervals,
        )

        type_sequence = self.manifest.get("timeline_type_ids")
        if (not isinstance(type_sequence, list) or
                len(type_sequence) != len(P5_COLLECTION_SEQUENCE)):
            type_sequence = None
        verified_location_ids = {
            int(order): str(location_id)
            for order, location_id in
            (self.manifest.get("verified_location_ids") or {}).items()
        }

        greedy = infer_greedy_collection_route(
            self.map_assets_dir, map_id, valid[-1]["player_game"],
            type_sequence=type_sequence)
        progress = minimap_trace.get("progress_timeline", {})
        transitions = progress.get("transitions", [])
        intervals = summarise_motion_intervals(
            minimap_trace.get("points", []), transitions,
            float(valid[-1]["time_seconds"]))
        if progress.get("status") == "decoded":
            step_guided = infer_motion_guided_collection_route(
                self.map_assets_dir, map_id, valid[-1]["player_game"],
                intervals, type_sequence=type_sequence)
            affine_guided = infer_affine_anchored_collection_route(
                self.map_assets_dir, map_id, valid[-1]["player_game"],
                intervals, type_sequence=type_sequence,
                fixed_location_ids=verified_location_ids)
            anchored_ids = dict(verified_location_ids)
            if affine_guided.get("sound_anchor_id"):
                anchored_ids.setdefault(5, affine_guided["sound_anchor_id"])
            if affine_guided.get("scenic_anchor_id"):
                anchored_ids.setdefault(6, affine_guided["scenic_anchor_id"])
            anchored_motion = infer_motion_guided_collection_route(
                self.map_assets_dir, map_id, valid[-1]["player_game"],
                intervals, type_sequence=type_sequence,
                fixed_location_ids=anchored_ids)
            # The motion route respects edited minimap segment boundaries and
            # the two visually confirmed semantic anchors.  It is a stronger
            # review candidate than a single transform over the whole video,
            # but remains non-executable until every event frame is confirmed.
            result = (anchored_motion if anchored_motion.get("nodes")
                      else affine_guided if affine_guided.get("nodes")
                      else step_guided)
            result["affine_projection_baseline"] = {
                "status": affine_guided.get("status"),
                "median_projection_error":
                    affine_guided.get("median_projection_error"),
                "location_ids": [node["location_id"]
                                 for node in affine_guided.get("nodes", [])],
                "nodes": affine_guided.get("nodes", []),
            }
            result["step_motion_baseline"] = {
                "status": step_guided.get("status"),
                "score": step_guided.get("score"),
                "location_ids": [node["location_id"]
                                 for node in step_guided.get("nodes", [])],
                "nodes": step_guided.get("nodes", []),
            }
        else:
            result = greedy
        result["motion_intervals"] = intervals
        result["greedy_baseline"] = {
            "status": greedy.get("status"),
            "total_distance": greedy.get("total_distance"),
            "location_ids": [node["location_id"]
                             for node in greedy.get("nodes", [])],
        }
        pixel_length = sum(math.hypot(point["dx_pixels"], point["dy_pixels"])
                           for point in minimap_trace.get("points", []))
        result["odometry_pixel_length"] = round(pixel_length, 3)
        total_distance = result.get("total_distance")
        result["implied_world_units_per_pixel"] = (
            round(total_distance / pixel_length, 3)
            if total_distance and pixel_length > 0 else None)
        result["automation_ready"] = False
        result.setdefault("warning",
                          "点位顺序尚未完成视频/实机双重验证，不能直接用于自动移动")
        return result

    @staticmethod
    def _combined_keyframes(*groups: Iterable[FrameEvidence]) -> list[FrameEvidence]:
        by_time: dict[float, FrameEvidence] = {}
        for group in groups:
            for item in group:
                by_time[item.time_seconds] = item
        return [by_time[key] for key in sorted(by_time)]

    def _write_keyframes(self, candidates: Sequence[FrameEvidence],
                         output_dir: Path) -> list[dict]:
        capture = cv2.VideoCapture(str(self.video_path))
        written = []
        try:
            for index, item in enumerate(candidates, 1):
                capture.set(cv2.CAP_PROP_POS_MSEC, item.time_seconds * 1000)
                ok, frame = capture.read()
                if not ok:
                    continue
                name = f"候选_{index:02}_{item.time_seconds:07.2f}s.jpg"
                if write_image(output_dir / name, frame):
                    written.append({"time_seconds": item.time_seconds,
                                    "image": f"关键帧/{name}"})
        finally:
            capture.release()
        return written

    def _write_contact_sheet(self, keyframes: Sequence[dict],
                             name: str = "关键帧总览.jpg") -> str:
        if not keyframes:
            return ""
        columns = 4
        cell_width, cell_height = 320, 200
        rows = math.ceil(len(keyframes) / columns)
        sheet = np.full((rows * cell_height, columns * cell_width, 3),
                        245, np.uint8)
        for index, item in enumerate(keyframes):
            image_path = self.output_dir / item["image"]
            encoded = np.fromfile(str(image_path), dtype=np.uint8)
            image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
            if image is None:
                continue
            thumb = cv2.resize(image, (cell_width, 180),
                               interpolation=cv2.INTER_AREA)
            x = (index % columns) * cell_width
            y = (index // columns) * cell_height
            sheet[y:y + 180, x:x + cell_width] = thumb
            cv2.putText(sheet, f"{item['time_seconds']:.2f}s", (x + 6, y + 195),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.48, (30, 30, 30), 1,
                        cv2.LINE_AA)
        return name if write_image(self.output_dir / name, sheet) else ""

    def _write_progress_event_frames(self, progress: dict) -> list[dict]:
        transitions = progress.get("transitions", [])
        if progress.get("status") != "decoded" or not transitions:
            return []
        folder = self.output_dir / "收集事件"
        folder.mkdir(exist_ok=True)
        capture = cv2.VideoCapture(str(self.video_path))
        written = []
        try:
            for item in transitions:
                seconds = float(item["time_seconds"])
                capture.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
                ok, frame = capture.read()
                if not ok:
                    continue
                order = int(item["order"])
                category = str(item.get("category") or "item")
                name = f"事件_{order:02}_{category}_{seconds:07.2f}s.jpg"
                if write_image(folder / name, frame):
                    written.append({
                        "order": order,
                        "category": category,
                        "time_seconds": round(seconds, 3),
                        "image": f"收集事件/{name}",
                    })
        finally:
            capture.release()
        if written:
            # The contact-sheet helper only relies on time_seconds/image.
            progress["event_overview"] = self._write_contact_sheet(
                written, "收集事件总览.jpg")
        return written

    @staticmethod
    def _candidate(row: FrameEvidence, score: str) -> dict:
        return {"time_seconds": row.time_seconds,
                "score": getattr(row, score)}

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    @staticmethod
    def _build_point_review(report: dict) -> list[dict]:
        """Build a per-event comparison without promoting any candidate."""
        hypothesis = report.get("route_hypothesis", {})
        source_groups = (
            ("锚定运动主候选", hypothesis.get("nodes", [])),
            ("全局仿射候选",
             hypothesis.get("affine_projection_baseline", {}).get("nodes", [])),
            ("无锚运动候选",
             hypothesis.get("step_motion_baseline", {}).get("nodes", [])),
        )
        event_frames = {
            int(item["order"]): item
            for item in report.get("minimap_odometry", {})
            .get("progress_timeline", {}).get("event_frames", [])
        }
        by_order: dict[int, dict[str, dict]] = {}
        for source, nodes in source_groups:
            for node in nodes:
                order = int(node["order"])
                location_id = str(node["location_id"])
                candidates = by_order.setdefault(order, {})
                if location_id not in candidates:
                    candidates[location_id] = {
                        "location_id": location_id,
                        "category": node.get("category", ""),
                        "type_id": node.get("type_id", ""),
                        "name": node.get("name", ""),
                        "x": node.get("x"),
                        "y": node.get("y"),
                        "description": node.get("description", ""),
                        "verified": bool(node.get("verified")),
                        "sources": [],
                    }
                candidates[location_id]["sources"].append(source)
                candidates[location_id]["verified"] = (
                    candidates[location_id]["verified"] or
                    bool(node.get("verified")))
        output = []
        for order in sorted(by_order):
            frame = event_frames.get(order, {})
            candidates = list(by_order[order].values())
            candidates.sort(key=lambda item: (
                "锚定运动主候选" not in item["sources"],
                item["location_id"]))
            output.append({
                "order": order,
                "category": candidates[0]["category"] if candidates else "",
                "time_seconds": frame.get("time_seconds"),
                "event_image": frame.get("image", ""),
                "candidate_count": len(candidates),
                "candidates": candidates,
            })
        return output

    @staticmethod
    def _point_review_markdown(items: Sequence[dict]) -> str:
        def clean(value: object) -> str:
            return str(value or "").replace("|", "\\|").replace("\n", " ")

        lines = [
            "# P5 点位复核清单",
            "",
            "本清单只比较算法候选，不代表点位已经验证。视频截图路径相对于本目录。",
            "",
            "|序号|时间|类别|候选来源|地图点位|坐标|地图说明|截图|",
            "|---:|---:|---|---|---|---|---|---|",
        ]
        for item in items:
            for candidate in item.get("candidates", []):
                sources = "、".join(candidate.get("sources", []))
                name = ("✅ " if candidate.get("verified") else "") + clean(
                    candidate.get("name"))
                coords = f"{candidate.get('x')}, {candidate.get('y')}"
                image = clean(item.get("event_image"))
                image_link = f"[查看]({image})" if image else ""
                lines.append(
                    f"|{item['order']}|{item.get('time_seconds', '')}|"
                    f"{clean(item.get('category'))}|{clean(sources)}|"
                    f"{name} `{clean(candidate.get('location_id'))}`|"
                    f"{clean(coords)}|{clean(candidate.get('description'))}|"
                    f"{image_link}|")
        lines.extend([
            "",
            "只有完成逐点画面/地图语义复核后，才能把对应节点的 `verified` 设为 true。",
        ])
        return "\n".join(lines) + "\n"

    def _route_draft(self, report: dict) -> dict:
        source = self.manifest
        hypothesis = report.get("route_hypothesis", {})
        candidate_nodes = hypothesis.get("nodes", [])
        return {
            "schema_version": source.get("schema_version", 1),
            "source": source.get("source", ""),
            "video_id": source.get("video_id", ""),
            "part": source.get("part", 0),
            "version": source.get("version", ""),
            "map": source.get("map", {}),
            "counts": source.get("counts", {}),
            "status": report["conversion"]["status"],
            "automation_ready": report["conversion"]["automation_ready"],
            "nodes": candidate_nodes,
            "evidence": {
                "report": "P5转换验证报告.json",
                "overview": report["candidates"]["overview"],
                "map_anchors": report["candidates"]["map_anchors"],
                "keyframes": report["candidates"]["keyframes"],
                "compass_sample_count": report["sampling"]["compass_hits"],
                "minimap_odometry": {
                    "status": report["minimap_odometry"]["status"],
                    "success_rate": report["minimap_odometry"]["success_rate"],
                    "segments": report["minimap_odometry"]["segments"],
                    "points": report["minimap_odometry"]["points"],
                    "progress_timeline":
                        report["minimap_odometry"]["progress_timeline"],
                },
                "route_hypothesis": {
                    "status": hypothesis.get("status"),
                    "total_distance": hypothesis.get("total_distance"),
                    "implied_world_units_per_pixel":
                        hypothesis.get("implied_world_units_per_pixel"),
                    "warning": hypothesis.get("warning"),
                },
            },
        }

    @staticmethod
    def _markdown(report: dict) -> str:
        video = report["video"]
        sampling = report["sampling"]
        conversion = report["conversion"]
        preflight = report.get("execution_preflight", {})
        counts = report["source_manifest"].get("counts", {})
        reasons = "\n".join(f"- {reason}" for reason in conversion["reasons"])
        preflight_errors = "\n".join(
            f"- {reason}" for reason in preflight.get("errors", []))
        readiness = "是" if conversion["automation_ready"] else "否"
        return f"""# P5 视频路线转换验证报告

## 结论

- 当前状态：`{conversion['status']}`
- 可直接用于自动移动：{readiness}
- 执行前硬校验：{'通过' if preflight.get('executable') else '未通过'}
- 下一步：{conversion['next_step']}

{reasons or '- 未发现阻塞项'}

### 执行前硬校验

{preflight_errors or '- 已通过全部结构、数量和逐点验证检查'}

## 输入核验

- 视频：{video['width']}×{video['height']}，{video['fps']:.3f} FPS，{video['duration_seconds']:.2f} 秒
- 预期内容：宝箱 {counts.get('chests', 0)}、声匣 {counts.get('sound_boxes', 0)}、观景点 {counts.get('scenic_points', 0)}、蝴蝶 {counts.get('butterflies', 0)}
- 抽样帧：{sampling['frame_samples']}
- 坐标探针：{sampling['coordinate_probes']}，有效坐标 {sampling['coordinate_hits']}（本地 OCR：{'已执行' if sampling['coordinate_ocr_enabled'] else '已跳过'}）
- 有效罗盘朝向样本：{sampling['compass_hits']}
- 小地图相对位移：{report['minimap_odometry']['successes']}/{report['minimap_odometry']['attempts']} 个有效区间，当前为像素轨迹
- 候选点位链：{len(report['route_hypothesis'].get('nodes', []))}/{report['route_hypothesis'].get('expected_nodes', 0)} 个节点（未验证）
- 视频编号进度：{len(report['minimap_odometry']['progress_timeline'].get('transitions', []))} 个状态

## 视觉候选

- 切镜/传送候选：{len(report['candidates']['scene_changes'])}
- 领取/交互候选：{len(report['candidates']['reward_or_interaction'])}
- 大地图候选：{len(report['candidates']['big_map'])}
- 通过校验的世界坐标锚点：{sum(1 for anchor in report['candidates']['map_anchors'] if anchor['success'])}
- 已导出关键帧：{len(report['candidates']['keyframes'])}

说明：领取候选和罗盘朝向是计算机视觉线索，需要与地图数据库点位完成一次配对后，才能生成可闭环纠偏的自动路线。验证器不会启动游戏、发送按键、读取或修改游戏内存。
"""
