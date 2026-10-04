import re
from dataclasses import dataclass
from typing import Optional, Tuple

import cv2
import numpy as np


@dataclass
class LetterboxResult:
    is_letterbox: bool
    top_brightness: float
    bottom_brightness: float
    center_brightness: float


@dataclass
class BeaconResult:
    found: bool
    x: int
    y: int
    width: int
    height: int
    confidence: float


@dataclass
class MinimapArrowResult:
    found: bool
    bearing_deg: float
    center_x: int
    center_y: int


@dataclass
class EdgeHintResult:
    has_hint: bool
    direction: str  # 'left', 'right', 'none'


@dataclass
class InteractActionResult:
    has_f: bool
    action_text: str
    box: Optional[Tuple[int, int, int, int]]


def detect_letterbox(
    frame: np.ndarray,
    margin_ratio: float = 0.08,
    black_threshold: float = 12.0,
    center_min_brightness: float = 20.0
) -> LetterboxResult:
    if frame is None or frame.size == 0:
        raise ValueError("输入画面数组不能为空")

    height, width = frame.shape[:2]
    if height < 10 or width < 10:
        raise ValueError("输入画面分辨率过小")

    margin_height = max(1, int(height * margin_ratio))
    top_roi = frame[:margin_height, :]
    bottom_roi = frame[height - margin_height:, :]
    center_roi = frame[int(height * 0.25):int(height * 0.75), :]

    top_mean = float(np.mean(top_roi))
    bottom_mean = float(np.mean(bottom_roi))
    center_mean = float(np.mean(center_roi))

    is_letterbox = (
        top_mean <= black_threshold
        and bottom_mean <= black_threshold
        and center_mean >= center_min_brightness
    )

    return LetterboxResult(
        is_letterbox=is_letterbox,
        top_brightness=top_mean,
        bottom_brightness=bottom_mean,
        center_brightness=center_mean
    )


def detect_screen_freeze(
    frame_a: np.ndarray,
    frame_b: np.ndarray,
    diff_threshold: float = 2.0
) -> Tuple[bool, float]:
    if frame_a is None or frame_b is None:
        raise ValueError("待比对的画面帧不能为空")
    if frame_a.shape != frame_b.shape:
        raise ValueError("两帧画面的尺寸必须完全一致")

    diff = cv2.absdiff(frame_a, frame_b)
    mean_diff = float(np.mean(diff))
    is_frozen = mean_diff <= diff_threshold
    return is_frozen, mean_diff


