import unittest

import cv2
import numpy as np

from src.match_engine.common import CoordsRef
from src.utils.VideoMapCalibrator import (
    detect_map_cursor,
    estimate_minimap_motion,
    MapFeatureLocator,
    RecordedMinimapTracker,
    RecordedMinimapMapLocator,
    P5_COLLECTION_SEQUENCE,
    decode_monotonic_progress,
    decode_linear_progress,
    infer_motion_guided_collection_route,
    summarise_motion_intervals,
    fit_motion_affine,
    infer_affine_anchored_collection_route,
    project_point,
)


class TestVideoMapCalibrator(unittest.TestCase):
    def test_world_map_locator_keeps_nearby_collection_query(self):
        self.assertTrue(hasattr(MapFeatureLocator, "nearby_collections"))
        self.assertFalse(hasattr(RecordedMinimapMapLocator,
                                 "nearby_collections"))

    def test_p5_sequence_matches_manifest_counts(self):
        self.assertEqual(22, len(P5_COLLECTION_SEQUENCE))
        self.assertEqual(17, P5_COLLECTION_SEQUENCE.count("chest"))
        self.assertEqual(1, P5_COLLECTION_SEQUENCE.count("sound_box"))
        self.assertEqual(1, P5_COLLECTION_SEQUENCE.count("scenic_point"))
        self.assertEqual(3, P5_COLLECTION_SEQUENCE.count("butterfly"))

    def test_exact_timeline_type_filters_motion_candidate(self):
        intervals = [{
            "dx_pixels": 1, "dy_pixels": 0, "path_pixels": 1,
            "straightness": 1, "reliable_direction": False,
        }]
        result = infer_motion_guided_collection_route(
            "assets/stitched", 8, (-121716, 70415), intervals,
            sequence=("chest",), type_sequence=("qzx_02",))
        self.assertEqual("qzx_02", result["nodes"][0]["type_id"])

    def test_detect_map_cursor_selects_central_yellow_component(self):
        frame = np.zeros((720, 1280, 3), np.uint8)
        yellow = (0, 230, 255)
        cv2.fillConvexPoly(
            frame,
            np.asarray([[640, 330], [620, 390], [660, 380]], np.int32),
            yellow,
        )
        cv2.rectangle(frame, (40, 40), (70, 70), yellow, -1)
        cursor = detect_map_cursor(frame)
        self.assertIsNotNone(cursor)
        self.assertAlmostEqual(640, cursor[0], delta=12)
        self.assertAlmostEqual(367, cursor[1], delta=12)

    def test_project_point_converts_homography_to_game_units(self):
        homography = np.asarray([
            [2.0, 0.0, 10.0],
            [0.0, 3.0, 20.0],
            [0.0, 0.0, 1.0],
        ])
        coords = CoordsRef(offset=(100.0, -50.0), scale=(10.0, 20.0))
        map_point, game_point = project_point(homography, (5, 7), coords)
        self.assertEqual((20.0, 41.0), map_point)
        self.assertEqual((300.0, 770.0), game_point)

    def test_minimap_motion_recovers_background_translation(self):
        rng = np.random.default_rng(12)
        previous = np.zeros((720, 1280, 3), np.uint8)
        texture = rng.integers(0, 256, size=(196, 215, 3), dtype=np.uint8)
        previous[20:216, 15:230] = texture
        current = previous.copy()
        patch = previous[20:216, 15:230]
        transform = np.float32([[1, 0, 5], [0, 1, -3]])
        current[20:216, 15:230] = cv2.warpAffine(
            patch, transform, (215, 196), borderMode=cv2.BORDER_REFLECT)
        motion = estimate_minimap_motion(previous, current)
        self.assertTrue(motion.success)
        self.assertAlmostEqual(5, motion.background_dx, delta=1.0)
        self.assertAlmostEqual(-3, motion.background_dy, delta=1.0)

    def test_tracker_reuses_previous_frame(self):
        frame = np.zeros((720, 1280, 3), np.uint8)
        rng = np.random.default_rng(3)
        frame[20:216, 15:230] = rng.integers(
            0, 256, size=(196, 215, 3), dtype=np.uint8)
        tracker = RecordedMinimapTracker()
        self.assertIsNone(tracker.update(frame))
        motion = tracker.update(frame.copy())
        self.assertTrue(motion.success)
        self.assertAlmostEqual(0, motion.player_dx_pixels, delta=0.2)

    def test_recorded_minimap_map_prepare_masks_non_map_hud(self):
        frame = np.full((720, 1280, 3), 255, np.uint8)
        gray, centre = RecordedMinimapMapLocator.prepare(frame)
        self.assertEqual((250, 250), gray.shape)
        self.assertEqual(0, int(gray[0, 0]))
        self.assertAlmostEqual(117.5, centre[0], delta=0.1)
        self.assertAlmostEqual(117.5, centre[1], delta=0.1)

    def test_candidate_region_is_centered_in_map_pixels(self):
        locator = object.__new__(RecordedMinimapMapLocator)
        class FakeFeatureLocator:
            class FakeCoords:
                @staticmethod
                def game_to_pixel(x, y):
                    return x / 10, y / 20
            coords = FakeCoords()
        locator.feature_locator = FakeFeatureLocator()
        seen = {}
        def fake_locate(frame, seconds, region=None):
            seen["region"] = region
            return None
        locator.locate = fake_locate
        locator.locate_near(np.zeros((1, 1, 3), np.uint8), 3,
                            (1000, 2000), region_size=400)
        self.assertEqual((-100, -100, 400, 400), seen["region"])

    def test_progress_decoder_preserves_numbered_order(self):
        times = list(range(24))
        scores = np.full((24, 4), 0.1)
        scores[0:6, 0] = 0.8
        scores[6:12, 1] = 0.8
        scores[12:18, 2] = 0.8
        scores[18:24, 3] = 0.8
        decoded = decode_monotonic_progress(times, scores)
        self.assertEqual("decoded", decoded["status"])
        self.assertEqual([1, 2, 3, 4],
                         [item["order"] for item in decoded["transitions"]])

    def test_linear_progress_decodes_marker_timestamps(self):
        times = np.arange(20, 561, 10, dtype=float)
        pointer_x = 2.25 * times - 5.5
        scores = np.full_like(times, 0.8)
        # Repeating HUD elements create convincing but non-linear outliers.
        pointer_x[::5] = 640
        decoded = decode_linear_progress(
            times, pointer_x, scores, frame_width=1280)
        self.assertEqual("decoded", decoded["status"])
        self.assertEqual(22, len(decoded["transitions"]))
        self.assertAlmostEqual(30, decoded["transitions"][0]["time_seconds"],
                               delta=1.0)
        self.assertAlmostEqual(564, decoded["transitions"][-1]["time_seconds"],
                               delta=2.0)

    def test_motion_intervals_follow_progress_boundaries(self):
        points = [
            {"time_seconds": 2, "segment": 1, "dx_pixels": 2, "dy_pixels": 0},
            {"time_seconds": 3, "segment": 1, "dx_pixels": 3, "dy_pixels": 0},
            {"time_seconds": 7, "segment": 1, "dx_pixels": 0, "dy_pixels": 4},
            {"time_seconds": 8, "segment": 1, "dx_pixels": 0, "dy_pixels": 3},
        ]
        transitions = [
            {"order": 1, "category": "chest", "time_seconds": 4},
            {"order": 2, "category": "chest", "time_seconds": 9},
        ]
        intervals = summarise_motion_intervals(points, transitions, 1)
        self.assertEqual(5, intervals[0]["dx_pixels"])
        self.assertEqual(7, intervals[1]["dy_pixels"])

    def test_motion_interval_crossing_tracker_reset_is_not_directional(self):
        points = [
            {"time_seconds": 2, "segment": 1, "dx_pixels": 3,
             "dy_pixels": 0},
            {"time_seconds": 3, "segment": 1, "dx_pixels": 3,
             "dy_pixels": 0},
            {"time_seconds": 4, "segment": 2, "dx_pixels": 3,
             "dy_pixels": 0},
            {"time_seconds": 5, "segment": 2, "dx_pixels": 3,
             "dy_pixels": 0},
        ]
        transitions = [
            {"order": 1, "category": "chest", "time_seconds": 6},
        ]
        interval = summarise_motion_intervals(points, transitions, 1)[0]
        self.assertEqual(2, interval["segment_count"])
        self.assertFalse(interval["reliable_direction"])

    def test_motion_route_can_lock_a_semantic_anchor(self):
        intervals = [{
            "dx_pixels": 1,
            "dy_pixels": 0,
            "path_pixels": 1,
            "straightness": 1,
            "reliable_direction": False,
        }]
        result = infer_motion_guided_collection_route(
            "assets/stitched", 8, (-121716, 70415), intervals,
            sequence=("sound_box",),
            fixed_location_ids={1: "1259098429200068608"})
        self.assertEqual("candidate_motion_guided", result["status"])
        self.assertEqual("1259098429200068608",
                         result["nodes"][0]["location_id"])
        self.assertTrue(result["nodes"][0]["verified"])

    def test_affine_fit_maps_two_motion_anchors(self):
        expected = np.asarray([[100.0, -20.0], [10.0, 80.0]])
        start = np.asarray([1000.0, 2000.0])
        motion_a = np.asarray([-20.0, -30.0])
        motion_b = np.asarray([-40.0, -25.0])
        world_a = start + expected @ motion_a
        world_b = start + expected @ motion_b
        fitted = fit_motion_affine(
            start, motion_a, world_a, motion_b, world_b)
        np.testing.assert_allclose(expected, fitted, atol=1e-8)

    def test_affine_route_respects_explicit_semantic_anchors(self):
        intervals = [{
            "dx_pixels": 1, "dy_pixels": 0, "path_pixels": 1,
            "straightness": 1, "reliable_direction": False,
        }] * 2
        result = infer_affine_anchored_collection_route(
            "assets/stitched", 8, (-121716, 70415), intervals,
            sequence=("sound_box", "scenic_point"),
            fixed_location_ids={
                1: "1259098429200068608",
                2: "1257138721367920640",
            })
        # The two identical motion vectors are singular, so no transform is
        # expected; the important regression is that filtering does not crash.
        self.assertEqual("no_valid_anchor_transform", result["status"])


if __name__ == "__main__":
    unittest.main()
