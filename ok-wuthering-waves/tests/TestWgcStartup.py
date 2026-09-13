import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.utils.wgc_startup import _recently_verified, _verify_frames, enable_wgc_startup_compat


class TestWgcStartup(unittest.TestCase):
    def capture(self, frames):
        return SimpleNamespace(exit_event=threading.Event(), connected=lambda: True,
                               get_frame=Mock(side_effect=frames), frame_pool=object(),
                               capture_target_signature=(123, 1920, 1080))

    def test_delayed_first_frame_is_retried_without_discarding_session(self):
        capture = self.capture([None, object()])
        self.assertTrue(_verify_frames(capture, Mock()))
        self.assertEqual(2, capture.get_frame.call_count)
        self.assertTrue(_recently_verified(capture))

    def test_fresh_validation_reused_only_for_same_live_pool_and_signature(self):
        capture = self.capture([object()])
        with patch('src.utils.wgc_startup.time.monotonic', return_value=100):
            self.assertTrue(_verify_frames(capture, Mock()))
        with patch('src.utils.wgc_startup.time.monotonic', return_value=102):
            self.assertTrue(_recently_verified(capture))
            capture.capture_target_signature = (456, 1920, 1080)
            self.assertFalse(_recently_verified(capture))
            capture.capture_target_signature = (123, 1920, 1080)
            capture.frame_pool = object()
            self.assertFalse(_recently_verified(capture))

    def test_old_validation_does_not_mask_a_stalled_capture(self):
        capture = self.capture([object()])
        with patch('src.utils.wgc_startup.time.monotonic', return_value=100):
            _verify_frames(capture, Mock())
        with patch('src.utils.wgc_startup.time.monotonic', return_value=106):
            self.assertFalse(_recently_verified(capture))

    def test_cancellation_stops_retry(self):
        capture = self.capture([])
        capture.exit_event.set()
        self.assertFalse(_verify_frames(capture, Mock()))
        capture.get_frame.assert_not_called()

    def test_failed_probe_is_bounded(self):
        capture = self.capture([None, None, object()])
        self.assertFalse(_verify_frames(capture, Mock()))
        self.assertEqual(2, capture.get_frame.call_count)

    def test_selector_reuses_success_but_falls_back_after_real_failure(self):
        from ok.device.capture_methods import update
        enable_wgc_startup_compat()
        capture = self.capture([object(), None, None])
        capture.start_or_stop = lambda: True
        capture.get_capture_hwnd = lambda: 123
        capture.close = Mock()
        with patch.object(update, 'windows_graphics_available', return_value=True), \
                patch.object(update, 'get_capture', return_value=capture):
            with patch('src.utils.wgc_startup.time.monotonic', return_value=100):
                self.assertIs(capture, update.get_win_graphics_capture(None, None, capture.exit_event))
            with patch('src.utils.wgc_startup.time.monotonic', return_value=102):
                self.assertIs(capture, update.get_win_graphics_capture(capture, None, capture.exit_event))
                self.assertEqual(1, capture.get_frame.call_count)
                capture.close.assert_not_called()
            with patch('src.utils.wgc_startup.time.monotonic', return_value=106):
                self.assertIsNone(update.get_win_graphics_capture(capture, None, capture.exit_event))
            capture.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
