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


def resize_quest_text_frame(frame: np.ndarray) -> Tuple[np.ndarray, float]:
    if frame is None or frame.size == 0:
        raise ValueError("任务文字画面不能为空")
    height, width = frame.shape[:2]
    scale = max(1.0, 1080 / height)
    if scale == 1.0:
        return frame, scale
    return cv2.resize(frame, (round(width * scale), 1080), interpolation=cv2.INTER_CUBIC), scale


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
    # 保留角色前下方的任务信标，遮蔽底部状态栏与两侧技能区域。
    spatial_mask[int(frame_height * 0.90):, :] = 0
    spatial_mask[int(frame_height * 0.72):, :int(frame_width * 0.20)] = 0
    spatial_mask[int(frame_height * 0.72):, int(frame_width * 0.72):] = 0
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


def detect_flower_guidance(frame: np.ndarray) -> BeaconResult:
    if frame is None or frame.size == 0:
        raise ValueError("花朵指引画面不能为空")
    height, width = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (80, 70, 205), (112, 255, 255))
    mask[:int(height * 0.18)] = 0
    mask[int(height * 0.76):] = 0
    mask[:, :int(width * 0.16)] = 0
    mask[:, int(width * 0.86):] = 0
    scale = width / 1920.0
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    candidates = []
    for index in range(1, count):
        x, y, w, h, area = stats[index]
        if not (12 * scale * scale <= area <= 450 * scale * scale):
            continue
        if not (5 * scale <= w <= 45 * scale and 5 * scale <= h <= 45 * scale):
            continue
        if not 0.4 <= w / h <= 2.5:
            continue
        candidates.append((int(area), int(x), int(y), int(w), int(h)))
    if not candidates:
        return BeaconResult(False, 0, 0, 0, 0, 0.0)
    _, x, y, w, h = max(candidates)
    return BeaconResult(True, x, y, w, h, 1.0)


_DISTANCE_REGEX = re.compile(r"(\d+(?:\.\d+)?)\s*(?:米|m|M)")
_QUEST_DISTANCE_LINE = re.compile(r"\s*(\d+(?:\.\d+)?)\s*(?:米|m|M)\s*[▲△▼▽↑↓]*\s*")


