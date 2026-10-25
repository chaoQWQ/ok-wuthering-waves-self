from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np


@dataclass(frozen=True)
class DialogAutoPlayResult:
    found: bool
    enabled: bool
    mode: str
    x: int
    y: int
    width: int
    height: int
    confidence: float


def _white_icon(frame: np.ndarray) -> np.ndarray:
    return cv2.inRange(frame, (195, 195, 195), (255, 255, 255))


@lru_cache(maxsize=4)
def _template(name: str) -> np.ndarray:
    path = Path(__file__).resolve().parents[2] / "assets" / f"dialog_{name}.png"
    frame = cv2.imread(str(path))
    if frame is None:
        raise FileNotFoundError(str(path))
    return _white_icon(frame)


def detect_dialog_auto_play(frame: np.ndarray) -> DialogAutoPlayResult:
    if frame is None or frame.size == 0:
        raise ValueError("剧情自动对话识别画面不能为空")
    height, width = frame.shape[:2]
    scale = height / 1080
    sx = max(0, width - round(260 * scale))
    ex = min(width, width - round(175 * scale))
    sy, ey = round(18 * scale), min(height, round(115 * scale))
    region = _white_icon(cv2.resize(frame[sy:ey, sx:ex], (85, 97)))
    best = None
    for mode in ("manual", "auto"):
        template = _template(f"{mode}_center")
        scores = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
        _, confidence, _, location = cv2.minMaxLoc(scores)
        candidate = (confidence, mode, location)
        if best is None or confidence > best[0]:
            best = candidate
    if best is None or best[0] < .78:
        return DialogAutoPlayResult(False, False, "unknown", 0, 0, 0, 0,
                                    0.0 if best is None else float(best[0]))
    confidence, mode, (mx, my) = best
    x = sx + round((mx - 18) * scale)
    y = sy + round((my - 19) * scale)
    bw, bh = round(61 * scale), round(66 * scale)
    if x < 0 or y < 0 or x + bw > width or y + bh > height:
        return DialogAutoPlayResult(False, False, "unknown", 0, 0, 0, 0, float(confidence))
    button = region[my - 19:my + 47, mx - 18:mx + 43]
    if button.shape != (66, 61):
        return DialogAutoPlayResult(False, False, "unknown", 0, 0, 0, 0, float(confidence))
    # 中央符号确认按钮档位，环绕箭头确认完整自动状态。
    outer_mask = np.full((66, 61), 255, dtype=np.uint8)
    outer_mask[16:50, 14:47] = 0
    outer_scores = {}
    for name in ("manual", "auto"):
        template = _template(f"{name}_button")
        outer_scores[name] = float(cv2.matchTemplate(button, template, cv2.TM_CCORR_NORMED,
                                                    mask=outer_mask)[0, 0])
    if not np.isfinite(list(outer_scores.values())).all() or max(outer_scores.values()) < .7:
        return DialogAutoPlayResult(False, False, "unknown", 0, 0, 0, 0, float(confidence))
    if mode == "auto":
        mode = "auto" if outer_scores["auto"] >= .8 else "intermediate"
    return DialogAutoPlayResult(True, mode == "auto", mode, x, y, bw, bh, float(confidence))
