from pathlib import Path
import re
import unittest

import cv2

from ok import Box
from onnxocr.onnx_paddleocr import ONNXPaddleOcr
from src.utils.QuestAreaSearch import minimap_visible
from src.utils.QuestOcrPrivacy import sanitize_quest_text
from src.utils.QuestVision import (
    detect_dialog_controls,
    detect_page_close_button,
    detect_top_left_skip_button,
)


class TestQuestReadingPageClose(unittest.TestCase):
    directory = Path(__file__).resolve().parent / "images"

    @classmethod
    def setUpClass(cls):
        cls.ocr_engine = ONNXPaddleOcr(use_angle_cls=False, use_openvino=True, use_npu=True)

    def read_image(self, path):
        frame = cv2.imread(str(path))
        self.assertIsNotNone(frame, str(path))
        return frame

    def test_detect_dialog_controls_on_all_dialogue_screens_and_resolutions(self):
        images = [
            "dialogue_auto.png",
            "dialogue_hidden.png",
            "dialogue_manual.png",
            "quest_dialog_advance.png",
        ]
        for name in images:
            original = self.read_image(self.directory / name)
            for width in (1024, 1280, 1536, 1920):
                with self.subTest(image=name, width=width):
                    height = round(original.shape[0] * width / original.shape[1])
                    frame = cv2.resize(original, (width, height))
                    self.assertTrue(detect_dialog_controls(frame))

    def test_detect_dialog_controls_false_on_non_dialogue_screens(self):
        non_dialogue_images = [
            "reading_memory_page.png",
            "reading_resolution_page.png",
            "quest_tool_blue_world.png",
            "quest_map_detail.png",
        ]
        for name in non_dialogue_images:
            with self.subTest(image=name):
                frame = self.read_image(self.directory / name)
                self.assertFalse(detect_dialog_controls(frame))

    def test_detect_page_close_button_on_reading_pages_at_multiple_resolutions(self):
        reading_images = [
            "reading_memory_page.png",
            "reading_resolution_page.png",
        ]
        for name in reading_images:
            original = self.read_image(self.directory / name)
            for width in (1024, 1280, 1536, 1920):
                with self.subTest(image=name, width=width):
                    height = round(original.shape[0] * width / original.shape[1])
                    frame = cv2.resize(original, (width, height))
                    close = detect_page_close_button(frame)
                    self.assertIsNotNone(close)
                    x, y, w, h = close
                    self.assertGreater(x / width, 0.88)
                    self.assertLess(y / height, 0.10)

    def test_detect_page_close_button_not_found_on_dialogue_and_world(self):
        safe_images = [
            "dialogue_auto.png",
            "dialogue_hidden.png",
            "dialogue_manual.png",
            "quest_dialog_advance.png",
            "quest_tool_blue_world.png",
        ]
        for name in safe_images:
            with self.subTest(image=name):
                frame = self.read_image(self.directory / name)
                self.assertIsNone(detect_page_close_button(frame))

    def test_reading_page_ocr_and_button_verification(self):
        reading_images = [
            "reading_memory_page.png",
            "reading_resolution_page.png",
        ]
        for name in reading_images:
            with self.subTest(image=name):
                frame = self.read_image(self.directory / name)
                self.assertFalse(minimap_visible(frame))
                self.assertFalse(detect_dialog_controls(frame))
                self.assertFalse(detect_top_left_skip_button(frame).found)

                close = detect_page_close_button(frame)
                self.assertIsNotNone(close)

                height, width = frame.shape[:2]
                title_region = Box(round(width * .18), round(height * .05), round(width * .64), round(height * .13))
                crop_title = frame[title_region.y:title_region.y + title_region.height,
                                   title_region.x:title_region.x + title_region.width]
                title_res = self.ocr_engine.ocr(crop_title, cls=False)[0]
                title_text = "".join(sanitize_quest_text(entry[1][0]) for entry in title_res) if title_res else ""
                self.assertRegex(title_text,
                                 r"记录|記錄|片段|回放|决议|決議|文书|文書|日志|日誌|档案|檔案|信件|笔记|筆記|"
                                 r"replay|record|resolution|document|journal|archive|letter|note")

                body_region = Box(round(width * .22), round(height * .19), round(width * .56), round(height * .64))
                crop_body = frame[body_region.y:body_region.y + body_region.height,
                                  body_region.x:body_region.x + body_region.width]
                body_res = self.ocr_engine.ocr(crop_body, cls=False)[0]
                long_lines = sum(len(sanitize_quest_text(entry[1][0])) >= 8 for entry in body_res) if body_res else 0
                self.assertGreaterEqual(long_lines, 3)

    def test_dialogue_screens_never_permit_page_close(self):
        dialogue_images = [
            "dialogue_auto.png",
            "dialogue_hidden.png",
            "dialogue_manual.png",
            "quest_dialog_advance.png",
        ]
        for name in dialogue_images:
            with self.subTest(image=name):
                frame = self.read_image(self.directory / name)
                # 处于对话界面时，对话控制按钮必定识别成功，从而完全阻止关闭操作。
                self.assertTrue(detect_dialog_controls(frame))
                self.assertIsNone(detect_page_close_button(frame))


if __name__ == "__main__":
    unittest.main(verbosity=2)
