"""Validated collection-route plans shared by offline tools and game tasks.

The video converter intentionally emits drafts before every point has been
confirmed.  This module is the hard boundary between those drafts and any
future task that may send movement keys: a route is executable only when the
file explicitly opts in *and* every node passes structural and verification
checks.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


COUNT_KEYS = {
    "chest": "chests",
    "sound_box": "sound_boxes",
    "scenic_point": "scenic_points",
    "butterfly": "butterflies",
}
EXECUTABLE_STATUSES = {"verified", "ready"}


@dataclass(frozen=True)
class CollectionRouteNode:
    order: int
    category: str
    location_id: str
    x: float
    y: float
    name: str = ""
    description: str = ""


@dataclass(frozen=True)
class CollectionRoutePreflight:
    path: Path | None
    executable: bool
    nodes: tuple[CollectionRouteNode, ...]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    video_id: str = ""
    part: int = 0
    map_state_id: int | None = None

    @property
    def summary_zh(self) -> str:
        if self.executable:
            return f"路线预检通过：{len(self.nodes)} 个点位"
        if self.errors:
            return f"路线不可执行：{self.errors[0]}"
        return "路线不可执行"

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path) if self.path else "",
            "executable": self.executable,
            "node_count": len(self.nodes),
            "video_id": self.video_id,
            "part": self.part,
            "map_state_id": self.map_state_id,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _as_positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def preflight_collection_route(
        payload: Mapping[str, Any], *, path: str | Path | None = None,
        expected_video_id: str | None = None,
        expected_part: int | None = None,
        expected_map_state_id: int | None = None) -> CollectionRoutePreflight:
    """Validate a route without performing any game interaction.

    The checks are deliberately redundant.  ``automation_ready`` and a ready
    status are required, but cannot bypass per-node verification or malformed
    coordinates.
    """
    errors: list[str] = []
    warnings: list[str] = []
    route_path = Path(path) if path is not None else None

    schema_version = _as_positive_int(payload.get("schema_version"))
    if schema_version != 1:
        errors.append("仅支持 schema_version=1")

    video_id = str(payload.get("video_id") or "").strip()
    part = _as_positive_int(payload.get("part")) or 0
    map_payload = payload.get("map")
    if not isinstance(map_payload, Mapping):
        map_payload = {}
        errors.append("缺少 map 对象")
    map_state_id = _as_positive_int(map_payload.get("state_id"))

    if expected_video_id and video_id != expected_video_id:
        errors.append(
            f"视频编号不匹配：需要 {expected_video_id}，实际 {video_id or '空'}")
    if expected_part is not None and part != expected_part:
        errors.append(f"视频分P不匹配：需要 P{expected_part}，实际 P{part}")
    if expected_map_state_id is not None and map_state_id != expected_map_state_id:
        errors.append(
            f"地图编号不匹配：需要 {expected_map_state_id}，实际 {map_state_id}")

    status = str(payload.get("status") or "").strip().lower()
    if status not in EXECUTABLE_STATUSES:
        errors.append(f"路线状态未验证：{status or '空'}")
    if payload.get("automation_ready") is not True:
        errors.append("automation_ready 必须明确为 true")

    raw_nodes = payload.get("nodes")
    if not isinstance(raw_nodes, Sequence) or isinstance(raw_nodes, (str, bytes)):
        raw_nodes = []
        errors.append("nodes 必须是点位数组")
    if not raw_nodes:
        errors.append("路线没有点位")

    nodes: list[CollectionRouteNode] = []
    seen_ids: set[str] = set()
    actual_counts: Counter[str] = Counter()
    for index, raw_node in enumerate(raw_nodes, 1):
        if not isinstance(raw_node, Mapping):
            errors.append(f"第 {index} 个点位不是对象")
            continue
        order = _as_positive_int(raw_node.get("order"))
        if order != index:
            errors.append(
                f"第 {index} 个点位 order 应为 {index}，实际 {raw_node.get('order')}")
        category = str(raw_node.get("category") or "").strip()
        if category not in COUNT_KEYS:
            errors.append(f"第 {index} 个点位类别无效：{category or '空'}")
        location_id = str(raw_node.get("location_id") or "").strip()
        if not location_id:
            errors.append(f"第 {index} 个点位缺少 location_id")
        elif location_id in seen_ids:
            errors.append(f"第 {index} 个点位 location_id 重复：{location_id}")
        seen_ids.add(location_id)
        x = _finite_number(raw_node.get("x"))
        y = _finite_number(raw_node.get("y"))
        if x is None or y is None:
            errors.append(f"第 {index} 个点位坐标无效")
        if raw_node.get("verified") is not True:
            errors.append(f"第 {index} 个点位尚未验证")
        if order is None or x is None or y is None:
            continue
        nodes.append(CollectionRouteNode(
            order=order,
            category=category,
            location_id=location_id,
            x=x,
            y=y,
            name=str(raw_node.get("name") or ""),
            description=str(raw_node.get("description") or ""),
        ))
        actual_counts[category] += 1

    declared_counts = payload.get("counts")
    if isinstance(declared_counts, Mapping):
        for category, count_key in COUNT_KEYS.items():
            declared = declared_counts.get(count_key)
            if declared is None:
                continue
            try:
                declared_int = int(declared)
            except (TypeError, ValueError):
                errors.append(f"counts.{count_key} 不是整数")
                continue
            if actual_counts[category] != declared_int:
                errors.append(
                    f"{count_key} 数量不一致：声明 {declared_int}，"
                    f"点位 {actual_counts[category]}")
    else:
        warnings.append("未提供 counts，无法核对各类收集物数量")

    executable = not errors and len(nodes) == len(raw_nodes)
    return CollectionRoutePreflight(
        path=route_path,
        executable=executable,
        nodes=tuple(nodes),
        errors=tuple(errors),
        warnings=tuple(warnings),
        video_id=video_id,
        part=part,
        map_state_id=map_state_id,
    )


def load_and_preflight_collection_route(
        path: str | Path, **expected: Any) -> CollectionRoutePreflight:
    route_path = Path(path)
    try:
        payload = json.loads(route_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return CollectionRoutePreflight(
            path=route_path, executable=False, nodes=(),
            errors=(f"路线文件不存在：{route_path}",), warnings=())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return CollectionRoutePreflight(
            path=route_path, executable=False, nodes=(),
            errors=(f"路线文件读取失败：{exc}",), warnings=())
    if not isinstance(payload, Mapping):
        return CollectionRoutePreflight(
            path=route_path, executable=False, nodes=(),
            errors=("路线 JSON 顶层必须是对象",), warnings=())
    return preflight_collection_route(payload, path=route_path, **expected)
