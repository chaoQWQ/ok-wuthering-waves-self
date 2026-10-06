import base64
import unittest
from pathlib import Path

import cv2
import numpy as np

from src.utils.QuestDecisionEngine import (
    build_detour_payload,
    encode_frame_as_data_url,
    parse_detour_response,
)


class TestQuestDetourDecision(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frame = cv2.imread(str(Path(__file__).parent / "images" / "quest_navigation_start.png"))
        if cls.frame is None:
            raise FileNotFoundError("任务区域截图不存在")

    def test_encoded_frame_is_valid_jpeg_data_url(self):
        data_url = encode_frame_as_data_url(self.frame)
        self.assertTrue(data_url.startswith("data:image/jpeg;base64,"))
        decoded = base64.b64decode(data_url.split(",", 1)[1])
        restored = cv2.imdecode(np.frombuffer(decoded, np.uint8), cv2.IMREAD_COLOR)
        self.assertIsNotNone(restored)
        scale = min(1.0, 1280 / self.frame.shape[1])
        self.assertEqual(restored.shape[1], round(self.frame.shape[1] * scale))
        self.assertEqual(restored.shape[0], round(self.frame.shape[0] * scale))

    def test_encoded_frame_downscales_extremely_wide_input(self):
        wide = cv2.resize(self.frame, (3000, self.frame.shape[0] * 3000 // self.frame.shape[1]))
        data_url = encode_frame_as_data_url(wide, max_width=1280)
        decoded = base64.b64decode(data_url.split(",", 1)[1])
        restored = cv2.imdecode(np.frombuffer(decoded, np.uint8), cv2.IMREAD_COLOR)
        self.assertLessEqual(restored.shape[1], 1280)

    def test_detour_payload_sanitizes_state_and_offers_three_directions(self):
        payload = build_detour_payload("欲知天将雨\n特征码12345678", {"stuck": True})
        self.assertEqual(set(payload["questions"]["direction"]["criteria"]), {"left", "right", "back"})
        encoded = str(payload["state"])
        self.assertNotIn("特征码", encoded)
        self.assertNotIn("12345678", encoded)
        self.assertIn("欲知天将雨", encoded)

    def test_parse_detour_response_reads_choice_answer(self):
        response = {
            "model": "clef",
            "answers": {
                "direction": {
                    "type": "choice",
                    "choice": "left",
                    "probabilities": {"left": .7, "right": .2, "back": .1},
                    "confidence": .8,
                }
            },
            "usage": {"input_tokens": 100, "output_tokens": 10},
        }
        decision = parse_detour_response(response)
        self.assertEqual(decision["direction"], "left")
        self.assertAlmostEqual(decision["confidence"], .8)
        self.assertEqual(decision["total_tokens"], 110)

    def test_parse_detour_response_unwraps_rest_result_envelope(self):
        answer = {"type": "choice", "choice": "right", "probabilities": {"right": .9, "back": .1}, "confidence": .85}
        response = {"success": True, "result": {"answers": {"direction": answer}, "usage": {"input_tokens": 5, "output_tokens": 5}}}
        self.assertEqual(parse_detour_response(response)["direction"], "right")

    def test_parse_detour_response_rejects_errors_and_unknown_directions(self):
        with self.assertRaisesRegex(RuntimeError, "auth"):
            parse_detour_response({"success": False, "errors": [{"message": "auth failed"}]})
        with self.assertRaises(ValueError):
            parse_detour_response({"answers": {"direction": {"type": "choice", "choice": "up", "probabilities": {"up": 1.0}, "confidence": 1.0}}})
        with self.assertRaises(ValueError):
            parse_detour_response({"answers": {}})

    def test_parse_detour_response_treats_yes_no_answer_as_retreat(self):
        response = {"answers": {"direction": {"type": "noul", "noul": .4}}}
        decision = parse_detour_response(response)
        self.assertEqual(decision["direction"], "back")
        self.assertEqual(decision["confidence"], 0)


if __name__ == "__main__":
    unittest.main()
