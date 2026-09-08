"""Pure-logic loader for video-assisted chest-route metadata.

The first validation slice deliberately accepts a route with no calibrated
coordinates.  This lets the overlay verify the video/region/count metadata
without inventing chest positions from a video frame.  Later calibration can
add ordered ``nodes`` (with a database ``location_id`` and optional x/y
coordinates) without changing the file format.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional, Tuple


class ChestRouteParseError(Exception):
    """Raised when a chest-route manifest is missing or malformed."""


@dataclass(frozen=True)
class ChestRouteNode:
    """One optionally calibrated route node.

    ``location_id`` is optional in the metadata-only phase.  A node without a
    location id is retained for future visual calibration but is not used for
    target selection.
    """

    order: int
    location_id: Optional[str]
    name: str
    x: Optional[float]
    y: Optional[float]


@dataclass(frozen=True)
class ChestRoute:
    """Validated metadata for one video part and its optional node order."""

    schema_version: int
    source: str
    video_id: str
    part: int
    version: str
    map_name: str
    state_id: int
    chest_count: int
    sound_box_count: int
    scenic_point_count: int
    butterfly_count: int
    status: str
    nodes: Tuple[ChestRouteNode, ...]

    @property
    def calibrated(self) -> bool:
        """Whether at least one node has a usable database location id."""
        return any(node.location_id for node in self.nodes)

    @property
    def route_label(self) -> str:
        """Compact label suitable for an overlay status panel."""
        return f"P{self.part} {self.map_name} {self.chest_count}箱"


def _required_int(payload: dict, key: str, *, minimum: int = 0) -> int:
    value = payload.get(key)
    # bool is an int subclass but is not meaningful for counts/ids here.
    if isinstance(value, bool):
        raise ChestRouteParseError(f"'{key}' must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ChestRouteParseError(f"'{key}' must be an integer")
    if parsed < minimum:
        raise ChestRouteParseError(f"'{key}' must be >= {minimum}")
    return parsed


def _optional_float(raw: Any, key: str) -> Optional[float]:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        raise ChestRouteParseError(f"node '{key}' must be numeric")


def _parse_node(raw: Any, index: int) -> ChestRouteNode:
    if not isinstance(raw, dict):
        raise ChestRouteParseError(f"node {index} must be an object")
    order = _required_int(raw, "order", minimum=1)
    location_id = raw.get("location_id")
    if location_id is not None:
        location_id = str(location_id)
        if not location_id:
            location_id = None
    return ChestRouteNode(
        order=order,
        location_id=location_id,
        name=str(raw.get("name") or ""),
        x=_optional_float(raw.get("x"), "x"),
        y=_optional_float(raw.get("y"), "y"),
    )


def parse_chest_route(data: dict) -> ChestRoute:
    """Validate and convert a JSON manifest into a :class:`ChestRoute`."""
    if not isinstance(data, dict):
        raise ChestRouteParseError("route manifest must be an object")
    source = data.get("source")
    if not isinstance(source, str) or not source.strip():
        raise ChestRouteParseError("'source' must be a non-empty string")
    video_id = data.get("video_id")
    if not isinstance(video_id, str) or not video_id.strip():
        raise ChestRouteParseError("'video_id' must be a non-empty string")
    map_info = data.get("map")
    if not isinstance(map_info, dict):
        raise ChestRouteParseError("'map' must be an object")
    map_name = map_info.get("name")
    if not isinstance(map_name, str) or not map_name.strip():
        raise ChestRouteParseError("'map.name' must be a non-empty string")
    state_id = _required_int(map_info, "state_id", minimum=0)
    counts = data.get("counts")
    if not isinstance(counts, dict):
        raise ChestRouteParseError("'counts' must be an object")
    nodes_raw = data.get("nodes", [])
    if not isinstance(nodes_raw, list):
        raise ChestRouteParseError("'nodes' must be a list")
    nodes = tuple(_parse_node(raw, i) for i, raw in enumerate(nodes_raw))
    orders = [node.order for node in nodes]
    if len(set(orders)) != len(orders):
        raise ChestRouteParseError("node 'order' values must be unique")
    return ChestRoute(
        schema_version=_required_int(data, "schema_version", minimum=1),
        source=source.strip(),
        video_id=video_id.strip(),
        part=_required_int(data, "part", minimum=1),
        version=str(data.get("version") or ""),
        map_name=map_name.strip(),
        state_id=state_id,
        chest_count=_required_int(counts, "chests", minimum=0),
        sound_box_count=_required_int(counts, "sound_boxes", minimum=0),
        scenic_point_count=_required_int(counts, "scenic_points", minimum=0),
        butterfly_count=_required_int(counts, "butterflies", minimum=0),
        status=str(data.get("status") or "metadata_only"),
        nodes=tuple(sorted(nodes, key=lambda node: node.order)),
    )


def load_chest_route(path: str) -> ChestRoute:
    """Read and validate a chest-route JSON manifest."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError as exc:
        raise ChestRouteParseError(f"route file not found: {path}") from exc
    except OSError as exc:
        raise ChestRouteParseError(f"could not read route file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ChestRouteParseError(f"could not parse route JSON: {path}") from exc
    return parse_chest_route(data)
