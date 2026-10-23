import json
import os
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'windows')

import win32api
import win32con
import win32gui
from PySide6.QtWidgets import QApplication, QLabel, QTextEdit, QVBoxLayout, QWidget
from PySide6.QtTest import QTest
from pynput.mouse import Button, Controller

from src.workflow.input_capture import WindowsInputCapture
from src.workflow.input_evidence import describe_input_events
from src.workflow.store import WorkflowStateStore


def main():
    app = QApplication.instance() or QApplication([])
    window = QWidget()
    window.setWindowTitle('键鼠记录验证')
    layout = QVBoxLayout(window)
    layout.addWidget(QLabel('正在验证长按 T、短按 T、鼠标轨迹及滚轮记录。'))
    editor = QTextEdit()
    editor.setPlainText('\n'.join(str(number) for number in range(100)))
    editor.setReadOnly(True)
    layout.addWidget(editor)
    window.resize(600, 400)
    window.show()
    window.activateWindow()
    window.raise_()
    editor.setFocus()
    QTest.qWait(200)
    hwnd = int(window.winId())
    if win32gui.GetForegroundWindow() != hwnd:
        win32gui.SetForegroundWindow(hwnd)
        QTest.qWait(100)
    if win32gui.GetForegroundWindow() != hwnd:
        raise RuntimeError('测试窗口没有位于前台，停止发送测试输入')
    if win32api.GetAsyncKeyState(0x54) & 0x8000:
        raise RuntimeError('T 已经处于按下状态，无法进行键鼠验证')
    capture = WindowsInputCapture(hwnd)
    original_cursor = win32api.GetCursorPos()
    pressed = False
    mouse_pressed = False

    def ensure_front():
        if win32gui.GetForegroundWindow() != hwnd:
            raise RuntimeError('测试窗口失去前台，停止发送输入')

    try:
        capture.start()
        capture.enabled.set()
        QTest.qWait(100)
        center = win32gui.ClientToScreen(hwnd, (300, 200))
        win32api.SetCursorPos(center)
        QTest.qWait(50)
        capture.drain(time.monotonic())

        ensure_front()
        win32api.keybd_event(0x54, 0, 0, 0)
        pressed = True
        QTest.qWait(200)
        for _ in range(12):
            ensure_front()
            win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, 5, 2, 0, 0)
            QTest.qWait(15)
        QTest.qWait(100)
        win32api.keybd_event(0x54, 0, win32con.KEYEVENTF_KEYUP, 0)
        pressed = False
        QTest.qWait(100)
        ensure_front()
        win32api.keybd_event(0x54, 0, 0, 0)
        pressed = True
        QTest.qWait(60)
        win32api.keybd_event(0x54, 0, win32con.KEYEVENTF_KEYUP, 0)
        pressed = False
        QTest.qWait(100)
        motion_started = time.monotonic()
        for _ in range(8):
            ensure_front()
            win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, -4, 1, 0, 0)
            QTest.qWait(15)
            win32api.SetCursorPos(center)
        mouse = Controller()
        ensure_front()
        mouse.press(Button.left)
        mouse_pressed = True
        QTest.qWait(30)
        mouse.release(Button.left)
        mouse_pressed = False
        ensure_front()
        mouse.scroll(0, -1)
        QTest.qWait(100)
        # 等待独立输入线程处理已经派发的 Windows 消息。
        time.sleep(.05)
        if editor.verticalScrollBar().value() == 0:
            raise AssertionError('Windows 未将测试滚轮操作传递到实际可滚动界面')
        samples = capture.drain(time.monotonic())
        key_events = [(timestamp, payload['key_event']) for timestamp, payload in samples
                      if payload.get('key_event', {}).get('key') == 't']
        if [event['pressed'] for _, event in key_events] != [True, False, True, False]:
            raise AssertionError(f'T 按下和释放记录不完整：{key_events}')
        durations = [key_events[1][0] - key_events[0][0], key_events[3][0] - key_events[2][0]]
        if durations[0] < .35 or not .02 <= durations[1] < .2:
            raise AssertionError(f'T 持续时间不符合实际输入：{durations}')
        motion = [payload for timestamp, payload in samples if timestamp >= motion_started
                  and payload.get('mouse', {}).get('mode') == 'relative'
                  and (payload['mouse']['x'] or payload['mouse']['y'])]
        if len(motion) < 8 or not any(not item['keys'] and not item['buttons'] for item in motion):
            raise AssertionError('没有记录到按键和按钮保持释放时的连续相对鼠标轨迹')
        if not any(payload.get('mouse', {}).get('wheel') == -120 for _, payload in samples):
            raise AssertionError('没有记录到实际滚轮输入')
        flags = [payload.get('mouse', {}).get('button_flags', 0) for _, payload in samples]
        if not any(value & 1 for value in flags) or not any(value & 2 for value in flags):
            raise AssertionError('没有完整记录鼠标按钮按下和释放')

        capture.enabled.clear()
        QTest.qWait(100)
        capture.drain(time.monotonic())
        ensure_front()
        win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, 3, 0, 0, 0)
        QTest.qWait(100)
        time.sleep(.05)
        if capture.drain(time.monotonic()):
            raise AssertionError('停止记录后仍然保存了鼠标操作')

        output = Path('working_images') / f'workflow_input_{time.time_ns()}'
        store = WorkflowStateStore(output)
        recording = store.start_recording()
        store.events_batch(recording, [(timestamp, 'input', payload) for timestamp, payload in samples])
        saved = store.events(recording)
        state_events, trajectories = describe_input_events(saved)
        if len(saved) != len(samples) or not trajectories:
            raise AssertionError('键鼠原始数据或轨迹摘要保存不完整')
        (output / 'samples.json').write_text(json.dumps({
            'source': '真实 Qt 验证窗口的 Windows Raw Input',
            'samples': [{'timestamp': timestamp, 'payload': payload} for timestamp, payload in samples],
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        report = {'verified': True, 'samples': len(samples), 't_durations': durations,
                  'unheld_motion_samples': len(motion), 'summary_events': len(state_events),
                  'trajectory_groups': len(trajectories), 'output': str(output.resolve())}
        (output / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False))
    finally:
        if pressed:
            win32api.keybd_event(0x54, 0, win32con.KEYEVENTF_KEYUP, 0)
        if mouse_pressed:
            Controller().release(Button.left)
        capture.close()
        win32api.SetCursorPos(original_cursor)
        window.close()


if __name__ == '__main__':
    main()
