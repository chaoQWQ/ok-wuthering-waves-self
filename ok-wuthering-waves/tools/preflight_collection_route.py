"""Read-only preflight for a converted collection route."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.utils.CollectionRoutePlan import (  # noqa: E402
    load_and_preflight_collection_route,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="只读检查转换路线是否满足自动执行条件")
    parser.add_argument("route", type=Path, help="路线 JSON 文件")
    parser.add_argument("--video-id", default=None, help="要求的视频 BV 号")
    parser.add_argument("--part", type=int, default=None, help="要求的分P编号")
    parser.add_argument("--map-state", type=int, default=None,
                        help="要求的地图 state_id")
    parser.add_argument("--json", action="store_true",
                        help="以 JSON 输出检查结果")
    return parser.parse_args()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    result = load_and_preflight_collection_route(
        args.route,
        expected_video_id=args.video_id,
        expected_part=args.part,
        expected_map_state_id=args.map_state,
    )
    if args.json:
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(result.summary_zh)
        print(f"文件：{args.route.resolve()}")
        print(f"点位：{len(result.nodes)}")
        for error in result.errors:
            print(f"[阻止执行] {error}")
        for warning in result.warnings:
            print(f"[提醒] {warning}")
    # 0 means executable, 3 means a readable but unsafe/incomplete route.
    return 0 if result.executable else 3


if __name__ == "__main__":
    raise SystemExit(main())
