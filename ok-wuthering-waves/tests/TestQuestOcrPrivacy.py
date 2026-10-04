from pathlib import Path
import re
import unittest

import cv2
import numpy as np
from onnxocr.onnx_paddleocr import ONNXPaddleOcr

from src.utils.QuestDecisionEngine import build_jev_payload
from src.utils.QuestOcrPrivacy import prepare_quest_ocr_frame, sanitize_quest_text


class TestQuestOcrPrivacy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frame = cv2.imread(str(Path(__file__).parent / "images" / "quest_navigation_start.png"))
        if cls.frame is None:
            raise FileNotFoundError("任务导航截图不存在")
        cls.protected_frame = prepare_quest_ocr_frame(cls.frame)
        engine = ONNXPaddleOcr(use_angle_cls=False, use_openvino=True, use_npu=True)
        cls.original_text = "\n".join(entry[1][0] for entry in engine.ocr(cls.frame, cls=False)[0])
        cls.protected_text = "\n".join(entry[1][0] for entry in engine.ocr(cls.protected_frame, cls=False)[0])

    def test_identity_region_is_excluded_before_ocr(self):
        height, width = self.frame.shape[:2]
        self.assertTrue(np.all(self.protected_frame[int(height * 0.90):, int(width * 0.70):] == 0))
        self.assertTrue(np.array_equal(self.frame[:int(height * 0.90)], self.protected_frame[:int(height * 0.90)]))
        self.assertRegex(self.original_text, r"\d{8,}")
        self.assertNotRegex(self.protected_text, r"\d{8,}")

    def test_request_removes_identity_from_original_ocr(self):
        payload = build_jev_payload(self.frame, self.original_text, ocr_text=self.original_text)
        self.assertNotRegex(payload["state"], r"\d{8,}|特征码|特征碼")
        self.assertIn("欲知天将雨", payload["state"])
        self.assertIn("39", payload["state"])

    def test_normalized_and_separated_identity_numbers(self):
        identity = re.search(r"\d{8,}", self.original_text).group()
        fullwidth_identity = identity.translate(str.maketrans("0123456789", "０１２３４５６７８９"))
        for text in (identity, fullwidth_identity, " ".join(identity), "-".join(identity)):
            with self.subTest(text_length=len(text)):
                self.assertEqual(sanitize_quest_text(text), "")


if __name__ == "__main__":
    unittest.main()
