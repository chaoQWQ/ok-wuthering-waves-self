import json
import tempfile
import unittest
from pathlib import Path

from src.utils.CollectionRoutePlan import (
    load_and_preflight_collection_route,
    preflight_collection_route,
)


def ready_payload():
    return {
        "schema_version": 1,
        "video_id": "BV17QwyzVEbZ",
        "part": 5,
        "map": {"name": "云陵谷·玉龙台", "state_id": 8},
        "status": "verified",
        "automation_ready": True,
        "counts": {
            "chests": 1,
            "sound_boxes": 0,
            "scenic_points": 0,
            "butterflies": 0,
        },
        "nodes": [{
            "order": 1,
            "category": "chest",
            "location_id": "point-1",
            "x": -125000.0,
            "y": 70000.0,
            "verified": True,
        }],
    }


class TestCollectionRoutePlan(unittest.TestCase):
    def test_verified_route_passes(self):
        result = preflight_collection_route(
            ready_payload(), expected_video_id="BV17QwyzVEbZ",
            expected_part=5, expected_map_state_id=8)
        self.assertTrue(result.executable)
        self.assertEqual(1, len(result.nodes))
        self.assertEqual("路线预检通过：1 个点位", result.summary_zh)

    def test_flags_cannot_bypass_unverified_node(self):
        payload = ready_payload()
        payload["nodes"][0]["verified"] = False
        result = preflight_collection_route(payload)
        self.assertFalse(result.executable)
        self.assertIn("第 1 个点位尚未验证", result.errors)

    def test_draft_is_rejected_for_multiple_independent_reasons(self):
        payload = ready_payload()
        payload["status"] = "needs_map_calibration"
        payload["automation_ready"] = False
        result = preflight_collection_route(payload)
        self.assertFalse(result.executable)
        self.assertTrue(any("路线状态未验证" in item for item in result.errors))
        self.assertIn("automation_ready 必须明确为 true", result.errors)

    def test_order_ids_coordinates_and_counts_are_checked(self):
        payload = ready_payload()
        payload["counts"]["chests"] = 2
        payload["nodes"].append({
            "order": 3,
            "category": "chest",
            "location_id": "point-1",
            "x": "nan",
            "y": 1,
            "verified": True,
        })
        result = preflight_collection_route(payload)
        self.assertFalse(result.executable)
        self.assertTrue(any("order 应为 2" in item for item in result.errors))
        self.assertTrue(any("location_id 重复" in item for item in result.errors))
        self.assertTrue(any("坐标无效" in item for item in result.errors))

    def test_file_loader_reports_invalid_json_without_raising(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "route.json"
            path.write_text("{not json", encoding="utf-8")
            result = load_and_preflight_collection_route(path)
        self.assertFalse(result.executable)
        self.assertTrue(result.errors[0].startswith("路线文件读取失败"))

    def test_current_p5_shape_remains_non_executable(self):
        payload = ready_payload()
        payload["status"] = "needs_map_calibration"
        payload["automation_ready"] = False
        payload["nodes"][0]["verified"] = False
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "P5路线草稿.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            result = load_and_preflight_collection_route(
                path, expected_video_id="BV17QwyzVEbZ", expected_part=5,
                expected_map_state_id=8)
        self.assertFalse(result.executable)
        self.assertGreaterEqual(len(result.errors), 3)


if __name__ == "__main__":
    unittest.main()
