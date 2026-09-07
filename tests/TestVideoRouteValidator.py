import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from src.utils.VideoRouteValidator import (
    FrameEvidence,
    big_map_score,
    coordinate_crop,
    parse_coordinate_text,
    recorded_big_map_score,
    robust_threshold,
    select_peaks,
    write_image,
    VideoRouteValidator,
)


class TestVideoRouteValidator(unittest.TestCase):
    def test_parse_coordinate_text_is_conservative(self):
        self.assertEqual((-1200, 689, 20),
                         parse_coordinate_text("-1200,689,20"))
        self.assertEqual((1, 2, -3), parse_coordinate_text("1，2，-3"))
        self.assertIsNone(parse_coordinate_text("坐标 1 2 3"))
        self.assertIsNone(parse_coordinate_text("1,2"))

    def test_coordinate_crop_scales_from_1080p(self):
        frame = np.zeros((720, 1280, 3), np.uint8)
        crop = coordinate_crop(frame)
        self.assertEqual((20, 160, 3), crop.shape)

    def test_big_map_score_uses_reference_pixels(self):
        frame = np.zeros((1000, 1000, 3), np.uint8)
        checks = (
            ((0.030, 0.094), (98, 150, 166)),
            ((0.890, 0.059), (249, 249, 238)),
            ((0.939, 0.931), (255, 255, 255)),
            ((0.035, 0.891), (236, 237, 235)),
        )
        for (rx, ry), colour in checks:
            frame[round(ry * 1000), round(rx * 1000)] = colour
        self.assertEqual(1.0, big_map_score(frame))

    def test_recorded_map_heuristic_requires_missing_compass(self):
        frame = np.full((720, 1280, 3), 70, np.uint8)
        for y in range(120, 450, 30):
            cv2.line(frame, (1152, y), (1279, y), (150, 150, 150), 1)
        self.assertGreaterEqual(recorded_big_map_score(frame, 0.0), 0.75)
        self.assertLess(recorded_big_map_score(frame, 0.8), 0.75)

    def test_peak_selection_respects_gap(self):
        rows = [
            FrameEvidence(1.0, 0.9, 0, 0, 0, None, 0),
            FrameEvidence(2.0, 0.8, 0, 0, 0, None, 0),
            FrameEvidence(9.0, 0.7, 0, 0, 0, None, 0),
        ]
        selected = select_peaks(rows, "scene_score", 0.5, 5.0, 10)
        self.assertEqual([1.0, 9.0], [row.time_seconds for row in selected])

    def test_robust_threshold_ignores_single_peak(self):
        threshold = robust_threshold([0.1, 0.1, 0.11, 0.1, 0.8], 3)
        self.assertGreater(threshold, 0.1)
        self.assertLess(threshold, 0.8)

    def test_write_image_supports_unicode_path(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "关键帧.jpg"
            self.assertTrue(write_image(path, np.zeros((8, 8, 3), np.uint8)))
            self.assertTrue(path.is_file())
            self.assertIsNotNone(cv2.imdecode(
                np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR))

    def test_point_review_deduplicates_same_candidate_from_algorithms(self):
        node = {
            "order": 1, "category": "chest", "location_id": "p1",
            "name": "宝箱", "x": 1, "y": 2, "description": "路边",
            "verified": False,
        }
        report = {
            "route_hypothesis": {
                "nodes": [node],
                "affine_projection_baseline": {"nodes": [node]},
                "step_motion_baseline": {"nodes": []},
            },
            "minimap_odometry": {"progress_timeline": {"event_frames": [
                {"order": 1, "time_seconds": 10, "image": "事件/1.jpg"},
            ]}},
        }
        review = VideoRouteValidator._build_point_review(report)
        self.assertEqual(1, len(review))
        self.assertEqual(1, review[0]["candidate_count"])
        self.assertEqual(["锚定运动主候选", "全局仿射候选"],
                         review[0]["candidates"][0]["sources"])


if __name__ == "__main__":
    unittest.main()
