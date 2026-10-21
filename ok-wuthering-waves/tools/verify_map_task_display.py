import argparse
import faulthandler
from copy import deepcopy
import json
from pathlib import Path
import sys
import time

import cv2
from PySide6.QtWidgets import QApplication


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import config
from ok import OK
from ok.device.capture_methods.update import update_capture_method
from src.task.MapOverlayTask import _parse_position
from src.utils.positionDetector import PositionDetector


def main():
    parser = argparse.ArgumentParser(description='运行实际地图任务与 Qt 覆盖层，采集本机显示诊断')
    parser.add_argument('--seconds', type=float, default=6)
    parser.add_argument('--startup-timeout', type=float, default=120)
    parser.add_argument('--output', type=Path, default=ROOT / 'logs/map-task-display-check')
    parser.add_argument('--prior-frame', type=Path, help='从已保存的真实大世界截图获取地图搜索位置')
    args = parser.parse_args()
    faulthandler.dump_traceback_later(args.startup_timeout + args.seconds + 15, repeat=False)
    app = QApplication.instance() or QApplication([])
    args.output.mkdir(parents=True, exist_ok=True)
    settings = deepcopy(config)
    settings.update({'gui': None, 'use_gui': False, 'check_mutex': False,
                     'config_folder': str(args.output / 'configs'),
                     'log_file': str(args.output / 'runtime.log'),
                     'onetime_tasks': [], 'custom_tasks': False,
                     'trigger_tasks': [['src.task.MapOverlayTask', 'MapOverlayTask']]})
    settings['windows']['start_exe'] = False
    settings.pop('web_tabs', None)
    task_config = json.loads((ROOT / 'configs/MapOverlayTask.json').read_text(encoding='utf-8'))
    task_config.update({'_enabled': False, 'Position source': 'OCR only',
                        '_View dead reckoning': False, 'Map display toggle hotkey': '',
                        'Chest confirm hotkey': '', 'Advance hotkey': ''})
    config_dir = args.output / 'configs'
    config_dir.mkdir(exist_ok=True)
    (config_dir / 'MapOverlayTask.json').write_text(json.dumps(task_config, ensure_ascii=False), encoding='utf-8')
    runtime = OK(settings)
    manager = runtime.device_manager
    task = runtime.task_executor.trigger_tasks[0]
    executor = runtime.task_executor
    capture = None
    try:
        runtime.global_config.get_config('Basic Options')['Auto Resize Game Window'] = False
        manager.config['preferred'] = manager.update_pc_device()
        manager.hwnd_window.do_update_window_size()
        capture = update_capture_method({'capture_method': ['WGC']}, None, manager.hwnd_window, runtime.exit_event)
        if capture is None:
            raise RuntimeError('实际游戏截图不可用')
        manager.capture_method = capture
        manager.interaction = manager.win_interaction_class(capture, manager.hwnd_window)
        executor.paused = False
        task.enable()
        task._configure_position_sources()
        if args.prior_frame is not None:
            prior = cv2.imread(str(args.prior_frame))
            if prior is None:
                raise ValueError('无法读取已保存的真实截图')
            task._last_valid = _parse_position(PositionDetector().detect_position(prior))
            if task._last_valid is None:
                raise ValueError('已保存的截图没有有效坐标')
        executor.start()
        rows = []
        start = time.monotonic()
        deadline = start + args.startup_timeout
        initialized = False
        ready_row = None
        while time.monotonic() < deadline:
            began = time.perf_counter()
            app.processEvents()
            time.sleep(.033)
            controller = task._overlay_controller
            if controller is None:
                continue
            bigmap = controller._interaction_window
            is_bigmap = bigmap is not None and bigmap.isVisible()
            window = bigmap if is_bigmap else controller._minimap_direction_window
            line_window = controller._bigmap_line_window
            projection = task._image_projection
            rows.append({'elapsed_ms': (time.perf_counter() - began) * 1000,
                         'visible': window.isVisible() if window else False,
                         'marker': window._target_marker if window else None,
                         'mode': 'bigmap' if is_bigmap else 'minimap',
                         'items': len(window._draw_items) if window else 0,
                         'routes_visible': line_window.isVisible() if line_window else False,
                         'route_points': [list(layer.points) for layer in line_window._path_layers] if line_window else [],
                         'image_frames': projection.frames if projection else 0,
                         'absolute_age': time.monotonic() - projection.absolute_at if projection else None,
                         'image_age': time.monotonic() - projection.image_at if projection else None,
                         'latest_age': time.monotonic() - projection.latest_at if projection else None,
                         'foreground': manager.hwnd_window.visible,
                         'source': task.position_status()})
            if not initialized and window is not None and window.isVisible() and window._draw_items:
                deadline = time.monotonic() + args.seconds
                initialized = True
                ready_row = len(rows) - 1
        frame = task._projection_frame
        if frame is None:
            raise RuntimeError('实际地图任务没有截图')
        cv2.imwrite(str(args.output / 'capture.png'), frame)
        (args.output / 'records.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
        if window is None or not window.isVisible():
            raise RuntimeError('实际任务未显示地图覆盖层')
        window.grab().save(str(args.output / 'overlay.png'))
        if line_window is not None and line_window.isVisible():
            line_window.grab().save(str(args.output / 'routes.png'))
        result = {'capture_size': [frame.shape[1], frame.shape[0]],
                  'window_geometry': window.geometry().getRect(),
                  'source_size': getattr(window, '_source_size', None),
                  'mode': 'bigmap' if is_bigmap else 'minimap',
                  'records': rows}
        (args.output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        visible_rows = [row for row in rows if row['visible']]
        ready_rows = rows[ready_row:] if ready_row is not None else []
        hidden_rows = [row for row in ready_rows if row['foreground'] and not row['visible']]
        print(json.dumps({key: value for key, value in result.items() if key != 'records'} |
                         {'samples': len(rows), 'visible_samples': len(visible_rows),
                          'ready_samples': len(ready_rows), 'hidden_ready_samples': len(hidden_rows),
                          'first_visible': visible_rows[0] if visible_rows else None,
                          'last_visible': visible_rows[-1] if visible_rows else None}, ensure_ascii=False))
        if not ready_rows or hidden_rows:
            raise RuntimeError('实际资源点尚未连续显示，查看 records.json')
    finally:
        faulthandler.cancel_dump_traceback_later()
        runtime.exit_event.set()
        until = time.monotonic() + 8
        while executor.thread is not None and executor.thread.is_alive() and time.monotonic() < until:
            app.processEvents()
            time.sleep(.02)
        if executor.thread is not None and executor.thread.is_alive():
            raise TimeoutError('实际地图任务未结束')
        executor.destroy()
        app.processEvents()
        manager.close()


if __name__ == '__main__':
    main()
