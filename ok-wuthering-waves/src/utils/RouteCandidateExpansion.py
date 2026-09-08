"""Expand video-route candidates around absolute minimap projections."""

from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from contextlib import closing
from pathlib import Path
from typing import Mapping, Sequence

from src.utils.VideoMapCalibrator import COLLECTION_CATEGORY_TYPES


def reliable_probe_positions(probe_payloads: Sequence[Mapping]) -> dict[int, list[tuple[float, float]]]:
    """Collect conservative absolute positions from candidate-window probes."""
    output: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for payload in probe_payloads:
        for item in payload.get("results", []):
            if "order" not in item:
                continue
            probe = item.get("probe") or {}
            position = probe.get("player_game")
            scale = float(probe.get("map_scale") or 0)
            if (not position or len(position) < 2 or
                    int(probe.get("inlier_count") or 0) < 3 or
                    int(probe.get("match_count") or 0) < 4 or
                    not 0.45 <= scale <= 1.8):
                continue
            point = (float(position[0]), float(position[1]))
            if all(math.hypot(point[0] - old[0], point[1] - old[1]) > 250
                   for old in output[int(item["order"])]):
                output[int(item["order"])].append(point)
    return dict(output)


def expand_review_candidates(
        review_items: Sequence[Mapping], database: str | Path,
        map_id: int, type_sequence: Sequence[str | None],
        probe_payloads: Sequence[Mapping], radius: float = 4500.0,
        max_candidates: int = 12) -> list[dict]:
    positions = reliable_probe_positions(probe_payloads)
    output = []
    with closing(sqlite3.connect(str(database))) as connection:
        for item in review_items:
            order = int(item["order"])
            category = str(item.get("category") or "")
            exact_type = (type_sequence[order - 1]
                          if order <= len(type_sequence) else None)
            accepted_types = ((str(exact_type),) if exact_type else
                              COLLECTION_CATEGORY_TYPES.get(category, ()))
            candidates = {
                str(candidate["location_id"]): dict(candidate)
                for candidate in item.get("candidates", [])
            }
            points = positions.get(order, [])
            if points and accepted_types:
                placeholders = ",".join("?" for _ in accepted_types)
                query = f"""
                    SELECT l.id, l.item_id, i.name, l.type_id, l.x, l.y,
                           l.description
                    FROM location l JOIN item i ON i.id = l.item_id
                    WHERE l.state_id = ? AND l.type_id IN ({placeholders})
                """
                rows = connection.execute(
                    query, [int(map_id), *accepted_types]).fetchall()
                for row in rows:
                    location_id, _, name, type_id, x, y, description = row
                    distance = min(math.hypot(float(x) - px, float(y) - py)
                                   for px, py in points)
                    if distance > radius:
                        continue
                    location_id = str(location_id)
                    candidate = candidates.setdefault(location_id, {
                        "location_id": location_id,
                        "category": category,
                        "type_id": str(type_id),
                        "name": str(name),
                        "x": float(x),
                        "y": float(y),
                        "description": str(description or ""),
                        "verified": False,
                        "sources": [],
                    })
                    if "小地图绝对定位邻域" not in candidate["sources"]:
                        candidate["sources"].append("小地图绝对定位邻域")
                    candidate["projection_neighbour_distance"] = round(distance, 1)
            candidate_list = list(candidates.values())
            candidate_list.sort(key=lambda candidate: (
                candidate.get("projection_neighbour_distance", float("inf")),
                "锚定运动主候选" not in candidate.get("sources", []),
                candidate["location_id"],
            ))
            candidate_list = candidate_list[:max(1, int(max_candidates))]
            expanded = dict(item)
            expanded["absolute_probe_positions"] = [list(point)
                                                    for point in points]
            expanded["candidates"] = candidate_list
            expanded["candidate_count"] = len(candidate_list)
            output.append(expanded)
    return output
