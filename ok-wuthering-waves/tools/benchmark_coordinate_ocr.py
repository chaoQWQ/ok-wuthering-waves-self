import argparse
import hashlib
import importlib.metadata
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw


APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))


def load_frame(path):
    frame = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError(f"无法读取截图：{path}")
    return frame


def create_sheets(output, manifest):
    if manifest is not None:
        source_entries = json.loads(manifest.read_text(encoding="utf-8"))
        paths = [APP_ROOT / entry["file"] for entry in source_entries]
    else:
        paths = sorted(
            (APP_ROOT / "screenshots/notification").glob("*_original.png"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        paths += sorted((APP_ROOT / "screenshots/quest-movement-verification").glob("*.png"))
        paths += sorted((APP_ROOT / "screenshots/quest-camera-verification").glob("*.png"))
        paths += sorted((APP_ROOT / "screenshots/log").glob("*_original.png"))[:2]
        paths += [APP_ROOT / "tests/images/mini_map.png"]
    output.mkdir(parents=True, exist_ok=True)
    entries = []
    for start in range(0, len(paths), 8):
        sheet = Image.new("RGB", (1500, 8 * 160), "#202020")
        draw = ImageDraw.Draw(sheet)
        for offset, path in enumerate(paths[start:start + 8]):
            index = start + offset
            frame = load_frame(path)
            height, width = frame.shape[:2]
            crop = frame[int(height * 1047 / 1080):height, :int(height * 480 / 1080)]
            image = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
            image = image.resize((1440, 99), Image.Resampling.NEAREST)
            sheet.paste(image, (10, offset * 160 + 45))
            relative = path.relative_to(APP_ROOT).as_posix()
            draw.text((10, offset * 160 + 10), f"{index:02d}  {relative}  {width}x{height}", fill="white")
            entries.append({"id": index, "file": relative, "expected": None})
        sheet.save(output / f"sheet-{start // 8}.png")
    (output / "candidates.json").write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已生成 {len(entries)} 张截图的检查图：{output}", flush=True)


def benchmark(manifest, reference, output, repeats, selected_variants):
    from ok import Logger
    from onnxocr.onnx_paddleocr import ONNXPaddleOcr
    from src.utils.positionDetector import PositionDetector

    sys.path.insert(0, str(reference / "src"))
    from ocr_engine import OCRWorker

    entries = json.loads(manifest.read_text(encoding="utf-8"))
    if not entries or repeats < 1:
        raise ValueError("截图清单必须非空，重复次数必须大于零")
    current = ONNXPaddleOcr(use_angle_cls=False, logger=Logger.get_logger("coordinate_benchmark"),
                            use_npu=True, use_openvino=True)
    detector = PositionDetector()
    worker = OCRWorker()
    model_path = reference / "models/coord_ocr.onnx"
    if not worker.load_model(model_path):
        raise RuntimeError("Navigator OCR 模型加载失败")

    def infer_current(frame, preprocessing="original", wide_roi=False):
        height, width = frame.shape[:2]
        image = Image.fromarray(frame[..., ::-1])
        box = (0, height - height // 32, width // 4, height) if wide_roi else detector._calc_crop_box(height, width)
        crop = image.crop(box)
        if preprocessing != "original":
            gray = cv2.cvtColor(np.array(crop), cv2.COLOR_RGB2GRAY)
            if preprocessing == "clahe":
                gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
            crop = Image.fromarray(cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB))
        result = current.ocr(np.array(detector.preprocess(crop)), det=False, rec=True, cls=False)
        texts = [text for text, _ in result[0]] if result and result[0] else []
        position = detector.postprocess(texts)
        return position or None, {"texts": texts, "recognition": result}

    def infer_reference(frame, own_roi):
        height, width = frame.shape[:2]
        if own_roi:
            crop = frame[height - height // 32:height, :width // 4]
        else:
            left, upper, right, lower = detector._calc_crop_box(height, width)
            crop = frame[upper:lower, left:right]
        detections = worker._run_onnx_inference(crop)
        success, coords, metadata = worker._parse_path_b_spacing_dominant(detections)
        position = ",".join(map(str, coords)) if success else None
        return position, metadata

    variants = {
        "current": infer_current,
        "current_grayscale": lambda frame: infer_current(frame, "grayscale"),
        "current_clahe": lambda frame: infer_current(frame, "clahe"),
        "current_wide_roi": lambda frame: infer_current(frame, wide_roi=True),
        "navigator_same_roi": lambda frame: infer_reference(frame, False),
        "navigator_default_roi": lambda frame: infer_reference(frame, True),
    }
    if selected_variants:
        variants = {name: variants[name] for name in selected_variants}
    frames = [(entry, load_frame(APP_ROOT / entry["file"])) for entry in entries]
    for infer in variants.values():
        for _ in range(3):
            infer(frames[0][1])
    rows = []
    for entry, frame in frames:
        for name, infer in variants.items():
            timings = []
            positions = []
            for _ in range(repeats):
                start = time.perf_counter()
                position, metadata = infer(frame)
                timings.append((time.perf_counter() - start) * 1000)
                positions.append(position)
            if len(set(positions)) != 1:
                raise RuntimeError(f"同一截图的重复识别结果发生变化：{entry['file']} {name}")
            rows.append({**entry, "variant": name, "position": position,
                         "correct": position == entry["expected"], "timings_ms": timings,
                         "metadata": metadata})
            print(f"{entry['id']:02d} {name}: {position} expected={entry['expected']} "
                  f"{statistics.median(timings):.1f} ms", flush=True)
    summaries = {}
    for name in variants:
        selected = [row for row in rows if row["variant"] == name]
        positive = [row for row in selected if row["expected"] is not None]
        negative = [row for row in selected if row["expected"] is None]
        timings = [duration for row in selected for duration in row["timings_ms"]]
        summaries[name] = {
            "positive_total": len(positive), "positive_correct": sum(row["correct"] for row in positive),
            "negative_total": len(negative), "false_positive": sum(row["position"] is not None for row in negative),
            "wrong_coordinates": sum(not row["correct"] and row["position"] is not None for row in positive),
            "missing_coordinates": sum(row["position"] is None for row in positive),
            "median_ms": statistics.median(timings), "p95_ms": float(np.percentile(timings, 95)),
        }
    revision = subprocess.check_output(["git", "-C", str(reference), "rev-parse", "HEAD"], text=True).strip()
    result = {"reference_revision": revision, "model_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
              "current_execution_devices": list(current.text_recognizer.session.get_property("EXECUTION_DEVICES")),
              "versions": {name: importlib.metadata.version(name) for name in
                           ("onnxocr-ppocrv5", "openvino", "onnxruntime", "opencv-python", "numpy")},
              "source_sha256": {entry["file"]: hashlib.sha256((APP_ROOT / entry["file"]).read_bytes()).hexdigest()
                                for entry in entries},
              "reference_providers": worker.model.get_providers(), "repeats": repeats,
              "summaries": summaries, "rows": rows}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summaries, ensure_ascii=False, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description="使用真实游戏截图比较当前 OCR 与 Navigator OCR")
    parser.add_argument("--sheet", action="store_true", help="生成截图检查图，不运行模型")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--variants", nargs="+", choices=["current", "current_grayscale", "current_clahe", "current_wide_roi",
                                                           "navigator_same_roi", "navigator_default_roi"])
    args = parser.parse_args()
    if args.sheet:
        create_sheets(args.output, args.manifest)
    else:
        if args.manifest is None or args.reference is None:
            parser.error("运行比较需要 --manifest 与 --reference")
        benchmark(args.manifest, args.reference, args.output, args.repeats, args.variants)


if __name__ == "__main__":
    main()
