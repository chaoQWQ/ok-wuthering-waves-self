import numpy as np
from PIL import Image

from src.utils.CoordinateOcr import CoordinateOcr, get_coordinate_ocr


class PositionDetector:
    CROP_BOX_1080P = (0, 1047, 480, 1080)

    def __init__(self):
        self.last_position = ''
        self.last_detections = []

    def _calc_crop_box(self, frame_h, frame_w):
        return CoordinateOcr.crop_box(frame_h, frame_w)

    def detect_position(self, frame, crop_box=None):
        if frame is None:
            self.last_position = ''
            self.last_detections = []
            return ''
        if isinstance(frame, Image.Image):
            frame = np.asarray(frame.convert("RGB"))[..., ::-1]
        if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3:
            raise ValueError("坐标识别需要三通道游戏截图")
        position, detections = get_coordinate_ocr().recognize(frame, crop_box)
        self.last_position = position
        self.last_detections = detections
        return position
