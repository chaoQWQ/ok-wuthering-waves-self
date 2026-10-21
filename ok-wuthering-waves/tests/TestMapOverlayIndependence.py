import logging
import tempfile
import threading
import unittest
from copy import deepcopy
from pathlib import Path

from PySide6.QtWidgets import QApplication

from config import config
from ok import OK, Box
from ok.core.events import communicate


class TestMapOverlayIndependence(unittest.TestCase):
    def test_map_window_keeps_global_boxes_setting(self):
        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / 'logs') as directory:
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
            runtime = OK(settings)
            app = runtime.headless_app
            task = runtime.task_executor.trigger_tasks[0]
            try:
                app.set_overlay_setting('boxes', False)
                runtime.task_executor.paused = False
                task._enabled = True
                overlay = task.get_overlay_view()
                self.assertGreater(overlay._hwnd, 0)
                self.assertFalse(overlay._boxes_enabled)
                self.assertFalse(app.overlay_state()['boxes'])

                communicate.emit_draw_box('ocr_zone', [Box(0, 0, 100, 100)], 'blue')
                self.assertFalse(overlay._boxes_active)
                drawn = threading.Event()

                def paint(canvas, window):
                    canvas.rectangle(20, 20, 40, 40, color=(0, 255, 0))
                    drawn.set()

                overlay.update_overlay(True, 0, 0, 200, 200, 200, 200, 1)
                overlay.draw('map_items', paint, duration=3)
                self.assertTrue(drawn.wait(5))
                app.set_overlay_setting('boxes', True)
                app.set_overlay_setting('boxes', False)
                self.assertIs(task.get_overlay_view(), overlay)
                self.assertFalse(overlay._closed)
                self.assertFalse(overlay._boxes_enabled)

                runtime.task_executor.paused = True
                task._on_executor_paused(True)
                self.assertTrue(overlay._closed)
                self.assertIsNone(task._map_overlay_view)
                self.assertFalse(app.overlay_state()['boxes'])

                runtime.task_executor.paused = False
                resumed = task.get_overlay_view()
                self.assertIsNot(resumed, overlay)
                self.assertGreater(resumed._hwnd, 0)
                app.set_overlay_setting('boxes', True)
                task.disable()
                self.assertTrue(resumed._closed)
                self.assertTrue(app.overlay_state()['boxes'])
            finally:
                runtime.exit_event.set()
                runtime.task_executor.destroy()
                runtime.device_manager.close()
                application.processEvents()
                logging.shutdown()


if __name__ == '__main__':
    unittest.main()
