import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


from src.utils.wgc_compat import _defer_capture_until_hwnd


class TestWgcCompat(unittest.TestCase):
    def test_capture_selection_waits_for_game_hwnd(self):
        stale_capture = Mock()
        manager = SimpleNamespace(
            hwnd_window=SimpleNamespace(hwnd=0),
            capture_method=stale_capture,
        )

        self.assertTrue(_defer_capture_until_hwnd(manager))
        stale_capture.close.assert_called_once_with()
        self.assertIsNone(manager.capture_method)

        manager.hwnd_window.hwnd = 1234
        manager.capture_method = Mock()
        self.assertFalse(_defer_capture_until_hwnd(manager))

    @unittest.skipUnless(sys.platform == "win32", "WGC is a Windows-only capture API")
    def test_config_respects_upstream_wgc_platform_gate(self):
        # Importing config may apply stability fixes, but must not replace the
        # upstream capability check and force WGC on unsupported builds.
        import config  # noqa: F401
        import ok.util.window as window
        from ok.device.capture_methods.update import windows_graphics_available
        from src.utils.wgc_compat import enable_windows_graphics_capture

        expected = bool(window.windows_graphics_available())
        self.assertEqual(bool(windows_graphics_available()), expected)
        self.assertEqual(enable_windows_graphics_capture(), expected)

    @unittest.skipUnless(sys.platform == "win32", "native overlay is Windows-only")
    def test_native_overlay_uses_unowned_popup_compatibility(self):
        import config  # noqa: F401
        from ok.ui.overlay.win32_gdi import Win32GdiOverlay

        # The game HWND is retained only for source geometry/visibility.  The
        # popup itself must be unowned so CreateWindowExW works cross-process.
        self.assertTrue(getattr(Win32GdiOverlay, "_okww_owner_compat", False))

    @unittest.skipUnless(sys.platform == "win32", "WGC is a Windows-only capture API")
    def test_wgc_target_signature_ignores_top_hwnd_and_child_hwnds(self):
        import config  # noqa: F401
        from ok.device.capture_methods.hwnd_window import HwndWindow
        from src.utils.wgc_compat import _enable_wgc_deadlock_and_stability_compat

        self.assertTrue(_enable_wgc_deadlock_and_stability_compat())

        class MockWindow:
            hwnd = 1234
            top_hwnd = 5678
            hwnds = [(5678, 'Child', 100, 100, 0, 0)]
            width = 1920
            height = 1080
            client_width = 1920
            client_height = 1080
            real_x_offset = 0
            real_y_offset = 0
            real_width = 1920
            real_height = 1080

        sig = HwndWindow.capture_target_signature.fget(MockWindow())
        # Signature should only contain base hwnd and dimensions/offsets, never top_hwnd or child hwnds
        self.assertEqual(sig, (1234, 1920, 1080, 1920, 1080, 0, 0, 1920, 1080))
        self.assertNotIn(5678, sig)

    @unittest.skipUnless(sys.platform == "win32", "WGC is a Windows-only capture API")
    def test_wgc_close_releases_com_resources(self):
        import config  # noqa: F401
        import threading
        from ok.device.capture_methods.windows_graphics import WindowsGraphicsCaptureMethod
        from src.utils.wgc_compat import _enable_wgc_deadlock_and_stability_compat

        self.assertTrue(_enable_wgc_deadlock_and_stability_compat())
        self.assertTrue(getattr(WindowsGraphicsCaptureMethod, "_okww_stability_compat", False))

        class MockSession:
            closed = False
            def Close(self):
                self.closed = True

        class MockFramePool:
            closed = False
            def Close(self):
                self.closed = True

        mock_capture = WindowsGraphicsCaptureMethod.__new__(WindowsGraphicsCaptureMethod)
        mock_capture.lock = threading.RLock()
        mock_capture.get_frame_lock = threading.Lock()
        mock_capture._frame_cancel_generation = 0
        mock_capture.frame_requested = threading.Event()
        mock_capture.frame_event = threading.Event()
        mock_capture.frame_pool = MockFramePool()
        mock_capture.session = MockSession()
        mock_capture.item = object()
        mock_capture.rtdevice = None
        mock_capture.dxdevice = None
        mock_capture.immediatedc = None
        mock_capture.cputex = None
        mock_capture.contexts = {}
        mock_capture.capture_hwnd = 1234
        mock_capture.capture_target_signature = (1234,)

        pool = mock_capture.frame_pool
        sess = mock_capture.session

        mock_capture.close()

        self.assertTrue(pool.closed)
        self.assertTrue(sess.closed)
        self.assertIsNone(mock_capture.frame_pool)
        self.assertIsNone(mock_capture.session)
        self.assertEqual(mock_capture.capture_hwnd, 0)

    @unittest.skipUnless(sys.platform == "win32", "PostMessage is a Windows-only interaction API")
    def test_post_message_foreground_compat_suppresses_wm_activate_and_guards_background_input(self):
        from unittest.mock import patch
        import win32con
        from ok.device.interaction_methods.post_message import PostMessageInteraction
        from src.utils.wgc_compat import _enable_post_message_foreground_compat

        self.assertTrue(_enable_post_message_foreground_compat())
        self.assertTrue(getattr(PostMessageInteraction, "_okww_foreground_compat", False))

        class FakeHwndWindow:
            def __init__(self, foreground=True, bring_succeeds=False):
                self.hwnd = 1001
                self.top_hwnd = 1001
                self.foreground = foreground
                self.bring_succeeds = bring_succeeds
                self.bring_calls = 0

            def is_foreground(self):
                return self.foreground

            def bring_to_front(self):
                self.bring_calls += 1
                if self.bring_succeeds:
                    self.foreground = True
                return self.bring_succeeds

        fg_window = FakeHwndWindow(foreground=True)
        interaction = PostMessageInteraction(Mock(), fg_window)

        with patch("win32gui.PostMessage") as mock_post:
            self.assertTrue(interaction.try_activate())
            self.assertEqual(fg_window.bring_calls, 0)
            mock_post.assert_not_called()

            interaction.post(win32con.WM_ACTIVATE, win32con.WA_ACTIVE, 0)
            mock_post.assert_not_called()

            interaction.post(win32con.WM_KEYDOWN, 0x45, 0x1E0001)
            mock_post.assert_called_once_with(1001, win32con.WM_KEYDOWN, 0x45, 0x1E0001)

        bg_window = FakeHwndWindow(foreground=False, bring_succeeds=False)
        bg_interaction = PostMessageInteraction(Mock(), bg_window)
        with patch("win32gui.PostMessage") as mock_post, patch("time.sleep"):
            bg_interaction.post(win32con.WM_KEYDOWN, 0x45, 0x1E0001)
            self.assertEqual(bg_window.bring_calls, 1)
            mock_post.assert_not_called()

            bg_interaction.post(win32con.WM_KEYUP, 0x45, 0xC01E0001)
            mock_post.assert_called_once_with(1001, win32con.WM_KEYUP, 0x45, 0xC01E0001)

        recover_window = FakeHwndWindow(foreground=False, bring_succeeds=True)
        recover_interaction = PostMessageInteraction(Mock(), recover_window)
        with patch("win32gui.PostMessage") as mock_post, patch("time.sleep"):
            recover_interaction.post(win32con.WM_KEYDOWN, 0x45, 0x1E0001)
            self.assertEqual(recover_window.bring_calls, 1)
            mock_post.assert_called_once_with(1001, win32con.WM_KEYDOWN, 0x45, 0x1E0001)


if __name__ == "__main__":
    unittest.main()