def detect_quest_beacon(
    frame: np.ndarray,
    search_box: Optional[Tuple[int, int, int, int]] = None
) -> BeaconResult:
    if frame is None or frame.size == 0:
        raise ValueError("输入画面数组不能为空")

    frame_height, frame_width = frame.shape[:2]
    if search_box:
        sx, sy, sw, sh = search_box
        roi = frame[sy:sy + sh, sx:sx + sw]
        offset_x, offset_y = sx, sy
    else:
        # 默认搜索区域排除底部按键区与顶部信息栏
        sy = int(frame_height * 0.1)
        sh = int(frame_height * 0.8)
        sx = 0
        sw = frame_width
        roi = frame[sy:sy + sh, sx:sx + sw]
        offset_x, offset_y = sx, sy

    roi_b, roi_g, roi_r = roi[:, :, 0], roi[:, :, 1], roi[:, :, 2]
    # 金黄色判定条件：红高、绿高、蓝低，红蓝差值显著
    gold_mask = (
        (roi_r > 185)
        & (roi_g > 165)
        & (roi_b < 145)
        & ((roi_r.astype(int) - roi_b.astype(int)) > 55)
    ).astype(np.uint8)

    contours, _ = cv2.findContours(gold_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best_candidate: Optional[Tuple[int, int, int, int, float]] = None

    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        if not (6 <= w <= 65 and 6 <= h <= 65):
            continue
        aspect_ratio = float(w) / float(h)
        if not (0.55 <= aspect_ratio <= 1.8):
            continue

        # 计算内部中心亮度特征（信标中心四角星芒区域呈明亮反光）
        crop = roi[y:y + h, x:x + w]
        center_h = max(1, h // 3)
        center_w = max(1, w // 3)
        center_crop = crop[center_h:h - center_h, center_w:w - center_w]
        if center_crop.size == 0:
            continue

        center_brightness = float(np.mean(center_crop))
        if center_brightness < 120.0:
            continue

        confidence = min(1.0, (center_brightness / 255.0) * (1.0 - abs(1.0 - aspect_ratio)))
        if best_candidate is None or confidence > best_candidate[4]:
            best_candidate = (x + offset_x, y + offset_y, w, h, confidence)

    if best_candidate:
        bx, by, bw, bh, bconf = best_candidate
        return BeaconResult(
            found=True,
            x=bx,
            y=by,
            width=bw,
            height=bh,
            confidence=bconf
        )

    return BeaconResult(
        found=False,
        x=0,
        y=0,
        width=0,
        height=0,
        confidence=0.0
    )


_DISTANCE_REGEX = re.compile(r"(\d+(?:\.\d+)?)\s*(?:米|m|M)")


def parse_distance_text(text: str) -> Optional[float]:
    if not text:
        return None
    match = _DISTANCE_REGEX.search(text)
    if match:
        return float(match.group(1))
    digits_match = re.search(r"(\d+(?:\.\d+)?)", text)
    if digits_match:
        return float(digits_match.group(1))
    return None


def detect_minimap_quest_arrow(
    frame: np.ndarray,
    minimap_roi_box: Optional[Tuple[int, int, int, int]] = None
) -> MinimapArrowResult:
    if frame is None or frame.size == 0:
        raise ValueError("输入画面数组不能为空")

    height, width = frame.shape[:2]
    if minimap_roi_box:
        mx, my, mw, mh = minimap_roi_box
    else:
        # 左上角默认小地图区域
        mx = int(width * 0.01)
        my = int(height * 0.02)
        mw = int(width * 0.12)
        mh = int(height * 0.22)

    roi = frame[my:my + mh, mx:mx + mw]
    roi_b, roi_g, roi_r = roi[:, :, 0], roi[:, :, 1], roi[:, :, 2]

    # 金色箭头判定：高饱和黄色
    gold_mask = (
        (roi_r > 190)
        & (roi_g > 170)
        & (roi_b < 130)
        & ((roi_r.astype(int) - roi_b.astype(int)) > 70)
    ).astype(np.uint8)

    contours, _ = cv2.findContours(gold_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    center_map_x = mw / 2.0
    center_map_y = mh / 2.0

    best_arrow: Optional[Tuple[float, int, int]] = None
    min_dist_to_rim = 1e9

    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        if not (8 <= w <= 45 and 8 <= h <= 45):
            continue
        arrow_cx = x + w / 2.0
        arrow_cy = y + h / 2.0
        dx = arrow_cx - center_map_x
        dy = arrow_cy - center_map_y
        dist = np.hypot(dx, dy)

        # 箭头通常处于小地图边缘环带
        min_radius = min(mw, mh) * 0.25
        max_radius = max(mw, mh) * 0.65
        if not (min_radius <= dist <= max_radius):
            continue

        # 计算顺时针角度（正北上方为 0 度，正东为 90 度）
        angle_rad = np.arctan2(dx, -dy)
        angle_deg = float(np.degrees(angle_rad)) % 360.0

        if dist < min_dist_to_rim:
            min_dist_to_rim = dist
            best_arrow = (angle_deg, int(mx + arrow_cx), int(my + arrow_cy))

    if best_arrow:
        deg, ax, ay = best_arrow
        return MinimapArrowResult(
            found=True,
            bearing_deg=deg,
            center_x=ax,
            center_y=ay
        )

    return MinimapArrowResult(
        found=False,
        bearing_deg=0.0,
        center_x=0,
        center_y=0
    )


def detect_edge_turn_hint(
    frame: np.ndarray,
    beacon_result: BeaconResult
) -> EdgeHintResult:
    if not beacon_result.found:
        return EdgeHintResult(has_hint=False, direction="none")

    height, width = frame.shape[:2]
    # 若信标位于右边缘区域（横坐标超过 75%）
    if beacon_result.x > int(width * 0.75):
        return EdgeHintResult(has_hint=True, direction="right")
    # 若信标位于左边缘区域（横坐标低于 25%）
    if beacon_result.x < int(width * 0.25):
        return EdgeHintResult(has_hint=True, direction="left")

    return EdgeHintResult(has_hint=False, direction="none")


def detect_interact_action(
    frame: np.ndarray,
    search_box: Optional[Tuple[int, int, int, int]] = None
) -> InteractActionResult:
    if frame is None or frame.size == 0:
        raise ValueError("输入画面数组不能为空")

    height, width = frame.shape[:2]
    if search_box:
        sx, sy, sw, sh = search_box
    else:
        # 中心右侧交互按钮搜索区域
        sx = int(width * 0.55)
        sy = int(height * 0.42)
        sw = int(width * 0.35)
        sh = int(height * 0.25)

    roi = frame[sy:sy + sh, sx:sx + sw]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

    # 寻找白色圆角矩形按键图标（F 键外框）
    white_mask = (gray > 220).astype(np.uint8)
    contours, _ = cv2.findContours(white_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    found_f_box: Optional[Tuple[int, int, int, int]] = None
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        if 8 <= w <= 50 and 8 <= h <= 50:
            aspect = float(w) / float(h)
            if 0.65 <= aspect <= 1.5:
                found_f_box = (sx + x, sy + y, w, h)
                break

    if found_f_box:
        return InteractActionResult(
            has_f=True,
            action_text="",
            box=found_f_box
        )

    return InteractActionResult(
        has_f=False,
        action_text="",
        box=None
    )
