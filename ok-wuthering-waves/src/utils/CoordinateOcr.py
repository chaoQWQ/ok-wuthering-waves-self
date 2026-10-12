import hashlib
import re
import threading
from pathlib import Path

import cv2
import numpy as np
import openvino as ov


# 图像缩放与检测输出处理参考 WutheringWaves Navigator，许可见 assets/coordinate_ocr/LICENSE.txt。
class CoordinateOcr:
    CLASSES = "0123456789,:-"
    MODEL_PATH = Path(__file__).resolve().parents[2] / "assets/coordinate_ocr/coord_ocr.onnx"
    MODEL_SHA256 = "4362a8da7082be40b3f1dfce9fd72b0e3c0d1e85308b362ca225c325e543ea51"

    def __init__(self, model_path=None):
        model_path = Path(model_path) if model_path is not None else self.MODEL_PATH
        if hashlib.sha256(model_path.read_bytes()).hexdigest() != self.MODEL_SHA256:
            raise ValueError("坐标识别模型的 SHA-256 校验失败")
        core = ov.Core()
        model = core.read_model(model_path)
        if tuple(model.input().shape) != (1, 3, 64, 640) or tuple(model.output().shape) != (1, 17, 840):
            raise ValueError("坐标识别模型的输入或输出尺寸不匹配")
        self.model = core.compile_model(model, "CPU", {"PERFORMANCE_HINT": "LATENCY", "INFERENCE_NUM_THREADS": 2})
        self.request = self.model.create_infer_request()
        self.lock = threading.Lock()

    @staticmethod
    def crop_box(height, width):
        return (0, height - max(1, height // 32), min(width, round(480 * height / 1080)), height)

    def detect_characters(self, image):
        height, width = image.shape[:2]
        if height == 0 or width == 0:
            raise ValueError("坐标识别区域不能为空")
        scale = min(640 / width, 64 / height)
        resized_w, resized_h = round(width * scale), round(height * scale)
        left, top = (640 - resized_w) // 2, (64 - resized_h) // 2
        canvas = np.full((64, 640, 3), 114, dtype=np.uint8)
        canvas[top:top + resized_h, left:left + resized_w] = cv2.resize(image, (resized_w, resized_h))
        tensor = np.ascontiguousarray(canvas[..., ::-1].transpose(2, 0, 1), dtype=np.float32)[None] / 255
        with self.lock:
            prediction = self.request.infer({0: tensor})[self.model.output(0)].copy()[0].T
        classes = prediction[:, 4:].argmax(axis=1)
        scores = prediction[np.arange(len(prediction)), classes + 4]
        selected = scores >= 0.45
        prediction, classes, scores = prediction[selected], classes[selected], scores[selected]
        if not len(prediction):
            return []
        boxes = prediction[:, :4].copy()
        boxes[:, :2] -= boxes[:, 2:] / 2
        boxes[:, 0] -= left
        boxes[:, 1] -= top
        boxes /= scale
        indexes = cv2.dnn.NMSBoxes(boxes.tolist(), scores.tolist(), 0.45, 0.7)
        detections = []
        for index in np.asarray(indexes).reshape(-1):
            x, y, box_w, box_h = boxes[index]
            detections.append({"char": self.CLASSES[classes[index]], "confidence": float(scores[index]),
                               "bbox": [float(x), float(y), float(x + box_w), float(y + box_h)]})
        return sorted(detections, key=lambda item: item["bbox"][0])

    @staticmethod
    def parse_characters(detections):
        if not detections:
            return ""
        widths = [item["bbox"][2] - item["bbox"][0] for item in detections if item["char"].isdigit()]
        if not widths:
            return ""
        digit_width = float(np.median(widths))
        groups = [[]]
        # 坐标与日期之间的空白大于数字宽度，日期不参与坐标解析。
        for detection in detections:
            if groups[-1] and detection["bbox"][0] - groups[-1][-1]["bbox"][2] > digit_width:
                groups.append([])
            groups[-1].append(detection)
        numbers = []
        current = ""
        previous = None
        for item in groups[0]:
            char = item["char"]
            gap = item["bbox"][0] - previous["bbox"][2] if previous else 0.0
            if previous and char == previous["char"] and char in ",-" and gap < 0:
                continue
            if char == ",":
                if not re.fullmatch(r"-?\d{1,7}", current):
                    return ""
                numbers.append(current)
                current = ""
            elif char == "-":
                if current:
                    if not current[-1].isdigit() or gap < digit_width * 0.25:
                        return ""
                    numbers.append(current)
                current = "-"
            elif char.isdigit():
                # 逗号遗漏时使用字符间隔恢复分隔，只接受完整的三个分量。
                if current and current[-1].isdigit() and gap > digit_width * 0.25:
                    numbers.append(current)
                    current = ""
                current += char
            else:
                return ""
            previous = item
        numbers.append(current)
        if len(numbers) != 3 or not all(re.fullmatch(r"-?\d{1,7}", number) for number in numbers):
            return ""
        return ",".join(numbers)

    def recognize(self, frame, crop_box=None):
        height, width = frame.shape[:2]
        left, upper, right, lower = crop_box or self.crop_box(height, width)
        detections = self.detect_characters(frame[upper:lower, left:right])
        return self.parse_characters(detections), detections


_coordinate_ocr = None
_initialization_lock = threading.Lock()


def get_coordinate_ocr():
    global _coordinate_ocr
    with _initialization_lock:
        if _coordinate_ocr is None:
            _coordinate_ocr = CoordinateOcr()
        return _coordinate_ocr
