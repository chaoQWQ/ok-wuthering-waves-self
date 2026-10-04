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
    spatial_mask[int(frame_height * 0.20):int(frame_height * 0.45), :int(frame_width * 0.16)] = 0
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
        & (roi_b < 230)
        & ((roi_r.astype(int) - roi_b.astype(int)) > 25)
    ).astype(np.uint8)

    # 使用黄色圆环的几何形状识别任务图标。
    scale = frame_width / 1920.0
    circles = cv2.HoughCircles(
        gold_mask * 255, cv2.HOUGH_GRADIENT, 1, max(12, int(25 * scale)),
        param1=100, param2=max(7, int(16 * scale)),
        minRadius=max(4, int(10 * scale)), maxRadius=max(6, int(19 * scale)),
    )
    if circles is not None:
        candidates = []
        angles = np.linspace(0, 2 * np.pi, 64, endpoint=False)
        for cx, cy, radius in circles[0]:
            ix, iy = int(round(cx)), int(round(cy))
            if spatial_mask[iy, ix] == 0:
                continue
            coverage = np.zeros(64, dtype=bool)
            for radius_delta in (-3 * scale, -1.5 * scale, 0, 1.5 * scale, 3 * scale):
                xs = np.clip(np.rint(cx + (radius + radius_delta) * np.cos(angles)).astype(int), 0, frame_width - 1)
                ys = np.clip(np.rint(cy + (radius + radius_delta) * np.sin(angles)).astype(int), 0, frame_height - 1)
                coverage |= gold_mask[ys, xs] != 0
            confidence = float(np.mean(coverage))
            center = frame[max(0, iy - 2):iy + 3, max(0, ix - 2):ix + 3]
            if confidence >= max(threshold, 0.80) and float(np.mean(center[:, :, 1])) > 180:
                candidates.append((confidence, ix, iy, int(round(radius))))
        if candidates:
            confidence, cx, cy, radius = max(candidates)
            return BeaconResult(True, cx - radius, cy - radius, radius * 2, radius * 2, confidence)

    # 优先执行高精度模板匹配
    if os.path.exists(template_path):
        template = cv2.imread(template_path, cv2.IMREAD_GRAYSCALE)
        if template is not None:
            gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            th, tw = template.shape[:2]
            best_score = -1.0
            best_rect: Optional[Tuple[int, int, int, int]] = None
            gold_integral = cv2.integral(gold_mask)

            scale_base = frame_width / 1024.0
            for scale_factor in [0.7, 0.85, 1.0, 1.2]:
                scaled_w = max(6, int(tw * scale_base * scale_factor))
                scaled_h = max(6, int(th * scale_base * scale_factor))
                scaled_template = cv2.resize(template, (scaled_w, scaled_h), interpolation=cv2.INTER_AREA)

                res = cv2.matchTemplate(gray_frame, scaled_template, cv2.TM_CCOEFF_NORMED)
                res_h, res_w = res.shape[:2]
                res_valid_mask = spatial_mask[:res_h, :res_w]
                res[res_valid_mask == 0] = -1.0
                gold_counts = (
                    gold_integral[scaled_h:, scaled_w:]
                    - gold_integral[:-scaled_h, scaled_w:]
                    - gold_integral[scaled_h:, :-scaled_w]
                    + gold_integral[:-scaled_h, :-scaled_w]
                )
                res[gold_counts <= scaled_h * scaled_w * 0.03] = -1.0

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


