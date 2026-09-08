import json
import tempfile
import unittest
from pathlib import Path

from src.utils.ManualRouteRecorder import (
    HotkeyEdgePoller,
    ManualRouteRecording,
    StablePositionFilter,
    parse_hotkey_vk_codes,
    parse_position_text,
)


class TestManualRouteRecorder(unittest.TestCase):
    def test_position_and_hotkey_parsers(self):
        self.assertEqual((1, -2, 3), parse_position_text("1,-2,3"))
        self.assertIsNone(parse_position_text("1,2"))
        self.assertEqual((0x76,), parse_hotkey_vk_codes("<f7>"))
        self.assertEqual((0x11, 0x75),
                         parse_hotkey_vk_codes("<ctrl>+<f6>"))

    def test_hotkey_poller_reports_only_rising_edge(self):
        down = {0x75: False}
        poller = HotkeyEdgePoller("<f6>", lambda code: down[code])
        self.assertFalse(poller.poll())
        down[0x75] = True
        self.assertTrue(poller.poll())
        self.assertFalse(poller.poll())
        down[0x75] = False
        self.assertFalse(poller.poll())
        down[0x75] = True
        self.assertTrue(poller.poll())

    def test_filter_rejects_one_jump_and_confirms_settled_teleport(self):
        filt = StablePositionFilter(jump_threshold=10,
                                    settle_threshold=3,
                                    teleport_confirmations=3)
        self.assertEqual((0, 0, 0), filt.update((0, 0, 0)).position)
        self.assertIsNone(filt.update((100, 100, 0)))
        self.assertIsNone(filt.update((101, 100, 0)))
        update = filt.update((101, 101, 0))
        self.assertTrue(update.teleported)
        self.assertEqual((101, 101, 0), update.position)

    def test_recording_saves_samples_actions_events_and_status(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "route.json"
            recording = ManualRouteRecording(
                path, {"video_id": "BV"},
                [{"order": 1, "category": "chest", "type_id": "qzx_01"}],
                started_at=100)
            recording.record_position((1, 2, 3), timestamp=101)
            recording.record_actions(("w",), timestamp=101.1)
            event = recording.mark_event(timestamp=102, heading_degrees=45)
            recording.finish("complete", timestamp=103)
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(1, payload["sample_count"])
        self.assertEqual([100, 200, 300], event["world_position"])
        self.assertEqual("complete", payload["status"])
        self.assertEqual(["w"], payload["actions"][0]["keys"])

    def test_task_source_contains_no_input_generation_helpers(self):
        source = Path("src/task/ManualRouteRecorderTask.py").read_text(
            encoding="utf-8")
        for forbidden in ("send_key(", "send_key_down(", "click(",
                          "mouse_down(", "mouse_move("):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
