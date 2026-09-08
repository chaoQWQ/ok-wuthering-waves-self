"""Local stability fixes for Windows Graphics Capture and the native overlay.

The installed ``ok-script`` release intentionally decides which Windows
builds should use WGC.  Keep that platform gate intact: forcing WGC on older
builds can wedge frame capture while the game's transient login windows are
being destroyed.  This module only patches WGC after the upstream capability
check succeeds.
"""

from __future__ import annotations


def _enable_overlay_owner_compat() -> bool:
    """Make the native overlay a top-level window on Windows.

    ``ok-script`` passes the game's Unreal ``HWND`` as the owner of its
    overlay.  On some Windows 10/DPI combinations a cross-process owner makes
    ``CreateWindowExW`` fail (the overlay then reports ``hwnd=0`` and can never
    paint).  The overlay is already positioned with ``HWND_TOPMOST`` and is
    input-transparent, so it does not need an owner relationship.  Keep the
    source window for geometry/visibility synchronisation, but create the
    overlay as an unowned top-level popup instead.

    This only changes the presentation window relationship; it does not access
    or modify the game process, memory, input hooks, or game files.
    """

    try:
        from ok.ui.overlay.win32_gdi import Win32GdiOverlay
    except Exception:
        return False

    if getattr(Win32GdiOverlay, "_okww_owner_compat", False):
        return True

    original_init = Win32GdiOverlay.__init__

    def owner_compat_init(self, hwnd_window=None, *args, **kwargs):
        # Pass no owner to CreateWindowExW.  Sync the original source after the
        # native window is ready so geometry/visibility behaviour is unchanged.
        original_init(self, None, *args, **kwargs)
        if hwnd_window is not None:
            self.sync_source_window(hwnd_window)

    Win32GdiOverlay.__init__ = owner_compat_init
    Win32GdiOverlay._okww_owner_compat = True
    return True


def _enable_wgc_deadlock_and_stability_compat() -> bool:
    """Prevent WinRT frame pool deadlock on close and redundant capture resets.

    1. WinRT's ``Direct3D11CaptureFramePool.Close()`` waits synchronously for
       in-flight callbacks on the frame arrival thread to complete.  Closing
       it while holding ``self.lock`` causes a mutual deadlock if
       ``frame_arrived_callback`` is waiting for ``self.lock``.  Releasing the
       COM resources outside ``self.lock`` prevents this hang.
    2. WGC captures the base Unreal window HWND directly.  Child or popup HWNDs
       (such as combo boxes or dialog overlays) changing on top of the game
       window do not require destroying and recreating the WGC session.
    """
    try:
        from ok.device.capture_methods.windows_graphics import (
            WindowsGraphicsCaptureMethod,
            clean_up_bitblt,
            logger,
        )
        from ok.device.capture_methods.hwnd_window import HwndWindow
    except Exception:
        return False

    if getattr(WindowsGraphicsCaptureMethod, "_okww_stability_compat", False):
        return True

    @property
    def base_capture_target_signature(self):
        return (
            self.hwnd,
            self.width,
            self.height,
            self.client_width,
            self.client_height,
            self.real_x_offset,
            self.real_y_offset,
            self.real_width,
            self.real_height,
        )

    HwndWindow.capture_target_signature = base_capture_target_signature

    def safe_close(self):
        with self.lock:
            self._frame_cancel_generation += 1
            self.frame_requested.clear()
            self.frame_event.set()
        with self.get_frame_lock:
            with self.lock:
                logger.info('destroy windows capture')
                self.frame_requested.clear()
                self.frame_event.set()
                frame_pool = self.frame_pool
                session = self.session
                self.frame_pool = None
                self.session = None
                self.item = None
                if self.rtdevice:
                    self.rtdevice.Release()
                    self.rtdevice = None
                if self.dxdevice:
                    self.dxdevice.Release()
                    self.dxdevice = None
                if self.immediatedc:
                    self.immediatedc.Release()
                    self.immediatedc = None
                if self.cputex:
                    self.cputex.Release()
                    self.cputex = None
                for context in self.contexts.values():
                    clean_up_bitblt(context)
                self.contexts.clear()
                self.capture_hwnd = 0
                self.capture_target_signature = None

            if session is not None:
                try:
                    session.Close()
                except Exception:
                    pass
            if frame_pool is not None:
                try:
                    frame_pool.Close()
                except Exception:
                    pass

    WindowsGraphicsCaptureMethod.close = safe_close
    WindowsGraphicsCaptureMethod._okww_stability_compat = True
    return True


def enable_windows_graphics_capture() -> bool:
    """Apply compatibility fixes when upstream says WGC is supported."""

    try:
        import sys

        if sys.platform != "win32":
            return False

        import ok.util.window as window

        # The minimap uses ok-script's native GDI overlay. Apply this before
        # the application constructs that overlay, independently of WGC.
        _enable_overlay_owner_compat()
        available = bool(window.windows_graphics_available())
        if available:
            _enable_wgc_deadlock_and_stability_compat()
            window.logger.info(f"WGC stability compatibility enabled for Windows build {window.WINDOWS_BUILD_NUMBER}")
        return available
    except Exception:
        return False
