"""Expand route-review candidates using absolute minimap probe positions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.utils.RouteCandidateExpansion import expand_review_candidates  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--probe", type=Path, nargs="+", required=True)
    parser.add_argument("--database", type=Path,
                        default=REPO_ROOT / "assets/stitched/map_items.db")
    parser.add_argument("--radius", type=float, default=4500)
    parser.add_argument("--max-candidates", type=int, default=12)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    review = json.loads(args.review.read_text(encoding="utf-8"))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    probes = [json.loads(path.read_text(encoding="utf-8"))
              for path in args.probe]
    items = expand_review_candidates(
        review.get("items", []), args.database,
        int(manifest["map"]["state_id"]),
        manifest.get("timeline_type_ids") or [], probes,
        radius=args.radius, max_candidates=args.max_candidates)
    payload = {"items": items}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已输出：{args.output.resolve()}")
    print(f"事件：{len(items)}，候选：{sum(len(i['candidates']) for i in items)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
