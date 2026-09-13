"""Avoid destroying a just-verified WGC session during repeated device setup."""
import threading
import time


_selection_lock = threading.RLock()


def _verify_frames(capture, logger, attempts=2):
    # get_frame itself has an interruptible four-second wait. Count bounded
    # attempts rather than advertise a 1.5s deadline around that blocking call.
    for attempt in range(1, attempts + 1):
        if capture.exit_event.is_set() or not capture.connected():
            return False
        try:
            if capture.get_frame() is not None:
                capture._okww_verified_pool = capture.frame_pool
                capture._okww_verified_signature = capture.capture_target_signature
                capture._okww_verified_at = time.monotonic()
                return True
        except Exception as error:
            logger.warning(f'WGC startup frame error: {error}')
            return False
        logger.warning(f'WGC startup received no frame (attempt {attempt}/{attempts})')
    return False


def _recently_verified(capture):
    return (
        capture.frame_pool is not None
        and getattr(capture, '_okww_verified_pool', None) is capture.frame_pool
        and getattr(capture, '_okww_verified_signature', None) == capture.capture_target_signature
        and time.monotonic() - getattr(capture, '_okww_verified_at', float('-inf')) < 5
        and capture.connected()
        and not capture.exit_event.is_set()
    )


def enable_wgc_startup_compat():
    from ok.device.capture_methods import update
    from ok.device.DeviceManager import DeviceManager

    if getattr(update, '_okww_startup_compat', False):
        return

    def get_win_graphics_capture(capture_method, hwnd, exit_event):
        if not update.windows_graphics_available():
            return None
        capture = update.get_capture(capture_method, update.WindowsGraphicsCaptureMethod, hwnd, exit_event)
        if capture.start_or_stop():
            if _recently_verified(capture):
                update.logger.info('reuse recently verified WGC session for unchanged window')
                return capture
            if _verify_frames(capture, update.logger):
                return capture
        capture_hwnd = capture.get_capture_hwnd()
        if capture_hwnd:
            capture.last_start_failure_key = capture_hwnd
            capture.last_start_failure_time = time.time()
        update.logger.warning('WGC startup unavailable after validation; trying configured fallback')
        capture.close()
        return None

    original_use_windows_capture = DeviceManager.use_windows_capture

    def serialized_use_windows_capture(self):
        # Keep method creation, validation AND publication to DeviceManager
        # together. A selector-only lock would still permit stale assignments.
        with _selection_lock:
            return original_use_windows_capture(self)

    update.get_win_graphics_capture = get_win_graphics_capture
    DeviceManager.use_windows_capture = serialized_use_windows_capture
    update._okww_startup_compat = True
