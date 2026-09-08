"""Command line entry point for the offline P5 route validator."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.utils.VideoRouteValidator import (  # noqa: E402
    VideoRouteValidator,
    create_onnx_coordinate_ocr,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="离线扫描一条龙视频并输出路线转换可行性报告")
    parser.add_argument("video", type=Path, help="本地视频文件")
    parser.add_argument(
        "--manifest", type=Path,
        default=REPO_ROOT / "assets/chest_routes/1.0_yunlinggu_yulongtai_p5.json",
        help="路线元数据 JSON")
    parser.add_argument(
        "--output", type=Path,
        default=REPO_ROOT / "working_images/p5_route_validation",
        help="报告和关键帧输出目录")
    parser.add_argument("--interval", type=float, default=0.5,
                        help="视觉抽样间隔（秒，默认 0.5）")
    parser.add_argument("--skip-coordinate-ocr", action="store_true",
                        help="跳过 12 个左下角坐标探针的本地 OCR")
    parser.add_argument(
        "--map-assets", type=Path,
        default=REPO_ROOT / "assets/stitched",
        help="地图特征和点位数据库目录")
    parser.add_argument("--skip-map-alignment", action="store_true",
                        help="跳过大地图关键帧世界坐标配准")
    return parser.parse_args()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    if not args.video.is_file():
        print(f"视频不存在：{args.video}", file=sys.stderr)
        return 2
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        coordinate_ocr = None
        if not args.skip_coordinate_ocr:
            coordinate_ocr = create_onnx_coordinate_ocr()
        report = VideoRouteValidator(
            args.video, manifest, args.output, args.interval,
            coordinate_ocr=coordinate_ocr,
            map_assets_dir=(None if args.skip_map_alignment
                            else args.map_assets)).run()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"验证失败：{exc}", file=sys.stderr)
        return 1
    conversion = report["conversion"]
    print(f"验证完成：{args.output.resolve()}")
    print(f"状态：{conversion['status']}")
    print(f"可直接用于自动移动：{'是' if conversion['automation_ready'] else '否'}")
    print(f"下一步：{conversion['next_step']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
