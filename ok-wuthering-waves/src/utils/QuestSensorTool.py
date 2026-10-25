from functools import lru_cache
from pathlib import Path
import re

import cv2
import numpy as np

from src.utils.QuestOcrPrivacy import sanitize_quest_text


def requires_sensor(goal: str) -> bool:
    text = sanitize_quest_text(goal)
    if re.search(r"(?:不要|禁止|无需|无须|不必|不用|避免).{0,12}感知|\b(?:do not|don't|avoid)\b.{0,25}\bsensor\b",
                 text, re.IGNORECASE):
        return False
    return bool(re.search(
        r"(?:使用|利用|开启|启动|释放|施放|施展|发动|借助|通过|运用).{0,12}感知|"
        r"感知.{0,10}(?:探查|寻找|找出|发现|侦测|扫描)|"
        r"\b(?:use|using|activate)\b.{0,25}\bsensor\b|\bwith\s+(?:the\s+)?sensor\b",
        text, re.IGNORECASE,
    ))


@lru_cache(maxsize=1)
def _sensor_template():
    path = Path(__file__).resolve().parents[2] / "assets/quest_sensor_icon.png"
    template = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if template is None:
        raise FileNotFoundError(str(path))
    return template


def find_sensor_icon(frame: np.ndarray, search_box: tuple[int, int, int, int], *, wheel=False):
    if frame is None or frame.size == 0:
        raise ValueError("感知工具画面不能为空")
    x, y, width, height = search_box
    if min(x, y) < 0 or min(width, height) <= 0 or x + width > frame.shape[1] or y + height > frame.shape[0]:
        raise ValueError("感知工具识别区域无效")
    gray = cv2.cvtColor(frame[y:y + height, x:x + width], cv2.COLOR_BGR2GRAY)
    template = _sensor_template()
    best, result = .78, None
    sizes = (.055, .125) if wheel else (.025, .070)
    tried = set()
    for ratio in np.arange(sizes[0], sizes[1] + .001, .0025):
        th = max(8, round(frame.shape[0] * ratio))
        tw = round(th * template.shape[1] / template.shape[0])
        if th in tried or th > height or tw > width:
            continue
        tried.add(th)
        resized = cv2.resize(template, (tw, th), interpolation=cv2.INTER_AREA)
        matches = cv2.matchTemplate(gray, resized, cv2.TM_CCOEFF_NORMED)
        _, confidence, _, location = cv2.minMaxLoc(matches)
        if confidence >= best:
            best = confidence
            result = (x + location[0], y + location[1], tw, th)
    return result


def sensor_wheel_selected(frame: np.ndarray, sensor_box: tuple[int, int, int, int]) -> bool:
    height, width = frame.shape[:2]
    x, y, w, h = sensor_box
    sensor = np.array((x + w / 2, y + h / 2))
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    offset = max(4, round(height * .012))
    for dx in (-.02, 0, .02):
        for dy in (-.02, 0, .02):
            center = np.array((width * (.5 + dx), height * (.5 + dy)))
            delta = sensor - center
            distance = float(np.linalg.norm(delta))
            angle = np.arctan2(delta[1], delta[0])
            angles = np.linspace(angle - .28, angle + .28, 40)
            directions = np.array((np.cos(angles), np.sin(angles)))
            for radius in np.arange(distance * .50, distance * .85, max(.7, height / 806)):
                values = []
                for displacement in (-offset, 0, offset):
                    positions = np.rint(center[:, None] + directions * (radius + displacement)).astype(int)
                    if (np.any(positions[0] < 0) or np.any(positions[0] >= width)
                            or np.any(positions[1] < 0) or np.any(positions[1] >= height)):
                        return False
                    values.append(gray[positions[1], positions[0]].astype(float))
                selected = (values[1] >= 210) & (values[1] - np.maximum(values[0], values[2]) >= 35)
                if np.mean(selected) >= .70:
                    return True
    return False
