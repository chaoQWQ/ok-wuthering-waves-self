import json
import logging
import tempfile
import time
import unittest
from copy import deepcopy
from pathlib import Path

from PySide6.QtWidgets import QApplication

from config import config
from ok import OK
from ok.device.capture_methods.update import update_capture_method
from src.utils.KuroRoutes import KuroRoute
from src.utils.MapMarksDB import MapMarksDB


ROOT = Path(__file__).resolve().parents[1]


class TestMapExplorationResponsiveness(unittest.TestCase):
    def test_route_confirmation_and_detection_without_executor_loop(self):
        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory(dir=ROOT / 'logs') as directory:
            settings = deepcopy(config)
            settings.update({
                'gui': None, 'use_gui': False, 'check_mutex': False,
                'config_folder': directory,
                'log_file': str(Path(directory) / 'runtime.log'),
                'onetime_tasks': [], 'custom_tasks': False,
                'trigger_tasks': [['src.task.MapOverlayTask', 'MapOverlayTask']],
            })
            settings['windows']['start_exe'] = False
            settings.pop('web_tabs', None)
            task_settings = json.loads((ROOT / 'configs/MapOverlayTask.json').read_text(encoding='utf-8'))
            task_settings.update({
                '_enabled': False, 'Position source': 'OCR only',
                'Map display toggle hotkey': '', 'Chest confirm hotkey': '',
                'Advance hotkey': '',
            })
            (Path(directory) / 'MapOverlayTask.json').write_text(
                json.dumps(task_settings, ensure_ascii=False), encoding='utf-8')
            runtime = OK(settings)
            app = runtime.headless_app
            executor = runtime.task_executor
            task = executor.trigger_tasks[0]
            try:
                controller = task._overlay_ctl()
                controller._marks_db = MapMarksDB(str(Path(directory) / 'marks.db'))
                route = KuroRoute.from_data(task.config['_Kuro route'])
                self.assertTrue(task.config['Kuro route navigation'])
                controller._update_kuro_route(None, route.path.state_id)
                first = controller._chest_target
                self.assertIsNotNone(first)
                position = (first.x / 100, first.y / 100, 0)
                controller.on_chest_confirm_hotkey()
                controller._process_chest_confirm(position, route.path.state_id)
                controller._update_kuro_route(position, None)
                second = controller._chest_target
                self.assertIsNotNone(second)
                self.assertNotEqual(first.location_id, second.location_id)
                self.assertTrue(controller._is_completed_id(first.location_id))
                controller._update_kuro_route(position, -1)
                self.assertIsNone(controller._chest_target)
                controller._update_kuro_route(position, None)
                self.assertIsNone(controller._chest_target)

                manager = runtime.device_manager
                runtime.global_config.get_config('Basic Options')['Auto Resize Game Window'] = False
                manager.config['preferred'] = manager.update_pc_device()
                manager.hwnd_window.do_update_window_size()
                capture = update_capture_method(
                    {'capture_method': ['WGC']}, None, manager.hwnd_window, runtime.exit_event)
                self.assertIsNotNone(capture)
                manager.capture_method = capture
                app.set_overlay_setting('boxes', False)
                task._enabled = True
                executor.paused = False
                task.run()
                samples = set()
                deadline = time.monotonic() + 7
                while time.monotonic() < deadline:
                    application.processEvents()
                    samples.add(task._detection_frame_at)
                    time.sleep(0.02)
                self.assertIsNone(executor.thread)
                self.assertTrue(task._detection_thread.is_alive())
                self.assertTrue(task._hotkey_thread.is_alive())
                self.assertGreaterEqual(len(samples), 6)
                self.assertLess(time.monotonic() - task._detection_frame_at, 2)
                self.assertIsNotNone(task._projection_frame)
                self.assertFalse(app.overlay_state()['boxes'])
                task.disable()
                self.assertIsNone(task._detection_thread)
                self.assertIsNone(task._hotkey_thread)
                self.assertIsNone(task._map_overlay_view)
                print(f'地图独立检测截图更新次数：{len(samples)}')
            finally:
                runtime.exit_event.set()
                task._stop_detection()
                executor.destroy()
                runtime.device_manager.close()
                application.processEvents()
                logging.shutdown()


if __name__ == '__main__':
    unittest.main()
