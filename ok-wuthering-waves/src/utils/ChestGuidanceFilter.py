"""Stable player-position samples for chest direction guidance.

Coordinate OCR occasionally emits a plausible but completely wrong position
for several frames. The task-level denoiser must allow real teleports, whereas
chest guidance should prefer keeping the last reliable target vector until a
new map position is confirmed. This filter is therefore deliberately scoped to
the visual chest cue and never affects game input or the map-matching pipeline.
"""

from __future__ import annotations

import math
import statistics
import time
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class GuidanceSample:
    player_x: float
    player_y: float
    bearing: float
    distance: float
    nearby: bool
    rejected: bool = False


class ChestGuidanceFilter:
    """Reject impossible jumps and smooth normal chest-guidance movement."""

    MAX_STEP_GAME_UNITS = 4000.0
    REBASE_CLUSTER_RADIUS = 4000.0
    REBASE_CONFIRM_SAMPLES = 5
    NEAR_SMOOTH_DISTANCE = 3000.0
    NEARBY_DISTANCE = 800.0
    NEARBY_EXIT_DISTANCE = 1100.0
    STATIONARY_DEADBAND = 300.0
    MOVEMENT_SETTLE_SECONDS = 1.2

    def __init__(self, history_size=5):
        self._history = deque(maxlen=max(3, int(history_size)))
        self._target_key = None
        self._stable = None
        self._stable_bearing = None
        self._pending = deque(maxlen=self.REBASE_CONFIRM_SAMPLES)
        self._last_movement_at = None
        self._nearby = False

    def reset(self):
        self._history.clear()
        self._target_key = None
        self._stable = None
        self._stable_bearing = None
        self._pending.clear()
        self._last_movement_at = None
        self._nearby = False

    @staticmethod
    def _bearing_degrees(player_x, player_y, target_x, target_y):
        dx = float(target_x) - float(player_x)
        dy = float(target_y) - float(player_y)
        return math.degrees(math.atan2(dx, -dy)) % 360.0

    @staticmethod
    def _circular_lerp(previous, current, amount):
        delta = (float(current) - float(previous) + 180.0) % 360.0 - 180.0
        return (float(previous) + delta * float(amount)) % 360.0

    def _sample(self, target_x, target_y, rejected=False, advance=True):
        px, py = self._stable
        distance = math.hypot(float(target_x) - px, float(target_y) - py)
        raw_bearing = self._bearing_degrees(px, py, target_x, target_y)
        bearing_alpha = 0.18 if distance < self.NEAR_SMOOTH_DISTANCE else 0.42
        if self._stable_bearing is None:
            self._stable_bearing = raw_bearing
        elif advance:
            self._stable_bearing = self._circular_lerp(
                self._stable_bearing, raw_bearing, bearing_alpha
            )
        # 使用不同的进入和退出距离，防止边界附近反复显示和隐藏箭头。
        limit = self.NEARBY_EXIT_DISTANCE if self._nearby else self.NEARBY_DISTANCE
        self._nearby = distance <= limit
        return GuidanceSample(
            px, py, self._stable_bearing, distance,
            self._nearby, rejected,
        )

    def update(self, player_pos_ocr, target_key, target_x, target_y,
               map_confirmed=False, movement_active=True, now=None):
        now = time.monotonic() if now is None else float(now)
        raw = (float(player_pos_ocr[0]) * 100.0,
               float(player_pos_ocr[1]) * 100.0)
        if target_key != self._target_key:
            self.reset()
            self._target_key = target_key

        if self._stable is None:
            self._stable = raw
            self._history.append(raw)
            if movement_active:
                self._last_movement_at = now
            return self._sample(target_x, target_y)

        jump = math.hypot(raw[0] - self._stable[0], raw[1] - self._stable[1])
        if movement_active:
            self._last_movement_at = now
        movement_recent = bool(movement_active) or (
            self._last_movement_at is not None
            and now - self._last_movement_at <= self.MOVEMENT_SETTLE_SECONDS
        )
        if not movement_recent:
            if jump <= self.STATIONARY_DEADBAND:
                # Do not let sub-pixel OCR noise rotate the arrow when the
                # player is stationary. The stable coordinate remains exactly
                # fixed until movement input resumes.
                self._pending.clear()
                return self._sample(target_x, target_y, advance=False)
            if jump <= self.MAX_STEP_GAME_UNITS:
                # A moderate coordinate change without movement input is OCR
                # drift, not traversal. Confirmed teleports are necessarily a
                # larger jump and are handled by the cluster branch below.
                self._pending.clear()
                return self._sample(target_x, target_y, rejected=True, advance=False)
            # Keep collecting a possible confirmed teleport cluster below, but
            # ordinary stationary OCR drift must not move the stable position.
            self._pending.append(raw)
            if not (map_confirmed and
                    len(self._pending) >= self.REBASE_CONFIRM_SAMPLES):
                return self._sample(target_x, target_y, rejected=True, advance=False)

        if jump > self.MAX_STEP_GAME_UNITS:
            if movement_recent:
                self._pending.append(raw)
            # A real same-map teleport may be adopted, but only after a tight
            # repeated cluster *and* a positive map lock. Short-lived OCR
            # hallucinations remain rejected and cannot swing the arrow.
            if map_confirmed and len(self._pending) >= self.REBASE_CONFIRM_SAMPLES:
                xs = [point[0] for point in self._pending]
                ys = [point[1] for point in self._pending]
                center = (statistics.median(xs), statistics.median(ys))
                spread = max(math.hypot(x - center[0], y - center[1])
                             for x, y in self._pending)
                if spread <= self.REBASE_CLUSTER_RADIUS:
                    self._stable = center
                    self._history.clear()
                    self._history.append(center)
                    self._pending.clear()
                    self._stable_bearing = None
                    return self._sample(target_x, target_y)
            return self._sample(target_x, target_y, rejected=True, advance=False)

        self._pending.clear()
        self._history.append(raw)
        median = (
            statistics.median(point[0] for point in self._history),
            statistics.median(point[1] for point in self._history),
        )
        current_distance = math.hypot(
            float(target_x) - self._stable[0],
            float(target_y) - self._stable[1],
        )
        position_alpha = 0.24 if current_distance < self.NEAR_SMOOTH_DISTANCE else 0.48
        self._stable = (
            self._stable[0] + (median[0] - self._stable[0]) * position_alpha,
            self._stable[1] + (median[1] - self._stable[1]) * position_alpha,
        )
        return self._sample(target_x, target_y)
