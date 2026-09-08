"""Probe absolute world positions from minimap frames in a local video."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict
from pathlib import Path

import cv2


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.utils.VideoMapCalibrator import RecordedMinimapMapLocator  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="离线把视频小地图匹配到本地世界地图")
    parser.add_argument("video", type=Path)
    parser.add_argument("--assets", type=Path,
                        default=REPO_ROOT / "assets/stitched")
    parser.add_argument("--map-id", default="8")
    parser.add_argument("--times", type=float, nargs="+",
                        help="需要探测的秒数")
    parser.add_argument("--review", type=Path,
                        help="P5点位复核清单.json；逐候选做局部匹配")
    parser.add_argument("--orders", type=int, nargs="*",
                        help="只探测指定事件序号")
    parser.add_argument("--window", type=float, default=0.0,
                        help="事件前后扫描秒数")
    parser.add_argument("--step", type=float, default=1.0,
                        help="窗口扫描步长（秒）")
    parser.add_argument("--region-size", type=int, default=1000,
                        help="候选局部匹配区域边长（地图像素）")
    parser.add_argument("--ratio", type=float, default=0.60,
                        help="SIFT ratio 阈值")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--quiet", action="store_true",
                        help="已有 --output 时不在终端重复打印 JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.times and not args.review:
        raise SystemExit("必须提供 --times 或 --review")
    locator = RecordedMinimapMapLocator(args.assets, args.map_id)
    locator.feature_locator.engine.ratio = args.ratio
    capture = cv2.VideoCapture(str(args.video))
    results = []
    try:
        for seconds in args.times or []:
            capture.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
            ok, frame = capture.read()
            if not ok:
                results.append({
                    "time_seconds": seconds,
                    "success": False,
                    "reason": "无法读取视频帧",
                })
                continue
            results.append(asdict(locator.locate(frame, seconds)))
        if args.review:
            review = json.loads(args.review.read_text(encoding="utf-8"))
            for item in review.get("items", []):
                if args.orders and int(item["order"]) not in args.orders:
                    continue
                seconds = float(item["time_seconds"])
                for candidate in item.get("candidates", []):
                    samples = []
                    window = max(0.0, float(args.window))
                    step = max(0.2, float(args.step))
                    offset = -window
                    while offset <= window + 1e-6:
                        sample_time = max(0.0, seconds + offset)
                        capture.set(cv2.CAP_PROP_POS_MSEC, sample_time * 1000)
                        ok, frame = capture.read()
                        if not ok:
                            offset += step
                            continue
                        probe = asdict(locator.locate_near(
                            frame, sample_time,
                            (candidate["x"], candidate["y"]),
                            region_size=args.region_size))
                        player_game = probe.get("player_game")
                        projected_error = None
                        if player_game:
                            projected_error = round(math.hypot(
                                float(player_game[0]) - float(candidate["x"]),
                                float(player_game[1]) - float(candidate["y"])), 1)
                        scale = float(probe.get("map_scale") or 0)
                        scale_penalty = (abs(math.log(scale / 0.77))
                                         if scale > 0 else 10.0)
                        error_penalty = ((projected_error / 5000.0)
                                         if projected_error is not None else 5.0)
                        window_score = (
                            float(probe.get("inlier_count") or 0) * 1.5 +
                            float(probe.get("match_count") or 0) * 0.15 +
                            float(probe.get("confidence") or 0) -
                            scale_penalty * 1.2 - error_penalty
                        )
                        samples.append({
                            "time_seconds": round(sample_time, 3),
                            "offset_seconds": round(offset, 3),
                            "projected_error": projected_error,
                            "window_score": round(window_score, 4),
                            "probe": probe,
                        })
                        offset += step
                    samples.sort(key=lambda sample: sample["window_score"],
                                 reverse=True)
                    best = samples[0] if samples else {
                        "projected_error": None,
                        "window_score": -999,
                        "probe": {"success": False,
                                  "reason": "窗口内无法读取视频帧"},
                    }
                    results.append({
                        "order": int(item["order"]),
                        "time_seconds": seconds,
                        "location_id": str(candidate["location_id"]),
                        "candidate_x": candidate["x"],
                        "candidate_y": candidate["y"],
                        "sources": candidate.get("sources", []),
                        "projected_error": best["projected_error"],
                        "window_score": best["window_score"],
                        "best_time_seconds": best.get("time_seconds"),
                        "best_offset_seconds": best.get("offset_seconds"),
                        "probe": best["probe"],
                        "sample_count": len(samples),
                    })
    finally:
        capture.release()
    payload = {"video": str(args.video), "map_id": str(args.map_id),
               "ratio": args.ratio, "region_size": args.region_size,
               "window": args.window, "step": args.step,
               "results": results}
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if not args.quiet:
        print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
