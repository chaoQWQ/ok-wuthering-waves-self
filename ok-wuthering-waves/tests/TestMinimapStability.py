import unittest

from ok import Box
from PySide6.QtWidgets import QApplication

from src.utils.ChestGuidanceFilter import ChestGuidanceFilter
from src.utils.MinimapDirectionWindow import MinimapDirectionWindow


class TestMinimapStability(unittest.TestCase):
    def test_stationary_and_rejected_samples_preserve_bearing(self):
        guidance = ChestGuidanceFilter()
        guidance.update((0, 0), 'chest', 5000, 0, now=0)
        moving = guidance.update((0, 10), 'chest', 5000, 0, now=0.1)
        for position in ((0, 10), (2, 10), (999, 999)):
            stationary = guidance.update(
                position, 'chest', 5000, 0,
                movement_active=False, now=3,
            )
            self.assertEqual(moving.bearing, stationary.bearing)
            self.assertEqual(moving.distance, stationary.distance)

    def test_nearby_boundary_has_separate_exit_threshold(self):
        guidance = ChestGuidanceFilter()
        self.assertTrue(guidance.update((7, 0), 'chest', 0, 0).nearby)
        for _ in range(30):
            sample = guidance.update((9, 0), 'chest', 0, 0)
            self.assertTrue(sample.nearby)
        for _ in range(30):
            sample = guidance.update((14, 0), 'chest', 0, 0)
        self.assertFalse(sample.nearby)
        self.assertFalse(guidance.update((9, 0), 'other', 0, 0).nearby)

    def test_bearing_crosses_north_without_full_rotation(self):
        guidance = ChestGuidanceFilter()
        first = guidance.update((1, 0), 'chest', 0, -5000)
        for _ in range(20):
            sample = guidance.update((-1, 0), 'chest', 0, -5000)
            delta = (sample.bearing - first.bearing + 180) % 360 - 180
            self.assertLess(abs(delta), 5)

    def test_real_qt_window_preserves_handle_and_frame(self):
        app = QApplication.instance() or QApplication([])
        window = MinimapDirectionWindow()
        box = Box(20, 20, 150, 150)
        try:
            window.render_direction((0, 0, 640, 400), 90, box, 1600)
            app.processEvents()
            self.assertTrue(window.isVisible())
            handle = int(window.winId())
            image = window.grab().toImage()
            for _ in range(20):
                window.render_direction((0, 0, 640, 400), 90, box, 1600)
                app.processEvents()
                self.assertTrue(window.isVisible())
                self.assertEqual(handle, int(window.winId()))
                self.assertEqual(image, window.grab().toImage())
            window.render_direction((0, 0, 640, 400), 180, box, 1600)
            app.processEvents()
            self.assertNotEqual(image, window.grab().toImage())
            window.hide_overlay()
            app.processEvents()
            self.assertFalse(window.isVisible())
            window.render_direction((0, 0, 640, 400), 180, box, 1600)
            app.processEvents()
            self.assertTrue(window.isVisible())
        finally:
            window.close()
            window.deleteLater()
            app.processEvents()


if __name__ == '__main__':
    unittest.main()
