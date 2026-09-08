"""Read-only helpers for recording a manually followed collection route."""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence

import cv2
import numpy as np


ACTION_VK_CODES = {
    "w": 0x57,
    "a": 0x41,
    "s": 0x53,
    "d": 0x44,
    "space": 0x20,
    "shift": 0x10,
    "f": 0x46,
    "t": 0x54,
    "q": 0x51,
    "e": 0x45,
}


def parse_position_text(text: str) -> tuple[int, int, int] | None:
    parts = str(text or "").split(",")
    if len(parts) != 3:
        return None
    try:
        return tuple(int(part) for part in parts)
    except ValueError:
        return None


def parse_hotkey_vk_codes(hotkey: str) -> tuple[int, ...] | None:
    aliases = {
        "ctrl": 0x11, "control": 0x11,
        "ctrl_l": 0x11, "ctrl_r": 0x11,
        "shift": 0x10, "shift_l": 0x10, "shift_r": 0x10,
        "alt": 0x12, "alt_l": 0x12, "alt_r": 0x12,
        "space": 0x20, "tab": 0x09, "enter": 0x0D,
    }
    result = []
    for raw in str(hotkey or "").strip().lower().split("+"):
        part = raw.strip().strip("<>").strip()
        if not part:
            return None
        code = aliases.get(part)
        if code is None and len(part) == 1 and part.isalnum():
            code = ord(part.upper())
        if code is None and part.startswith("f") and part[1:].isdigit():
            number = int(part[1:])
            if 1 <= number <= 24:
                code = 0x70 + number - 1
        if code is None:
            return None
        if code not in result:
            result.append(code)
    return tuple(result) if result else None


def _default_key_reader(vk_code: int) -> bool:
    if os.name != "nt":
        return False
    import ctypes
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk_code) & 0x8000)


class HotkeyEdgePoller:
    def __init__(self, hotkey: str,
                 key_reader: Callable[[int], bool] | None = None):
        self.hotkey = str(hotkey or "")
        self.vk_codes = parse_hotkey_vk_codes(self.hotkey)
        self.key_reader = key_reader or _default_key_reader
        self.was_down = False

    @property
    def supported(self) -> bool:
        return self.vk_codes is not None

    def poll(self) -> bool:
        down = bool(self.vk_codes) and all(
            self.key_reader(code) for code in self.vk_codes)
        rising = down and not self.was_down
        self.was_down = down
        return rising


def read_action_keys(
        key_reader: Callable[[int], bool] | None = None) -> tuple[str, ...]:
    reader = key_reader or _default_key_reader
    return tuple(name for name, code in ACTION_VK_CODES.items()
                 if reader(code))


@dataclass(frozen=True)
class PositionUpdate:
    position: tuple[int, int, int]
    teleported: bool = False


class StablePositionFilter:
    """Reject isolated OCR jumps and confirm a settled teleport in 3 reads."""

    def __init__(self, jump_threshold: float = 120,
                 settle_threshold: float = 18,
                 teleport_confirmations: int = 3):
        self.jump_threshold = float(jump_threshold)
        self.settle_threshold = float(settle_threshold)
        self.teleport_confirmations = max(2, int(teleport_confirmations))
        self.last: tuple[int, int, int] | None = None
        self.pending: list[tuple[int, int, int]] = []

    @staticmethod
    def _distance(a: Sequence[float], b: Sequence[float]) -> float:
        return math.hypot(float(a[0]) - float(b[0]),
                          float(a[1]) - float(b[1]))

    def update(self, position: tuple[int, int, int]) -> PositionUpdate | None:
        if self.last is None:
            self.last = position
            return PositionUpdate(position)
        if self._distance(position, self.last) <= self.jump_threshold:
            self.pending.clear()
            self.last = position
            return PositionUpdate(position)
        if (not self.pending or
                self._distance(position, self.pending[-1]) <= self.settle_threshold):
            self.pending.append(position)
        else:
            self.pending = [position]
        if len(self.pending) < self.teleport_confirmations:
            return None
        confirmed = self.pending[-1]
        self.pending.clear()
        self.last = confirmed
        return PositionUpdate(confirmed, teleported=True)


