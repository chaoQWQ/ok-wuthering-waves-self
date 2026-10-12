import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from src.utils.CoordinateOcr import CoordinateOcr, get_coordinate_ocr
from src.utils.positionDetector import PositionDetector


APP_ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((APP_ROOT / "tests/data/coordinate_ocr_additional_screenshots.json").read_text(encoding="utf-8"))


def read_frame(file):
    frame = cv2.imdecode(np.fromfile(APP_ROOT / file, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert frame is not None
    return frame


@pytest.mark.parametrize("case", CASES, ids=[Path(case["file"]).stem for case in CASES])
def test_real_screenshot_coordinate(case):
    assert PositionDetector().detect_position(read_frame(case["file"])) == (case["expected"] or "")


def test_real_screenshot_pil_and_explicit_region():
    frame = read_frame("tests/images/mini_map.png")
    detector = PositionDetector()
    expected = "-1175,2126,7"
    assert detector.detect_position(frame) == expected
    assert detector.detect_position(Image.fromarray(frame[..., ::-1])) == expected
    assert detector.detect_position(frame, detector._calc_crop_box(*frame.shape[:2])) == expected
    assert detector.last_detections
    assert detector.last_position == expected


def test_concurrent_real_screenshots_share_model():
    frames = [(case, read_frame(case["file"])) for case in CASES]

    def recognize(entry):
        case, frame = entry
        return id(get_coordinate_ocr()), PositionDetector().detect_position(frame), case["expected"] or ""

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(recognize, frames * 3))
    assert len({model_id for model_id, _, _ in results}) == 1
    assert all(position == expected for _, position, expected in results)


def test_no_frame_clears_observation():
    detector = PositionDetector()
    detector.detect_position(read_frame("tests/images/mini_map.png"))
    assert detector.detect_position(None) == ""
    assert detector.last_position == ""
    assert detector.last_detections == []


def test_missing_model_fails_at_load(tmp_path):
    with pytest.raises(FileNotFoundError):
        CoordinateOcr(tmp_path / "missing.onnx")
