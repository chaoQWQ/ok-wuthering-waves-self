import argparse
import ctypes
import json
import os
import threading
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'windows')

import win32api
import win32con
import win32gui
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from src.workflow.input import WorkflowStopped
from src.workflow.input_capture import WindowsInputCapture
from src.workflow.playback import KEY_CODES, NativeSequenceInput, SequenceWatchdog, check_sample


def transitions(samples):
    result = []
    for timestamp, sample in samples:
        key = sample.get('key_event')
        if key:
            result.append((key['key'], key['pressed'], timestamp))
        flags = sample.get('mouse', {}).get('button_flags', 0)
        if flags & 4:
            result.append(('right', True, timestamp))
        if flags & 8:
            result.append(('right', False, timestamp))
    return result


def durations(samples):
    started, result = {}, {}
    for key, pressed, timestamp in transitions(samples):
        if pressed:
            if key in started:
                raise AssertionError(f'{key} 出现重复按下事件')
            started[key] = timestamp
        else:
            result[key] = timestamp - started.pop(key)
    if started:
        raise AssertionError('实际 Windows 记录中仍有未释放的输入')
    return result


def write_samples(path, samples):
    path.write_text(json.dumps({'source': '真实 Qt 验证窗口的 Windows Raw Input',
                                'samples': [{'timestamp': timestamp, 'payload': sample}
                                            for timestamp, sample in samples]},
                               ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    output = (args.output_dir or Path('working_images') / f'workflow_playback_{time.time_ns()}').resolve()
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    window = QWidget()
    window.setWindowTitle('轨迹复现验证：独立 Windows 窗口')
    window.setWindowFlag(Qt.WindowStaysOnTopHint)
    layout = QVBoxLayout(window)
    layout.addWidget(QLabel('正在验证实际 Tab、T、W、鼠标轨迹与右键输入。'))
    layout.addWidget(QLabel('本窗口用于验证 Windows 输入传递与释放。'))
    window.resize(640, 420)
    window.show()
    window.activateWindow()
    window.raise_()
    QTest.qWait(200)
    hwnd = int(window.winId())
    cursor = win32api.GetCursorPos()
    center = win32gui.ClientToScreen(hwnd, (300, 200))
    if win32gui.GetAncestor(win32gui.WindowFromPoint(center), win32con.GA_ROOT) != hwnd:
        window.close()
        raise RuntimeError('验证窗口没有显示在指定点击位置')
    if any(win32api.GetAsyncKeyState(code) & 0x8000 for code in (*KEY_CODES.values(), 1, 2)):
        window.close()
        raise RuntimeError('验证需要所有相关按键和鼠标按钮保持释放')
    capture = WindowsInputCapture(hwnd)
    stopped = threading.Event()
    hidden_calls = 0

    def check():
        if stopped.is_set():
            raise WorkflowStopped('验证停止事件已经设置')
        if win32gui.GetForegroundWindow() != hwnd or not win32gui.IsWindowVisible(hwnd):
            raise WorkflowStopped('独立验证窗口失去前台')

    sink = NativeSequenceInput(hwnd, check)

    def key(name, pressed):
        check()
        win32api.keybd_event(KEY_CODES[name], 0, 0 if pressed else win32con.KEYEVENTF_KEYUP, 0)

    def begin():
        capture.enabled.clear()
        QTest.qWait(50)
        capture.drain(time.monotonic())
        capture.enabled.set()
        QTest.qWait(80)

    def end():
        QTest.qWait(80)
        # 让独立的 Raw Input Python 线程完成已经派发的 Windows 消息。
        time.sleep(.05)
        samples = capture.drain(time.monotonic())
        capture.enabled.clear()
        if not samples:
            raise AssertionError('没有取得真实 Raw Input 记录')
        return samples

    def play(samples):
        maximum_cursor_error = 0
        started = time.monotonic()
        first = samples[0][0]
        for timestamp, sample in samples:
            target = started + timestamp - first
            while time.monotonic() < target:
                check()
                QTest.qWait(1)
            sink.apply(sample)
            if sample['cursor_visible']:
                width, height = sample['client_size']
                actual = win32gui.ScreenToClient(hwnd, win32api.GetCursorPos())
                expected = (round(sample['cursor'][0] * width), round(sample['cursor'][1] * height))
                maximum_cursor_error = max(maximum_cursor_error, *(abs(a - b) for a, b in zip(actual, expected)))
        sink.release()
        return maximum_cursor_error

    def assert_released():
        QTest.qWait(50)
        held = [name for name, code in KEY_CODES.items() if win32api.GetAsyncKeyState(code) & 0x8000]
        if held or win32api.GetAsyncKeyState(2) & 0x8000 or sink.keys or sink.buttons:
            raise AssertionError(f'实际 Windows 输入仍然处于按下状态：{held}')

    try:
        win32api.SetCursorPos(center)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        QTest.qWait(100)
        check()
        capture.start()
        begin()
        key('tab', True)
        QTest.qWait(220)
        for index in range(12):
            check()
            win32api.SetCursorPos(win32gui.ClientToScreen(hwnd, (300 + index * 6, 200 + index * 3)))
            QTest.qWait(20)
        QTest.qWait(100)
        key('tab', False)
        QTest.qWait(100)
        key('t', True)
        QTest.qWait(60)
        key('t', False)
        QTest.qWait(100)
        key('w', True)
        QTest.qWait(300)
        check()
        win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
        QTest.qWait(100)
        win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
        QTest.qWait(100)
        key('w', False)
        original = end()
        write_samples(output / 'original.json', original)
        for _, sample in original:
            check_sample(sample)
        expected = [('tab', True), ('tab', False), ('t', True), ('t', False),
                    ('w', True), ('right', True), ('right', False), ('w', False)]
        if [(key, pressed) for key, pressed, _ in transitions(original)] != expected:
            raise AssertionError('原始 Windows 输入顺序与实际发送的操作不符')
        begin()
        cursor_error = play(original)
        replayed = end()
        write_samples(output / 'replayed.json', replayed)
        if [(key, pressed) for key, pressed, _ in transitions(replayed)] != expected:
            raise AssertionError(f'复现记录中的 Windows 按下释放顺序不符：{transitions(replayed)}；材料：{output}')
        original_durations, replayed_durations = durations(original), durations(replayed)
        differences = {key: abs(value - replayed_durations[key]) for key, value in original_durations.items()}
        if max(differences.values()) > .06 or cursor_error > 1:
            raise AssertionError(f'实际输入持续时间或光标位置不符：{differences}, {cursor_error}')
        assert_released()
        begin()
        held_sample = next(sample for _, sample in original if 'w' in sample['keys'] and 'right' in sample['buttons'])
        sink.apply(held_sample)
        QTest.qWait(60)
        if not (win32api.GetAsyncKeyState(KEY_CODES['w']) & 0x8000 and win32api.GetAsyncKeyState(2) & 0x8000):
            raise AssertionError('停止验证前的实际 W 与右键没有按下')
        stopped.set()
        try:
            sink.apply(original[-1][1])
        except WorkflowStopped:
            pass
        else:
            raise AssertionError('实际停止事件没有终止输入')
        finally:
            sink.release()
        assert_released()
        canceled = end()
        write_samples(output / 'canceled.json', canceled)
        if [(key, pressed) for key, pressed, _ in transitions(canceled)] != [
                ('w', True), ('right', True), ('w', False), ('right', False)]:
            raise AssertionError('停止之后没有记录到实际 Windows 释放事件')
        stopped.clear()
        begin()
        guarded = NativeSequenceInput(hwnd, check)
        watchdog = SequenceWatchdog(guarded, stopped.is_set, time.monotonic() + 5)
        watchdog_running = False
        try:
            guarded.apply(held_sample)
            QTest.qWait(60)
            if not (win32api.GetAsyncKeyState(KEY_CODES['w']) & 0x8000
                    and win32api.GetAsyncKeyState(2) & 0x8000):
                raise AssertionError('输入监护验证前的实际 W 与右键没有按下')
            watchdog_started = time.monotonic()
            watchdog.pulse()
            watchdog.start()
            watchdog_running = True
            # 主线程停止发送 pulse，验证监护线程能够独立释放实际输入。
            time.sleep(.35)
            watchdog.close()
            assert_released()
            if guarded.keys or guarded.buttons or not isinstance(guarded.error, WorkflowStopped):
                raise AssertionError('输入监护线程没有终止持续输入并保存异常')
            with_error = False
            try:
                guarded.apply(held_sample)
            except WorkflowStopped:
                with_error = True
            if not with_error:
                raise AssertionError('输入监护终止后仍然能够重新按下输入')
        finally:
            if watchdog_running:
                watchdog.close()
            guarded.release()
        watchdog_samples = end()
        write_samples(output / 'watchdog.json', watchdog_samples)
        if [(key, pressed) for key, pressed, _ in transitions(watchdog_samples)] != [
                ('w', True), ('right', True), ('w', False), ('right', False)]:
            raise AssertionError('输入监护终止后没有记录到实际 Windows 释放事件')
        watchdog_release_seconds = max(timestamp for _, pressed, timestamp in transitions(watchdog_samples)
                                       if not pressed) - watchdog_started
        if not .1 <= watchdog_release_seconds < .4:
            raise AssertionError(f'输入监护的实际释放时间异常：{watchdog_release_seconds}')
        win32api.SetCursorPos(center)
        while win32gui.GetCursorInfo()[0] & 1:
            ctypes.windll.user32.ShowCursor(False)
            hidden_calls += 1
            if hidden_calls > 10:
                raise RuntimeError('无法确认 Windows 光标隐藏状态')
        begin()
        for _ in range(8):
            check()
            win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, 6, 2, 0, 0)
            QTest.qWait(20)
        hidden_original = end()
        write_samples(output / 'hidden-original.json', hidden_original)
        if any(sample['cursor_visible'] for _, sample in hidden_original):
            raise AssertionError('相对鼠标验证期间 Windows 光标并未保持隐藏')
        begin()
        play(hidden_original)
        hidden_replayed = end()
        write_samples(output / 'hidden-replayed.json', hidden_replayed)

        def movement(samples):
            motion = [sample['mouse'] for _, sample in samples if sample.get('mouse', {}).get('mode') == 'relative']
            return [sum(item[axis] for item in motion) for axis in ('x', 'y')]

        if movement(hidden_original) != [48, 16] or movement(hidden_replayed) != movement(hidden_original):
            raise AssertionError('隐藏光标的实际相对鼠标位移复现不符')
        assert_released()
        for name, samples in (('original', original), ('replayed', replayed), ('canceled', canceled),
                              ('hidden-original', hidden_original), ('hidden-replayed', hidden_replayed)):
            write_samples(output / f'{name}.json', samples)
        report = {'verified': True, 'source': '独立真实 Windows Qt 窗口', 'output': str(output),
                  'original_durations': original_durations, 'replayed_durations': replayed_durations,
                  'duration_differences': differences, 'maximum_cursor_error_pixels': cursor_error,
                  'relative_motion': movement(hidden_replayed), 'normal_release_verified': True,
                  'stop_release_verified': True, 'watchdog_release_verified': True,
                  'watchdog_release_seconds': watchdog_release_seconds,
                  'scope': '验证 Windows 输入传递、时序、光标与释放；游戏任务和车辆流程尚未验证'}
        (output / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False))
    finally:
        try:
            sink.release()
            for name in ('tab', 't', 'w'):
                win32api.keybd_event(KEY_CODES[name], 0, win32con.KEYEVENTF_KEYUP, 0)
            win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
        finally:
            try:
                capture.close()
            finally:
                for _ in range(hidden_calls):
                    ctypes.windll.user32.ShowCursor(True)
                try:
                    win32api.SetCursorPos(cursor)
                finally:
                    window.close()
                    app.processEvents()


if __name__ == '__main__':
    main()
