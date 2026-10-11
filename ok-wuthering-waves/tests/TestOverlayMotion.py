import time
import unittest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.utils.InteractionOverlayWindow import InteractionOverlayWindow
from src.utils.MinimapDirectionWindow import BigmapLineWindow
from src.utils.OverlayMotion import transform_overlay
from src.utils.PathRoute import PathLayer
from src.utils.ViewStateEstimator import ViewStateEstimator
from src.utils.overlay_types import Rect


class TestOverlayMotion(unittest.TestCase):
    def test_pan_zoom_preserves_hit_regions_and_route_projection(self):
        items = [(100, 100, None, 'chest', None, 1.0, 'id', 0)]
        boxes = [(Rect(85, 85, 115, 115), 0)]
        layers = (PathLayer((255, 0, 0), ((100, 100), (200, 100)), ('id', 'next')),)
        projected, masks, marker, routes = transform_overlay(
            items, boxes, (100, 100), layers, (0, 0), 10, (-100, 0), 5, (400, 300))
        self.assertEqual(projected[0][:2], (20, 50))
        self.assertEqual(marker, (20, 50))
        self.assertEqual(masks[0][0], Rect(5, 35, 35, 65))
        self.assertEqual(routes[0].points, ((20, 50), (220, 50)))
        self.assertEqual(routes[0].node_ids, ('id', 'next'))
        with self.assertRaises(ValueError):
            transform_overlay(items, boxes, None, layers, (0, 0), 10, (0, 0), 0, (400, 300))

    def test_real_windows_follow_drag_without_detection_frame(self):
        app = QApplication.instance() or QApplication([])
        window = InteractionOverlayWindow()
        lines = BigmapLineWindow()
        estimator = ViewStateEstimator()
        estimator.on_match((0, 0), 10)
        layers = (PathLayer((255, 0, 0), ((200, 150), (300, 150)), ('id', 'next')),)
        window.setGeometry(100, 100, 400, 300)
        window.show()
        try:
            window.render_frame([(200, 150, None, 'chest', None, 1.0, 'id', 0)],
                                hitboxes=[(Rect(185, 135, 215, 165), 0)],
                                target_marker=(200, 150),
                                viewport=(estimator, (0, 0), 10, lines, layers))
            QTest.qWait(60)
            now = time.monotonic()
            estimator.on_mouse_press(200, 150, 'left', now)
            estimator.on_mouse_drag(240, 150, 40, 0, ('left',), now + 0.01)
            QTest.qWait(80)
            self.assertEqual(window._draw_items[0][:2], (240, 150))
            self.assertEqual(window._target_marker, (240, 150))
            self.assertEqual(window._hitboxes_with_z[0][0], Rect(225, 135, 255, 165))
            self.assertEqual(lines._path_layers[0].points[0], (240, 150))
            self.assertTrue(lines.windowFlags() & Qt.WindowTransparentForInput)
            self.assertTrue(window.isVisible())
            estimator.on_mouse_scroll(200, 150, 0, 1, time.monotonic())
            QTest.qWait(80)
            state = estimator.state()
            expected_x = round(200 - state.center[0] / state.game_scale)
            self.assertEqual(window._draw_items[0][:2], (expected_x, 150))
            self.assertEqual(lines._path_layers[0].points[0], (expected_x, 150))
            self.assertEqual(window._hitboxes_with_z[0][0],
                             Rect(expected_x - 15, 135, expected_x + 15, 165))
            window.hide_overlay()
            QTest.qWait(60)
            self.assertFalse(window.isVisible())
            self.assertFalse(window._motion_timer.isActive())
        finally:
            window.close()
            lines.close()
            app.processEvents()

    def test_latest_frame_and_expired_tracking(self):
        app = QApplication.instance() or QApplication([])
        window = InteractionOverlayWindow()
        estimator = ViewStateEstimator()
        estimator.on_match((0, 0), 10)
        window.setGeometry(0, 0, 400, 300)
        window.show()
        try:
            for index in range(100):
                window.render_frame([(index, 150, None, 'chest', None, 1, 'id', 0)])
            app.processEvents()
            self.assertEqual(window._draw_items[0][0], 99)
            window.hide_overlay()
            window.show_overlay()
            window.render_frame([(123, 150, None, 'chest', None, 1, 'id', 0)])
            app.processEvents()
            self.assertTrue(window.isVisible())
            self.assertEqual(window._draw_items[0][0], 123)
            window.render_frame([(200, 150, None, 'chest', None, 1, 'id', 0)],
                                viewport=(estimator, (0, 0), 10, None, ()))
            app.processEvents()
            window.render_frame([(200, 150, None, 'chest', None, 1, 'id', 0)], viewport=False)
            app.processEvents()
            self.assertFalse(window._motion_timer.isActive())
            window.render_frame([(200, 150, None, 'chest', None, 1, 'id', 0)],
                                viewport=(estimator, (0, 0), 10, None, ()))
            app.processEvents()
            estimator.invalidate('测试结束')
            QTest.qWait(70)
            self.assertFalse(window.isVisible())
        finally:
            window.close()
            app.processEvents()


if __name__ == '__main__':
    unittest.main()
