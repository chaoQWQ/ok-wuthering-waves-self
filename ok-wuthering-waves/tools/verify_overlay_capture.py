import argparse
import ctypes
import json
from pathlib import Path
import sys
import time

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import config
from ok import ExitEvent
from ok.device.DeviceManager import DeviceManager
from ok.device.capture_methods.update import update_capture_method
from ok.util.config import Config
from ok.util.GlobalConfig import GlobalConfig, basic_options
from src.utils.ScreenMotionTracker import ScreenMotionTracker
from src.utils.map_geometry import get_minimap_geometry


def main():
    parser = argparse.ArgumentParser(description='使用实际 WGC 截图检查地图地形跟踪，不发送游戏输入')
    parser.add_argument('--seconds', type=float, default=4)
    parser.add_argument('--bigmap', action='store_true')
    parser.add_argument('--frame-step', type=int, default=5)
    parser.add_argument('--output', type=Path, default=ROOT / 'logs/overlay-capture-check')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
    Config.config_folder = str(args.output / 'configs')
    global_config = GlobalConfig([])
    basic = global_config.get_config(basic_options)
    basic['Auto Resize Game Window'] = False
    basic['Mute Game while in Background'] = False
    event = ExitEvent()
    manager = DeviceManager({'windows': dict(config['windows'])}, event, global_config)
    capture = None
    try:
        manager.update_pc_device()
        manager.hwnd_window.do_update_window_size()
        capture = update_capture_method({'capture_method': ['WGC']}, None, manager.hwnd_window, event)
        if capture is None:
            raise RuntimeError('实际游戏 WGC 截图不可用')
        first = capture.get_frame()
        if first is None:
            raise RuntimeError('未收到实际游戏截图')
        h, w = first.shape[:2]
        if args.bigmap:
            x, y, rw, rh = round(w * .18), round(h * .2), round(w * .62), round(h * .6)
        else:
            geometry = get_minimap_geometry(w, h)
            x, y, rw, rh = (round(geometry[key]) for key in ('left', 'top', 'width', 'height'))
        tracker = ScreenMotionTracker(minimap=not args.bigmap, max_age=args.seconds + 1)
        start = time.monotonic()
        if not tracker.anchor(first[y:y + rh, x:x + rw], start):
            raise RuntimeError('当前地形图像未提供足够的有效特征')
        records, crops = [], []
        cv2.imwrite(str(args.output / 'first.png'), first)
        while time.monotonic() - start < args.seconds:
            frame = capture.get_frame()
            if frame is None:
                raise RuntimeError('实际 WGC 截图中断')
            timestamp = time.monotonic()
            before = time.perf_counter()
            crop = frame[y:y + rh, x:x + rw].copy()
            transform = tracker.track(crop, timestamp)
            elapsed = (time.perf_counter() - before) * 1000
            records.append({'time': timestamp - start, 'elapsed_ms': elapsed,
                            'transform': transform.__dict__ if transform else None})
            if len(records) % args.frame_step == 0:
                crops.append(crop)
        cv2.imwrite(str(args.output / 'last.png'), frame)
        np.savez_compressed(args.output / 'frames.npz', frames=np.asarray(crops))
        accepted = [record for record in records if record['transform'] is not None]
        result = {'capture_size': [w, h], 'window_scaling': manager.hwnd_window.scaling,
                  'region': [x, y, rw, rh], 'frames': len(records), 'accepted': len(accepted),
                  'tracking_median_ms': float(np.median([r['elapsed_ms'] for r in records])),
                  'records': records}
        (args.output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({k: v for k, v in result.items() if k != 'records'}, ensure_ascii=False))
    finally:
        event.set()
        if capture is not None:
            capture.close()
        manager.close()


if __name__ == '__main__':
    main()