class ManualRouteRecording:
    SCHEMA_VERSION = 1

    def __init__(self, output_path: str | Path, metadata: Mapping,
                 event_specs: Sequence[Mapping],
                 started_at: float | None = None):
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot_dir = self.output_path.with_suffix("")
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.started_at = float(started_at if started_at is not None
                                else time.time())
        self.event_specs = [dict(item) for item in event_specs]
        self.filter = StablePositionFilter()
        self.segment = 1
        self.samples: list[dict] = []
        self.actions: list[dict] = []
        self.events: list[dict] = []
        self.last_position: tuple[int, int, int] | None = None
        self.last_actions: tuple[str, ...] = ()
        self.last_saved_at = 0.0
        self.payload = {
            "schema_version": self.SCHEMA_VERSION,
            "kind": "manual_collection_route_recording",
            "status": "recording",
            "metadata": dict(metadata),
            "started_at": datetime.fromtimestamp(
                self.started_at, timezone.utc).isoformat(),
        }
        self.save(force=True)

    @property
    def next_event_order(self) -> int:
        return len(self.events) + 1

    @property
    def complete(self) -> bool:
        return len(self.events) >= len(self.event_specs)

    def relative_time(self, timestamp: float | None = None) -> float:
        now = float(timestamp if timestamp is not None else time.time())
        return round(max(0.0, now - self.started_at), 3)

    def record_position(self, position: tuple[int, int, int],
                        timestamp: float | None = None,
                        heading_degrees: float | None = None,
                        raw_text: str = "") -> PositionUpdate | None:
        update = self.filter.update(position)
        if update is None:
            return None
        if update.teleported:
            self.segment += 1
        self.last_position = update.position
        sample = {
            "t": self.relative_time(timestamp),
            "segment": self.segment,
            "ocr_position": list(update.position),
            "world_position": [value * 100 for value in update.position],
            "teleported": update.teleported,
        }
        if raw_text:
            sample["raw_text"] = raw_text
        if heading_degrees is not None:
            sample["heading_degrees"] = round(float(heading_degrees), 2)
        self.samples.append(sample)
        self.save()
        return update

    def record_actions(self, actions: Sequence[str],
                       timestamp: float | None = None) -> bool:
        current = tuple(sorted(set(str(item) for item in actions)))
        if current == self.last_actions:
            return False
        self.last_actions = current
        self.actions.append({"t": self.relative_time(timestamp),
                             "keys": list(current)})
        self.save()
        return True

    def mark_event(self, timestamp: float | None = None,
                   heading_degrees: float | None = None,
                   screenshot: str = "") -> dict | None:
        if self.complete or self.last_position is None:
            return None
        spec = dict(self.event_specs[len(self.events)])
        event = {
            **spec,
            "t": self.relative_time(timestamp),
            "segment": self.segment,
            "ocr_position": list(self.last_position),
            "world_position": [value * 100 for value in self.last_position],
        }
        if heading_degrees is not None:
            event["heading_degrees"] = round(float(heading_degrees), 2)
        if screenshot:
            event["screenshot"] = screenshot
        self.events.append(event)
        self.save(force=True)
        return event

    def snapshot_path(self, order: int) -> Path:
        return self.snapshot_dir / f"event_{int(order):02}.jpg"

    def save(self, force: bool = False,
             timestamp: float | None = None) -> None:
        now = float(timestamp if timestamp is not None else time.time())
        if not force and now - self.last_saved_at < 1.5:
            return
        self.last_saved_at = now
        payload = {
            **self.payload,
            "expected_event_count": len(self.event_specs),
            "sample_count": len(self.samples),
            "event_count": len(self.events),
            "segment_count": self.segment,
            "samples": self.samples,
            "actions": self.actions,
            "events": self.events,
        }
        temporary = self.output_path.with_suffix(self.output_path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        temporary.replace(self.output_path)

    def finish(self, status: str, timestamp: float | None = None) -> None:
        self.payload["status"] = str(status)
        self.payload["finished_at"] = datetime.fromtimestamp(
            float(timestamp if timestamp is not None else time.time()),
            timezone.utc).isoformat()
        self.save(force=True, timestamp=timestamp)


def write_snapshot(path: str | Path, frame: np.ndarray) -> bool:
    if frame is None:
        return False
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".jpg", frame,
                               [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        return False
    encoded.tofile(str(target))
    return True
