import json
import time
from datetime import datetime
from pathlib import Path

from ok import Logger

from src.task.BaseWWTask import BaseWWTask
from src.utils.ManualRouteRecorder import (
    HotkeyEdgePoller,
    ManualRouteRecording,
    parse_position_text,
    read_action_keys,
    write_snapshot,
)
from src.utils.VideoMapCalibrator import P5_COLLECTION_SEQUENCE
from src.utils.positionDetector import PositionDetector


logger = Logger.get_logger(__name__)


class ManualRouteRecorderTask(BaseWWTask):
    """Record a route while the user moves manually; never sends game input."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "🧭 Manual Route Recorder"
        self.description = (
            "Follow a collection video manually while recording screen "
            "coordinates and event markers")
        self.default_config = {
            "Route manifest": "assets\\chest_routes\\1.0_yunlinggu_yulongtai_p5.json",
            "Mark event hotkey": "<f6>",
            "Stop recording hotkey": "<f7>",
            "Sample interval (ms)": 250,
            "Maximum duration (minutes)": 20,
            "Record action keys": True,
            "Save event screenshots": True,
            "_Output folder": "working_images\\route_recordings",
        }
        self.config_description = {
            "Route manifest": "Video-route manifest that defines event order and item types.",
            "Mark event hotkey": "Press after reaching or collecting the current numbered item.",
            "Stop recording hotkey": "Stop safely and save the current recording.",
            "Sample interval (ms)": "Screen-coordinate OCR interval; no game memory is read.",
            "Maximum duration (minutes)": "Recording stops automatically at this limit.",
            "Record action keys": "Read movement/action key states without intercepting them.",
            "Save event screenshots": "Save one game frame for each marked event.",
        }
        self.config_type = {
            "Sample interval (ms)": {"min": 100, "max": 2000, "step": 50},
            "Maximum duration (minutes)": {"min": 1, "max": 120},
        }

    @staticmethod
    def _resolve_path(value):
        path = Path(str(value))
        return path if path.is_absolute() else Path.cwd() / path

    def _load_manifest(self):
        path = self._resolve_path(self.config.get("Route manifest"))
        payload = json.loads(path.read_text(encoding="utf-8"))
        types = payload.get("timeline_type_ids") or [None] * len(P5_COLLECTION_SEQUENCE)
        if len(types) != len(P5_COLLECTION_SEQUENCE):
            raise ValueError("timeline_type_ids count does not match P5 events")
        specs = [{
            "order": index,
            "category": category,
            "type_id": types[index - 1],
        } for index, category in enumerate(P5_COLLECTION_SEQUENCE, 1)]
        return path, payload, specs

    def run(self):
        manifest_path, manifest, event_specs = self._load_manifest()
        mark_hotkey = HotkeyEdgePoller(self.config.get("Mark event hotkey"))
        stop_hotkey = HotkeyEdgePoller(self.config.get("Stop recording hotkey"))
        if not mark_hotkey.supported or not stop_hotkey.supported:
            self.log_error("Unsupported route recorder hotkey", notify=True)
            return
        output_dir = self._resolve_path(self.config.get("_Output folder"))
        output_name = datetime.now().strftime("P5_manual_%Y%m%d_%H%M%S.json")
        recording = ManualRouteRecording(
            output_dir / output_name,
            metadata={
                "manifest": str(manifest_path),
                "video_id": manifest.get("video_id"),
                "part": manifest.get("part"),
                "map": manifest.get("map"),
                "mark_hotkey": self.config.get("Mark event hotkey"),
                "stop_hotkey": self.config.get("Stop recording hotkey"),
                "safety": "screen_capture_ocr_and_read_only_key_state_only",
            },
            event_specs=event_specs,
        )
        detector = PositionDetector()
        interval = max(0.1, int(self.config.get("Sample interval (ms)")) / 1000)
        next_sample_at = 0.0
        deadline = time.monotonic() + max(
            1, int(self.config.get("Maximum duration (minutes)"))) * 60
        status = "stopped"
        self.info_set("Route recording", "等待坐标；F6 标记，F7 结束")
        self.info_set("Recording file", str(recording.output_path))
        try:
            while time.monotonic() < deadline and not recording.complete:
                now = time.monotonic()
                if now >= next_sample_at:
                    self.next_frame()
                    raw_position = detector.detect_position(self.frame)
                    position = parse_position_text(raw_position)
                    if position is not None:
                        update = recording.record_position(
                            position, raw_text=raw_position)
                        if update is not None:
                            self.info_set(
                                "Position", ",".join(map(str, update.position)))
                            self.info_set("Route segment", recording.segment)
                    next_sample_at = now + interval
                if self.config.get("Record action keys"):
                    recording.record_actions(read_action_keys())
                if mark_hotkey.poll():
                    heading = None
                    try:
                        heading = self.get_my_angle()
                    except Exception as exc:
                        logger.warning(f"route mark heading unavailable: {exc}")
                    screenshot = ""
                    order = recording.next_event_order
                    if self.config.get("Save event screenshots"):
                        snapshot_path = recording.snapshot_path(order)
                        if write_snapshot(snapshot_path, self.frame):
                            screenshot = snapshot_path.name
                    event = recording.mark_event(
                        heading_degrees=heading, screenshot=screenshot)
                    if event is None:
                        self.log_warning("No stable screen coordinate; event was not marked")
                    else:
                        self.info_set("Marked events",
                                      f"{len(recording.events)}/{len(event_specs)}")
                        self.log_info(
                            f"Marked route event {event['order']}/{len(event_specs)}",
                            notify=True)
                if stop_hotkey.poll():
                    status = "stopped_by_hotkey"
                    break
                # Poll F6/F7 frequently enough to catch a normal short key press,
                # while keeping the more expensive coordinate OCR at its configured
                # interval. This is read-only Win32 key-state polling, not a hook.
                self.sleep(min(0.04, max(0.01, next_sample_at - time.monotonic())))
            else:
                status = "complete" if recording.complete else "timeout"
        except Exception:
            status = "error"
            raise
        finally:
            recording.finish(status)
            self.info_set("Route recording", status)
            self.log_info(
                f"Route recording saved: {recording.output_path}", notify=True)
