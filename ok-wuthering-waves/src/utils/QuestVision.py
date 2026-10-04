import os
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
    search_box: Optional[Tuple[int, int, int, int]] = None,
    template_path: Optional[str] = None,
    threshold: float = 0.75
) -> BeaconResult:
    if frame is None or frame.size == 0:
        raise ValueError("输入画面数组不能为空")

    frame_height, frame_width = frame.shape[:2]

    if template_path is None:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(current_dir))
        template_path = os.path.join(project_root, "assets", "quest_beacon_icon.png")

    # 构建空间遮罩排除固定界面区域
    spatial_mask = np.ones((frame_height, frame_width), dtype=np.uint8) * 255
    # 排除顶部信息栏
    spatial_mask[:int(frame_height * 0.08), :] = 0
    # 排除底部技能按键区域
    spatial_mask[int(frame_height * 0.80):, :] = 0
    # 排除左上角小地图区域
    spatial_mask[:int(frame_height * 0.25), :int(frame_width * 0.16)] = 0
    # 排除左侧固定任务描述栏
    spatial_mask[int(frame_height * 0.20):int(frame_height * 0.45), :int(frame_width * 0.25)] = 0
    # 排除右侧队伍状态栏
    spatial_mask[:int(frame_height * 0.50), int(frame_width * 0.88):] = 0

    if search_box:
        sx, sy, sw, sh = search_box
        box_mask = np.zeros((frame_height, frame_width), dtype=np.uint8)
        box_mask[sy:sy + sh, sx:sx + sw] = 255
        spatial_mask = cv2.bitwise_and(spatial_mask, box_mask)

    # 提取金黄色色彩掩码
    roi_b, roi_g, roi_r = frame[:, :, 0], frame[:, :, 1], frame[:, :, 2]
    gold_mask = (
        (roi_r > 175)
        & (roi_g > 155)
        & (roi_b < 155)
        & ((roi_r.astype(int) - roi_b.astype(int)) > 45)
    ).astype(np.uint8)

    # 优先执行高精度模板匹配
    if os.path.exists(template_path):
        template = cv2.imread(template_path, cv2.IMREAD_GRAYSCALE)
        if template is not None:
            gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            th, tw = template.shape[:2]
            best_score = -1.0
            best_rect: Optional[Tuple[int, int, int, int]] = None

            scale_base = frame_width / 1024.0
            for scale_factor in [0.7, 0.85, 1.0, 1.2]:
                scaled_w = max(6, int(tw * scale_base * scale_factor))
                scaled_h = max(6, int(th * scale_base * scale_factor))
                scaled_template = cv2.resize(template, (scaled_w, scaled_h), interpolation=cv2.INTER_AREA)

                res = cv2.matchTemplate(gray_frame, scaled_template, cv2.TM_CCOEFF_NORMED)
                res_h, res_w = res.shape[:2]
                res_valid_mask = spatial_mask[:res_h, :res_w]
                res[res_valid_mask == 0] = -1.0

                min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
                if max_val > best_score:
                    bx, by = max_loc
                    patch_gold = gold_mask[by:by + scaled_h, bx:bx + scaled_w]
                    gold_ratio = float(np.mean(patch_gold)) if patch_gold.size > 0 else 0.0
                    if gold_ratio > 0.03:
                        best_score = float(max_val)
                        best_rect = (bx, by, scaled_w, scaled_h)

            if best_rect and best_score >= threshold:
                bx, by, bw, bh = best_rect
                return BeaconResult(
                    found=True,
                    x=bx,
                    y=by,
                    width=bw,
                    height=bh,
                    confidence=best_score
                )

    # 备用方案：几何连通域筛选
    masked_gold = cv2.bitwise_and(gold_mask, gold_mask, mask=spatial_mask)
    contours, _ = cv2.findContours(masked_gold, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best_candidate: Optional[Tuple[int, int, int, int, float]] = None

    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        if not (6 <= w <= 65 and 6 <= h <= 65):
            continue
        aspect_ratio = float(w) / float(h)
        if not (0.55 <= aspect_ratio <= 1.8):
            continue

        crop = frame[y:y + h, x:x + w]
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
            best_candidate = (x, y, w, h, confidence)

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
        mw = int(width * 0.13)
        mh = int(height * 0.23)

    roi = frame[my:my + mh, mx:mx + mw]
    roi_b, roi_g, roi_r = roi[:, :, 0], roi[:, :, 1], roi[:, :, 2]

    # 金黄色判定条件：红高、绿高、蓝低，色彩差值显著
    gold_mask = (
        (roi_r > 185)
        & (roi_g > 165)
        & (roi_b < 140)
        & ((roi_r.astype(int) - roi_b.astype(int)) > 55)
    ).astype(np.uint8)

    contours, _ = cv2.findContours(gold_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    center_map_x = mw * 0.48
    center_map_y = mh * 0.44

    best_arrow: Optional[Tuple[float, int, int]] = None
    max_area = 0.0

    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < 15.0:
            continue
        x, y, w, h = cv2.boundingRect(cnt)
        if not (6 <= w <= 50 and 6 <= h <= 50):
            continue

        arrow_cx = x + w / 2.0
        arrow_cy = y + h / 2.0
        dx = arrow_cx - center_map_x
        dy = arrow_cy - center_map_y
        dist = float(np.hypot(dx, dy))

        # 计算顺时针角度（正北上方为 0 度，正东为 90 度）
        if dist >= 10.0:
            angle_rad = np.arctan2(dx, -dy)
            angle_deg = float(np.degrees(angle_rad)) % 360.0
        else:
            angle_deg = 0.0

        if area > max_area:
            max_area = area
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
    search_box: Optional[Tuple[int, int, int, int]] = None,
    template_path: Optional[str] = None,
    threshold: float = 0.85
) -> InteractActionResult:
    if frame is None or frame.size == 0:
        raise ValueError("输入画面数组不能为空")

    height, width = frame.shape[:2]
    if search_box:
        sx, sy, sw, sh = search_box
    else:
        # 中心右侧交互按钮搜索区域
        sx = int(width * 0.55)
        sy = int(height * 0.38)
        sw = int(width * 0.35)
        sh = int(height * 0.32)

    roi = frame[sy:sy + sh, sx:sx + sw]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

    if template_path is None:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(current_dir))
        template_path = os.path.join(project_root, "assets", "interact_f_icon.png")

    if os.path.exists(template_path):
        template = cv2.imread(template_path, cv2.IMREAD_GRAYSCALE)
        if template is not None:
            th, tw = template.shape[:2]
            scale = width / 1024.0
            stw = max(5, int(tw * scale))
            sth = max(5, int(th * scale))
            scaled_tpl = cv2.resize(template, (stw, sth), interpolation=cv2.INTER_AREA)

            if gray.shape[0] >= sth and gray.shape[1] >= stw:
                res = cv2.matchTemplate(gray, scaled_tpl, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, max_loc = cv2.minMaxLoc(res)
                if max_val >= threshold:
                    found_f_box = (sx + max_loc[0], sy + max_loc[1], stw, sth)
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


@dataclass
class DialogAdvanceResult:
    found: bool
    x: int
    y: int
    width: int
    height: int
    confidence: float


def detect_dialog_advance_indicator(
    frame: np.ndarray,
    template_path: Optional[str] = None,
    threshold: float = 0.75
) -> DialogAdvanceResult:
    if frame is None or frame.size == 0:
        raise ValueError("输入画面数组不能为空")

    if template_path is None:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(current_dir))
        template_path = os.path.join(project_root, "assets", "dialog_advance_icon.png")

    if not os.path.exists(template_path):
        raise FileNotFoundError(f"推进图标模板文件不存在: {template_path}")

    template = cv2.imread(template_path, cv2.IMREAD_GRAYSCALE)
    if template is None:
        raise RuntimeError(f"无法读取推进图标模板: {template_path}")

    tpl_h, tpl_w = template.shape[:2]
    frame_h, frame_w = frame.shape[:2]

    # 截取屏幕底部中央的候选区域
    sx = int(frame_w * 0.40)
    ex = int(frame_w * 0.60)
    sy = int(frame_h * 0.70)
    ey = int(frame_h * 0.95)

    roi = frame[sy:ey, sx:ex]
    gray_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

    # 依据画面分辨率自适应缩放模板（以 1024 宽度为基准尺寸）
    scale = frame_w / 1024.0
    scaled_w = max(5, int(tpl_w * scale))
    scaled_h = max(5, int(tpl_h * scale))

    scaled_template = cv2.resize(template, (scaled_w, scaled_h), interpolation=cv2.INTER_AREA)

    if gray_roi.shape[0] < scaled_h or gray_roi.shape[1] < scaled_w:
        return DialogAdvanceResult(
            found=False,
            x=0,
            y=0,
            width=0,
            height=0,
            confidence=0.0
        )

    match_result = cv2.matchTemplate(gray_roi, scaled_template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(match_result)

    if max_val >= threshold:
        match_x = sx + max_loc[0]
        match_y = sy + max_loc[1]
        return DialogAdvanceResult(
            found=True,
            x=match_x,
            y=match_y,
            width=scaled_w,
            height=scaled_h,
            confidence=float(max_val)
        )

    return DialogAdvanceResult(
        found=False,
        x=0,
        y=0,
        width=0,
        height=0,
        confidence=float(max_val)
    )


@dataclass
class TopLeftSkipResult:
    found: bool
    x: int
    y: int
    width: int
    height: int
    confidence: float


def detect_top_left_skip_button(
    frame: np.ndarray,
    template_path: Optional[str] = None,
    threshold: float = 0.75
) -> TopLeftSkipResult:
    if frame is None or frame.size == 0:
        raise ValueError("输入画面数组不能为空")

    if template_path is None:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(current_dir))
        template_path = os.path.join(project_root, "assets", "skip_dialog_hex.png")

    if not os.path.exists(template_path):
        raise FileNotFoundError(f"左上角跳过按钮模板文件不存在: {template_path}")

    template = cv2.imread(template_path, cv2.IMREAD_GRAYSCALE)
    if template is None:
        raise RuntimeError(f"无法读取跳过按钮模板: {template_path}")

    tpl_h, tpl_w = template.shape[:2]
    frame_h, frame_w = frame.shape[:2]

    # 截取屏幕左上角区域（横向 0~20%，纵向 0~20%）
    ex = int(frame_w * 0.20)
    ey = int(frame_h * 0.20)

    roi = frame[:ey, :ex]
    gray_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

    scale = frame_w / 1024.0
    scaled_w = max(5, int(tpl_w * scale))
    scaled_h = max(5, int(tpl_h * scale))

    scaled_template = cv2.resize(template, (scaled_w, scaled_h), interpolation=cv2.INTER_AREA)

    if gray_roi.shape[0] < scaled_h or gray_roi.shape[1] < scaled_w:
        return TopLeftSkipResult(
            found=False,
            x=0,
            y=0,
            width=0,
            height=0,
            confidence=0.0
        )

    match_result = cv2.matchTemplate(gray_roi, scaled_template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(match_result)

    if max_val >= threshold:
        return TopLeftSkipResult(
            found=True,
            x=max_loc[0],
            y=max_loc[1],
            width=scaled_w,
            height=scaled_h,
            confidence=float(max_val)
        )

    return TopLeftSkipResult(
        found=False,
        x=0,
        y=0,
        width=0,
        height=0,
        confidence=float(max_val)
    )