def extract_beacon_distance_roi(
    frame: np.ndarray,
    beacon_result: BeaconResult
) -> Optional[np.ndarray]:
    if not beacon_result.found:
        return None
    fh, fw = frame.shape[:2]
    bx, by, bw, bh = beacon_result.x, beacon_result.y, beacon_result.width, beacon_result.height
    sy = by + bh
    ey = min(fh, by + int(bh * 2.8))
    sx = max(0, bx - bw // 2)
    ex = min(fw, bx + int(bw * 1.8))
    if ey <= sy or ex <= sx:
        return None
    return frame[sy:ey, sx:ex]


def extract_task_panel_distance_roi(
    frame: np.ndarray
) -> np.ndarray:
    if frame is None or frame.size == 0:
        raise ValueError("输入画面数组不能为空")
    fh, fw = frame.shape[:2]
    sy = int(fh * 0.18)
    ey = int(fh * 0.48)
    sx = int(fw * 0.01)
    ex = int(fw * 0.28)
    return frame[sy:ey, sx:ex]


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
        (roi_r > 175)
        & (roi_g > 155)
        & (roi_b < 230)
        & ((roi_r.astype(int) - roi_b.astype(int)) > 25)
    ).astype(np.uint8)

    center_map_x = width * 0.068 - mx if minimap_roi_box is None else mw * 0.5
    center_map_y = height * 0.115 - my if minimap_roi_box is None else mh * 0.5

    # 从玩家箭头计算当前朝向，目标方位必须转换为相对转向角度。
    player_contours, _ = cv2.findContours(gold_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    heading = None
    for contour in player_contours:
        moments = cv2.moments(contour)
        if moments["m00"] < 20:
            continue
        cx = moments["m10"] / moments["m00"]
        cy = moments["m01"] / moments["m00"]
        if np.hypot(cx - center_map_x, cy - center_map_y) > min(mw, mh) * 0.08:
            continue
        hull = cv2.convexHull(contour)
        points = cv2.approxPolyDP(hull, cv2.arcLength(hull, True) * 0.06, True)[:, 0, :].astype(float)
        if len(points) != 3:
            continue
        center = np.array([cx, cy])
        tip_angles = []
        for index, point in enumerate(points):
            before = points[index - 1] - point
            after = points[(index + 1) % len(points)] - point
            tip_angles.append(np.arccos(np.clip(np.dot(before, after) / (np.linalg.norm(before) * np.linalg.norm(after)), -1, 1)))
        tip = points[np.argmin(tip_angles)]
        heading = float(np.degrees(np.arctan2(tip[0] - cx, cy - tip[1])))
        break
    if heading is None:
        return MinimapArrowResult(False, 0.0, 0, 0)

    # 遮蔽小地图中心玩家自身黄色箭头，仅检测贴在小地图边缘的真实指引标记
    player_radius = int(min(mw, mh) * 0.11)
    cv2.circle(gold_mask, (int(center_map_x), int(center_map_y)), player_radius, 0, -1)

    contours, _ = cv2.findContours(gold_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    best_arrow: Optional[Tuple[float, int, int]] = None
    max_area = 0.0

    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < 10.0:
            continue
        x, y, w, h = cv2.boundingRect(cnt)
        if not (5 <= w <= 50 and 5 <= h <= 50):
            continue

        arrow_cx = x + w / 2.0
        arrow_cy = y + h / 2.0
        dx = arrow_cx - center_map_x
        dy = arrow_cy - center_map_y
        dist = float(np.hypot(dx, dy))

        if dist < player_radius or dist > min(mw, mh) * 0.42:
            continue

        angle_rad = np.arctan2(dx, -dy)
        angle_deg = (float(np.degrees(angle_rad)) - heading) % 360.0

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
    threshold: float = 0.70
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

    # 截取屏幕左上角区域（横向 0~28%，纵向 0~25%）
    ex = int(frame_w * 0.28)
    ey = int(frame_h * 0.25)

    roi = frame[:ey, :ex]
    gray_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

    scale_base = frame_w / 1024.0
    best_score = -1.0
    best_match = None

    for factor in [0.75, 0.85, 0.95, 1.0, 1.05, 1.15, 1.25, 1.4, 1.6]:
        scaled_w = max(5, int(tpl_w * scale_base * factor))
        scaled_h = max(5, int(tpl_h * scale_base * factor))

        if gray_roi.shape[0] < scaled_h or gray_roi.shape[1] < scaled_w:
            continue

        scaled_template = cv2.resize(template, (scaled_w, scaled_h), interpolation=cv2.INTER_AREA)
        match_result = cv2.matchTemplate(gray_roi, scaled_template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(match_result)

        if max_val > best_score:
            best_score = float(max_val)
            best_match = (max_loc[0], max_loc[1], scaled_w, scaled_h)

    if best_match and best_score >= threshold:
        bx, by, bw, bh = best_match
        return TopLeftSkipResult(
            found=True,
            x=bx,
            y=by,
            width=bw,
            height=bh,
            confidence=best_score
        )

    return TopLeftSkipResult(
        found=False,
        x=0,
        y=0,
        width=0,
        height=0,
        confidence=best_score if best_score > 0 else 0.0
    )


@dataclass
class ClimbStateResult:
    is_climbing: bool
    confidence: float
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0


def detect_climbing_state(
    frame: np.ndarray,
    template_path: Optional[str] = None,
    threshold: float = 0.75
) -> ClimbStateResult:
    if frame is None or frame.size == 0:
        raise ValueError("输入画面数组不能为空")

    if template_path is None:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(current_dir))
        template_path = os.path.join(project_root, "assets", "climb_drop_icon.png")

    if not os.path.exists(template_path):
        raise FileNotFoundError(f"脱离攀爬按键图标模板文件不存在: {template_path}")

    template = cv2.imread(template_path, cv2.IMREAD_GRAYSCALE)
    if template is None:
        raise RuntimeError(f"无法读取脱离攀爬图标模板: {template_path}")

    tpl_h, tpl_w = template.shape[:2]
    frame_h, frame_w = frame.shape[:2]

    sx = int(frame_w * 0.75)
    ex = int(frame_w * 0.96)
    sy = int(frame_h * 0.80)
    ey = int(frame_h * 0.98)

    roi = frame[sy:ey, sx:ex]
    gray_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

    scale_base = frame_w / 1024.0
    best_score = -1.0
    best_match: Optional[Tuple[int, int, int, int]] = None

    for factor in [0.8, 0.9, 1.0, 1.15, 1.3]:
        scaled_w = max(5, int(tpl_w * scale_base * factor))
        scaled_h = max(5, int(tpl_h * scale_base * factor))

        if gray_roi.shape[0] < scaled_h or gray_roi.shape[1] < scaled_w:
            continue

        scaled_template = cv2.resize(template, (scaled_w, scaled_h), interpolation=cv2.INTER_AREA)
        match_result = cv2.matchTemplate(gray_roi, scaled_template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(match_result)

        if max_val > best_score:
            best_score = float(max_val)
            best_match = (max_loc[0], max_loc[1], scaled_w, scaled_h)

    if best_match and best_score >= threshold:
        match_x, match_y, match_w, match_h = best_match
        return ClimbStateResult(
            is_climbing=True,
            confidence=best_score,
            x=sx + match_x,
            y=sy + match_y,
            width=match_w,
            height=match_h
        )

    return ClimbStateResult(
        is_climbing=False,
        confidence=best_score if best_score > 0 else 0.0,
        x=0,
        y=0,
        width=0,
        height=0
    )