def parse_distance_text(text: str, *, require_unit: bool = False) -> Optional[float]:
    if not text:
        return None
    match = _DISTANCE_REGEX.search(text)
    if match:
        return float(match.group(1))
    if not require_unit:
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
    threshold: float = 0.80,
    ocr_boxes=None,
    key_box: Optional[Tuple[int, int, int, int]] = None,
) -> InteractActionResult:
    if frame is None or frame.size == 0:
        raise ValueError("输入画面数组不能为空")

    height, width = frame.shape[:2]

    def interaction_at_key(key):
        kx, ky, kw, kh = key
        labels = [] if ocr_boxes is None else [box for box in ocr_boxes
                  if box.x >= kx + kw and box.x - kx <= width * .18
                  and abs(box.y + box.height / 2 - ky - kh / 2) <= max(kh, box.height) * .6
                  and (box.name or "").strip().upper() not in ("F", "[F]")]
        text = " ".join(box.name for box in sorted(labels, key=lambda box: box.x) if box.name)
        return InteractActionResult(True, text, key)

    if ocr_boxes is not None:
        candidates = []
        if key_box is not None:
            candidates.append(key_box)
        for box in ocr_boxes:
            if (box.name or "").strip().upper() not in ("F", "[F]", "［F］") or box.confidence < threshold:
                continue
            if not (width * .55 <= box.x <= width * .92 and height * .38 <= box.y <= height * .70):
                continue
            key_pixels = frame[box.y:box.y + box.height, box.x:box.x + box.width]
            if key_pixels.size and np.mean(np.min(key_pixels, axis=2) >= 200) >= .20:
                candidates.append((box.x, box.y, box.width, box.height))
        for kx, ky, kw, kh in candidates:
            result = interaction_at_key((kx, ky, kw, kh))
            if result.action_text:
                return result
        if candidates:
            return InteractActionResult(True, "", candidates[0])

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
            candidates = []
            for factor in (.8, 1.0, 1.05, 1.2):
                scale = width / 1920.0 * factor
                stw, sth = max(5, round(tw * scale)), max(5, round(th * scale))
                scaled_tpl = cv2.resize(template, (stw, sth), interpolation=cv2.INTER_AREA)
                if gray.shape[0] >= sth and gray.shape[1] >= stw:
                    res = cv2.matchTemplate(gray, scaled_tpl, cv2.TM_CCOEFF_NORMED)
                    _, max_val, _, max_loc = cv2.minMaxLoc(res)
                    if max_val >= threshold:
                        candidates.append((max_val, (sx + max_loc[0], sy + max_loc[1], stw, sth)))
            if candidates:
                return interaction_at_key(max(candidates, key=lambda candidate: candidate[0])[1])

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
    ey = int(frame_h * 0.99)

    roi = frame[sy:ey, sx:ex]
    gray_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

    # 依据画面分辨率自适应缩放模板（以 1024 宽度为基准尺寸进行多尺度匹配）
    base_scale = frame_w / 1024.0
    best_candidate: Optional[Tuple[int, int, int, int, float]] = None
    best_confidence: float = 0.0

    for factor in (0.9, 1.0, 1.1):
        scale = base_scale * factor
        scaled_w = max(5, round(tpl_w * scale))
        scaled_h = max(5, round(tpl_h * scale))

        if gray_roi.shape[0] < scaled_h or gray_roi.shape[1] < scaled_w:
            continue

        scaled_template = cv2.resize(template, (scaled_w, scaled_h), interpolation=cv2.INTER_LINEAR)
        match_result = cv2.matchTemplate(gray_roi, scaled_template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(match_result)

        if max_val > best_confidence:
            best_confidence = float(max_val)

        if max_val >= threshold and (best_candidate is None or max_val > best_candidate[4]):
            match_x = sx + max_loc[0]
            match_y = sy + max_loc[1]
            best_candidate = (match_x, match_y, scaled_w, scaled_h, float(max_val))

    if best_candidate is not None:
        bx, by, bw, bh, bconf = best_candidate
        return DialogAdvanceResult(
            found=True,
            x=bx,
            y=by,
            width=bw,
            height=bh,
            confidence=bconf
        )

    return DialogAdvanceResult(
        found=False,
        x=0,
        y=0,
        width=0,
        height=0,
        confidence=best_confidence
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


def detect_climbing_stamina(frame: np.ndarray) -> Optional[float]:
    if not detect_climbing_state(frame).is_climbing:
        return None
    height, width = frame.shape[:2]
    x, y = round(width * .53), round(height * .4)
    roi = frame[y:round(height * .75), x:round(width * .64)]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    circles = cv2.HoughCircles(cv2.GaussianBlur(gray, (5, 5), 0), cv2.HOUGH_GRADIENT,
                               1, max(8, height * .03), param1=80, param2=12,
                               minRadius=max(5, round(height * .014)), maxRadius=round(height * .035))
    if circles is None:
        return None
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    candidates = []
    for cx, cy, radius in circles[0]:
        angles = np.linspace(0, 2 * np.pi, 72, endpoint=False)
        filled = np.zeros(72, dtype=bool)
        for ratio in (.85, 1.0, 1.1):
            xs = np.rint(cx + np.cos(angles) * radius * ratio).astype(int)
            ys = np.rint(cy + np.sin(angles) * radius * ratio).astype(int)
            if np.any(xs < 0) or np.any(xs >= roi.shape[1]) or np.any(ys < 0) or np.any(ys >= roi.shape[0]):
                continue
            values = hsv[ys, xs]
            colored = ((values[:, 0] <= 40) | (values[:, 0] >= 170)) & (values[:, 1] >= 65) & (values[:, 2] >= 80)
            filled |= colored
        fraction = float(np.mean(filled))
        if fraction >= .08:
            candidates.append(fraction)
    return candidates[0] if len(candidates) == 1 else None


def detect_quest_vertical_hint(frame: np.ndarray, distance_box: tuple) -> str:
    x, y, width, height = distance_box
    if width <= 0 or height <= 0:
        return "unknown"
    # 箭头位于距离文字末尾或紧邻右侧。
    x0 = max(0, x + width - round(height * .7))
    roi = frame[max(0, y):min(frame.shape[0], y + height + 2), x0:min(frame.shape[1], x + width + height)]
    if roi.size == 0:
        return "unknown"
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array((15, 50, 90)), np.array((40, 255, 255)))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    hints = []
    for contour in contours:
        if cv2.contourArea(contour) < max(3, height * height * .015):
            continue
        bx, by, bw, bh = cv2.boundingRect(contour)
        if not .6 <= bw / bh <= 1.8 or not .3 <= cv2.contourArea(contour) / (bw * bh) <= .8:
            continue
        shape = mask[by:by + bh, bx:bx + bw]
        rows = np.count_nonzero(shape, axis=1)
        count = max(1, bh // 3)
        upper, lower = float(np.mean(rows[:count])), float(np.mean(rows[-count:]))
        if lower > upper * 1.8:
            hints.append((bx, "above"))
        elif upper > lower * 1.8:
            hints.append((bx, "below"))
    return max(hints)[1] if hints else "unknown"


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


@dataclass
class CompanionLabelResult:
    found: bool
    x: int
    y: int
    width: int
    height: int
    confidence: float


def detect_companion_label(
    frame: np.ndarray,
    template_path: Optional[str] = None,
    threshold: float = 0.75
) -> CompanionLabelResult:
    """识别剧情编队右侧队伍栏二号位的“同行”字样。

    剧情模式中部分任务会安排一名不可操作的 AI 同行与玩家角色同时在场，
    其头像下方标注“同行”。识别到该字样即说明当前编队只有一号位可操作，
    战斗应按单角色处理。
    """
    if frame is None or frame.size == 0:
        raise ValueError("输入画面数组不能为空")

    if template_path is None:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(current_dir))
        template_path = os.path.join(project_root, "assets", "companion_label.png")

    if not os.path.exists(template_path):
        raise FileNotFoundError(f"同行标注模板文件不存在: {template_path}")

    template = cv2.imread(template_path, cv2.IMREAD_GRAYSCALE)
    if template is None:
        raise RuntimeError(f"无法读取同行标注模板: {template_path}")

    tpl_h, tpl_w = template.shape[:2]
    frame_h, frame_w = frame.shape[:2]

    # 二号位头像标题区域（右侧队伍栏下半部分）
    sx = int(frame_w * 0.78)
    ex = int(frame_w * 0.98)
    sy = int(frame_h * 0.30)
    ey = int(frame_h * 0.44)

    roi = frame[sy:ey, sx:ex]
    gray_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

    scale_base = frame_w / 1920.0
    best_score = -1.0
    best_match = None

    for factor in (0.85, 1.0, 1.15):
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
        return CompanionLabelResult(
            found=True,
            x=sx + bx,
            y=sy + by,
            width=bw,
            height=bh,
            confidence=best_score
        )

    return CompanionLabelResult(
        found=False,
        x=0,
        y=0,
        width=0,
        height=0,
        confidence=best_score if best_score > 0 else 0.0
    )
