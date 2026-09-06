"""Windows Graphics Capture compatibility for supported Windows 10 builds.

The installed ``ok-script`` release exposes two separate Windows build gates:
one for WGC itself and one for the newer ``IsBorderRequired`` API.  Its
availability helper currently uses the latter gate for both, which disables
WGC on Windows 10 19041+ even though the basic capture API is available.

This module only adjusts the capability check.  The capture implementation
already guards the optional border API, so no input, process, or game state
operations are added here.
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
    """Enable the basic WGC capability check on supported Windows builds.

    Returns ``True`` when the host build and WinRT activation interface are
    available.  On non-Windows hosts, or when the WinRT interface cannot be
    loaded, this returns ``False`` without changing the installed package.
    """

    try:
        import sys

        if sys.platform != "win32":
            return False

        import ok.util.window as window

        # The minimap uses ok-script's native GDI overlay.  Apply this before
        # the application constructs that overlay; the big-map interaction
        # window is independent and needs no change.
        _enable_overlay_owner_compat()
        _enable_wgc_deadlock_and_stability_compat()

        build = window.WINDOWS_BUILD_NUMBER
        minimum_build = getattr(window, "WGC_MIN_BUILD", 19041)
        if build < minimum_build:
            return False

        def wgc_available() -> bool:
            try:
                from ok.rotypes import idldsl  # noqa: F401 - activates WinRT type support
                from ok.rotypes.roapi import GetActivationFactory
                from ok.rotypes.Windows.Graphics.Capture import IGraphicsCaptureItemInterop

                GetActivationFactory("Windows.Graphics.Capture.GraphicsCaptureItem").astype(
                    IGraphicsCaptureItemInterop
                )
                return True
            except Exception as exc:
                window.logger.error(f"check available failed: {exc}", exception=exc)
                return False

        # DeviceManager imports this helper lazily during application start;
        # patching the module attribute is enough for that path.  Patch the
        # already-imported capture selector as well for embedded/debug starts.
        window.windows_graphics_available = wgc_available
        try:
            import ok.device.capture_methods.update as capture_update

            capture_update.windows_graphics_available = wgc_available
        except Exception:
            pass
        available = wgc_available()
        if available:
            window.logger.info(
                f"WGC compatibility enabled for Windows build {build} "
                f"(minimum basic build {minimum_build})"
            )
        return available
    except Exception:
        return False
