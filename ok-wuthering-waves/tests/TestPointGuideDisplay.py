import os
import tempfile
import time
import unittest
from pathlib import Path

from PySide6.QtCore import QPoint, QThread, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from config import config
from ok import Config
from ok.device.DeviceManager import DeviceManager
from ok.task.TaskExecutor import TaskExecutor
from ok.util.GlobalConfig import GlobalConfig
from ok.util.handler import ExitEvent

from src.task.MapOverlayTask import ClickTarget, MapOverlayTask, OverlayController
from src.utils.GuideImageCache import GuideImageCache
from src.utils.InteractionOverlayWindow import InteractionOverlayWindow
from src.utils.KuroRoutes import KuroRoutesClient
from src.utils.MapItemOverlay import MapItemOverlay


ROOT = Path(__file__).resolve().parents[1]
POINT_ID = '1287514641007132672'


class TestPointGuideDisplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.detail = KuroRoutesClient(8).point_detail(POINT_ID)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.previous_folder = Config.config_folder
        Config.config_folder = self.directory.name
        self.exit_event = ExitEvent()
        global_config = GlobalConfig(config['global_configs'])
        self.manager = DeviceManager({}, self.exit_event, global_config)
        self.executor = TaskExecutor(self.manager, exit_event=self.exit_event,
                                     global_config=global_config, config={'locale': 'zh_CN'})
        self.task = MapOverlayTask(executor=self.executor, app=self.app)
        self.task.config = Config('guide-test', self.task.default_config, folder=self.directory.name)
        self.task._locked_map_id = '8'
        self.task._overlay = MapItemOverlay(str(ROOT / 'assets/stitched/map_items.db'))
        self.controller = OverlayController(self.task)
        self.controller._guide_image_cache = GuideImageCache(self.directory.name)
        self.window = InteractionOverlayWindow(0, 0, 800, 600)
        self.controller._interaction_window = self.window
        self.controller._connect_window_signals(self.window)
        self.controller._click_targets = [
            ClickTarget('item', POINT_ID, 100, 100, section_id=1, index=1, name='基准奇藏箱')]

    def tearDown(self):
        self.controller.close()
        self.task._overlay.close()
        self.manager.close()
        self.executor.destroy()
        self.exit_event.set()
        Config.config_folder = self.previous_folder
        self.directory.cleanup()

    def wait_until(self, predicate):
        deadline = time.monotonic() + 45
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(25)
        self.assertTrue(predicate())

    def open_point(self):
        self.controller.on_left_click(0)
        self.wait_until(lambda: self.controller._bubble.image_status in ('ready', 'failed', 'no_image'))
        self.assertEqual(self.controller._bubble.image_status, 'ready')
        self.app.processEvents()

    def test_live_point_images_buttons_and_reopen(self):
        self.task.config['Kuro route navigation'] = True
        self.open_point()
        bubble = self.controller._bubble
        self.assertEqual(bubble.image_count, 2)
        self.assertIn('完成能量矩阵解密获得', bubble.text)
        self.assertFalse(bubble.pixmap.isNull())
        first = bubble.pixmap.toImage()
        self.window.show()
        self.app.processEvents()
        next_rect = self.window._bubble_navigation_rects()[1][1]
        self.assertTrue(self.window.mask().contains(next_rect.center()))
        QTest.mouseClick(self.window, Qt.LeftButton, pos=next_rect.center())
        self.wait_until(lambda: self.controller._bubble.image_status == 'ready')
        self.assertEqual(self.controller._bubble.image_index, 1)
        self.assertNotEqual(first, self.controller._bubble.pixmap.toImage())
        self.app.processEvents()
        prev_rect = self.window._bubble_navigation_rects()[0][1]
        QTest.mouseClick(self.window, Qt.LeftButton, pos=prev_rect.center())
        self.assertEqual(self.controller._bubble.image_index, 0)
        self.assertEqual(self.controller._bubble.pixmap.toImage(), first)
        self.controller.close_bubble()
        self.open_point()
        self.assertEqual(self.controller._bubble.pixmap.toImage(), first)
        canvas = QImage(800, 600, QImage.Format_ARGB32)
        canvas.fill(0)
        painter = QPainter(canvas)
        self.window._paint_bubble(painter)
        painter.end()
        self.assertGreater(canvas.pixelColor(self.window._bubble_box_rect().center()).alpha(), 0)
        screenshot = os.environ.get('POINT_GUIDE_SCREENSHOT')
        if screenshot:
            self.assertTrue(canvas.save(screenshot))

    def test_normal_item_and_stale_request(self):
        self.controller.on_left_click(0)
        self.controller.close_bubble()
        self.wait_until(lambda: bool(self.controller._guide_image_cache._details))
        self.app.processEvents()
        self.assertIsNone(self.controller._bubble)
        self.open_point()
        self.assertEqual(self.controller._bubble.image_count, 2)

    def test_cache_callbacks_gui_thread_duplicate_and_disk(self):
        cache = self.controller._guide_image_cache
        url = self.detail['content']['picturesUrl'][0]
        results = []

        def ready(image, status):
            results.append((status, QThread.currentThread(), image))

        cache.request_image(POINT_ID, url, ready)
        cache.request_image(POINT_ID, url, ready)
        cache.request_image('another-node', url, ready)
        self.wait_until(lambda: len(results) == 3)
        self.assertTrue(all(status == 'ready' and thread == self.app.thread()
                            for status, thread, image in results))
        cache.request_image(POINT_ID, url, ready)
        self.assertEqual(len(results), 4)
        cache.stop()
        disk_cache = GuideImageCache(self.directory.name)
        try:
            disk_cache.request_image(POINT_ID, url, ready)
            self.wait_until(lambda: len(results) == 5)
            self.assertEqual(results[-1][0], 'ready')
            self.assertFalse(disk_cache.get_pixmap(POINT_ID, url).isNull())
        finally:
            disk_cache.stop()

    def test_live_overlay_route_section_selection(self):
        from src.utils.MapMarksDB import MapMarksDB
        from src.utils.NodeIconCache import NodeIconCache
        data = KuroRoutesClient(8).detail('1452042731439439872')
        self.controller._marks_db = MapMarksDB(str(Path(self.directory.name) / 'marks.db'))
        self.controller._icon_cache = NodeIconCache(str(Path(self.directory.name) / 'icons'))
        self.task.config['_Kuro route'] = data
        self.task.config['Kuro route navigation'] = True
        self.controller._update_kuro_route(None, 8)
        first_id = data['sectionList'][0]['sectionId']
        second_id = data['sectionList'][1]['sectionId']
        self.assertEqual(self.task.config['_Kuro route section'], first_id)
        self.assertEqual(len(self.controller._route.sections), 1)
        choices = tuple((s.section_id, len(s.nodes))
                        for s in self.controller._kuro_navigation.route.path.sections)
        self.window.set_route_sections(choices, first_id)
        self.window.show()
        self.app.processEvents()
        self.assertEqual(self.window.section_selector.count(), 15)
        self.assertTrue(self.window.mask().contains(self.window.section_selector.geometry().center()))
        selector = self.window.section_selector
        QTest.mouseClick(selector, Qt.LeftButton, pos=QPoint(selector.width() - 15, selector.height() // 2))
        QTest.qWait(200)
        self.assertTrue(selector.view().isVisible())
        option_rect = selector.view().visualRect(selector.model().index(1, 0))
        QTest.mouseMove(selector.view().viewport(), option_rect.center())
        QTest.qWait(50)
        QTest.mouseClick(selector.view().viewport(), Qt.LeftButton, pos=option_rect.center())
        self.assertEqual(self.task.config['_Kuro route section'], second_id)
        self.controller._update_kuro_route(None, 8)
        section = self.controller._route.sections[0]
        self.assertEqual(section.section_id, second_id)
        self.assertEqual(self.controller._chest_target.location_id, section.nodes[0].position_id)
        layers, items, boxes, targets = self.controller._build_visible_path(
            section.nodes[0].x, section.nodes[0].y, 0.01, 400, 300, (0, 0, 800, 600))
        self.assertEqual(len(layers), 1)
        self.assertEqual(len(layers[0].segments), len(section.nodes) - 1)
        self.assertTrue(all(t.section_id == second_id for t in targets))
        restored = Config('guide-test', self.task.default_config, folder=self.directory.name)
        self.assertEqual(restored['_Kuro route section'], second_id)

    def test_native_direction_arrow_pixels(self):
        import ctypes
        from ctypes import wintypes
        from ok.ui.overlay.win32_gdi import BITMAPINFO, GdiCanvas, gdi32
        from src.utils.PathRoute import PathLayer
        from src.utils.MapItemOverlay import MapItemOverlay
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(info.bmiHeader)
        info.bmiHeader.biWidth = 200
        info.bmiHeader.biHeight = -100
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        pixels = ctypes.c_void_p()
        dc = gdi32.CreateCompatibleDC(None)
        bitmap = gdi32.CreateDIBSection(dc, ctypes.byref(info), 0, ctypes.byref(pixels), None, 0)
        self.assertTrue(dc and bitmap)
        previous = gdi32.SelectObject(dc, bitmap)
        gdi32.GetPixel.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        gdi32.GetPixel.restype = wintypes.DWORD
        try:
            ctypes.memset(pixels, 0, 200 * 100 * 4)
            layer = PathLayer((40, 220, 180), ((20, 50), (180, 50)), ('a', 'b'))
            MapItemOverlay.make_paint_callback([], (layer,), draw_path_nodes=False)(GdiCanvas(dc, 1), None)
            color = 40 | (220 << 8) | (180 << 16)
            self.assertEqual(gdi32.GetPixel(dc, 60, 50), color)
            self.assertEqual(gdi32.GetPixel(dc, 95, 54), color)
            self.assertNotEqual(gdi32.GetPixel(dc, 105, 54), color)
        finally:
            gdi32.SelectObject(dc, previous)
            gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(dc)

    def test_minimap_target_guide_image(self):
        from ok import Box
        from src.utils.MinimapDirectionWindow import MinimapDirectionWindow
        window = MinimapDirectionWindow()
        window._guide_cache = GuideImageCache(str(Path(self.directory.name) / 'minimap'))
        try:
            window.render_direction((0, 0, 800, 600), 175, Box(20, 20, 150, 150),
                                    distance=1564, hint_text='该地点暂无描述', guide_target=(8, POINT_ID))
            self.wait_until(lambda: window._guide_status in ('ready', 'failed', 'no_image'))
            self.assertEqual(window._guide_status, 'ready')
            self.assertFalse(window._guide_pixmap.isNull())
            self.assertIn('完成能量矩阵解密获得', window._guide_description)
            self.app.processEvents()
            image = window.grab().toImage()
            self.assertGreater(image.pixelColor(100, 340).alpha(), 0)
            screenshot = os.environ.get('MINIMAP_GUIDE_SCREENSHOT')
            if screenshot:
                self.assertTrue(image.save(screenshot))
            window.hide_overlay()
            self.app.processEvents()
            self.assertIsNone(window._guide_target)
            self.assertIsNone(window._guide_pixmap)
        finally:
            window.close_overlay()
            self.app.processEvents()

    def test_bigmap_qt_visible_route_window(self):
        import ctypes
        from src.utils.KuroRoutes import KuroRoute
        from src.utils.PathRoute import build_path_layers
        from src.utils.MinimapDirectionWindow import BigmapLineWindow
        route = KuroRoute.from_data(KuroRoutesClient(8).detail('1452042731439439872')).path
        layers = build_path_layers(route, 8, -95000, 30000, 0.008, 400, 300)
        window = self.controller._create_minimap_direction_window(BigmapLineWindow)
        self.controller._bigmap_line_window = window
        window.render_routes((0, 0, 800, 600), (layers[0],))
        self.app.processEvents()
        self.assertTrue(window.isVisible())
        style = ctypes.windll.user32.GetWindowLongW(int(window.winId()), -20)
        self.assertTrue(style & 0x00000008)
        self.assertTrue(style & 0x00000020)
        image = window.grab().toImage()
        self.assertTrue(any(image.pixelColor(x, y).alpha() > 0
                            for x in range(800) for y in range(600)))
        screenshot = os.environ.get('BIGMAP_LINES_SCREENSHOT')
        if screenshot:
            self.assertTrue(image.save(screenshot))
        self.controller._hide_interaction_window()
        self.app.processEvents()
        self.assertFalse(window.isVisible())


if __name__ == '__main__':
    unittest.main()
