import os
import threading
import time

import pytest


def test_empty_winrt_capture_frame_is_a_null_com_object():
    from ok.rotypes.Windows.Graphics.Capture import Direct3D11CaptureFrame

    frame = Direct3D11CaptureFrame()
    assert frame is not None
    assert not frame


@pytest.mark.skipif(os.environ.get('QT_QPA_PLATFORM') != 'windows', reason='需要真实 Windows WGC 窗口')
def test_native_capture_survives_empty_notifications_minimize_and_resize(tmp_path):
    import win32gui
    from pathlib import Path
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from ok.util.handler import ExitEvent
    from src.workflow.diagnostics import RecordingDiagnostics
    from src.workflow.recorder import WindowsGameplaySource
    from tools.verify_gameplay_runtime import NativeCaptureWindow
    from tools.verify_recording_diagnostics import DiagnosticWindow

    app = QApplication.instance() or QApplication([])
    root = Path(__file__).resolve().parents[1]
    window = DiagnosticWindow(root / 'tests/images/quest_yangyang_interaction.png')
    window.show()
    QTest.qWait(200)
    exit_event = ExitEvent()
    source = WindowsGameplaySource(NativeCaptureWindow(int(window.winId()), exit_event))
    source.set_diagnostics(RecordingDiagnostics(tmp_path, 'native-lifecycle', 'capture'))
    stopping = threading.Event()
    samples, errors = [], []

    def capture_frames():
        try:
            while not stopping.is_set():
                frame = source.frame()
                samples.append((time.monotonic(), None if frame is None else frame.shape[:2]))
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=capture_frames, name='NativeLifecycleCapture')

    def wait_for_frames(count, dimensions, timeout=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if errors:
                raise errors[0]
            if sum(shape == dimensions for _, shape in samples) >= count:
                return
            QTest.qWait(30)
        raise AssertionError(f'窗口截图没有恢复至所需尺寸：{dimensions}；实际记录：{samples}')

    try:
        # 真实 frame pool 的重复通知允许没有可以取得的 frame。
        for _ in range(20):
            source.capture.frame_arrived_callback()
        thread.start()
        dimensions = tuple(reversed(win32gui.GetClientRect(int(window.winId()))[2:]))
        wait_for_frames(2, dimensions)
        window.showMinimized()
        minimized_at = time.monotonic()
        while time.monotonic() - minimized_at < 12:
            if errors:
                raise errors[0]
            if any(timestamp >= minimized_at and shape is None for timestamp, shape in samples):
                break
            QTest.qWait(30)
        assert any(timestamp >= minimized_at and shape is None for timestamp, shape in samples), samples
        window.showNormal()
        QTest.qWait(100)
        window.resize(960, 540)
        QTest.qWait(100)
        resized = tuple(reversed(win32gui.GetClientRect(int(window.winId()))[2:]))
        wait_for_frames(2, resized)
        window.resize(1280, 720)
        QTest.qWait(100)
        restored = tuple(reversed(win32gui.GetClientRect(int(window.winId()))[2:]))
        previous = sum(shape == restored for _, shape in samples)
        wait_for_frames(previous + 2, restored)
        assert source.callback_failure is None
        assert source.pending_callback_errors == []
    finally:
        stopping.set()
        source.close()
        if thread.ident is not None:
            thread.join(timeout=6)
            assert not thread.is_alive()
        window.timer.stop()
        window.close()
        app.processEvents()
        exit_event.set()
    if errors:
        raise errors[0]
