import json
import os
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'windows')

import win32api
import win32con
import win32gui
from PySide6.QtCore import Qt
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
    window.setWindowFlag(Qt.WindowStaysOnTopHint)
    layout = QVBoxLayout(window)
    layout.addWidget(QLabel('正在验证长按 T、短按 T、Esc、鼠标轨迹及滚轮记录。'))
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
    original_cursor = win32api.GetCursorPos()
    focus_point = win32gui.ClientToScreen(hwnd, (300, 200))
    if win32gui.GetAncestor(win32gui.WindowFromPoint(focus_point), win32con.GA_ROOT) != hwnd:
        raise RuntimeError('验证窗口的点击位置不可见，停止激活窗口')
    try:
        win32api.SetCursorPos(focus_point)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        QTest.qWait(100)
    finally:
        win32api.SetCursorPos(original_cursor)
    if win32gui.GetForegroundWindow() != hwnd:
        raise RuntimeError('测试窗口没有位于前台，停止发送测试输入')
    if win32api.GetAsyncKeyState(0x54) & 0x8000:
        raise RuntimeError('T 已经处于按下状态，无法进行键鼠验证')
    if win32api.GetAsyncKeyState(win32con.VK_ESCAPE) & 0x8000:
        raise RuntimeError('Esc 已经处于按下状态，无法进行键鼠验证')
    capture = WindowsInputCapture(hwnd)
    pressed = False
    escape_pressed = False
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
        ensure_front()
        win32api.keybd_event(win32con.VK_ESCAPE, 0, 0, 0)
        escape_pressed = True
        QTest.qWait(60)
        win32api.keybd_event(win32con.VK_ESCAPE, 0, win32con.KEYEVENTF_KEYUP, 0)
        escape_pressed = False
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
        escape_events = [(timestamp, payload) for timestamp, payload in samples
                         if payload.get('key_event', {}).get('key') == 'esc']
        if [payload['key_event']['pressed'] for _, payload in escape_events] != [True, False]:
            raise AssertionError(f'Esc 按下和释放记录不完整：{escape_events}')
        escape_started, escape_down = escape_events[0]
        escape_ended, escape_up = escape_events[1]
        if ('esc' not in escape_down['keys'] or escape_down['held_since'].get('esc') != escape_started
                or 'esc' in escape_up['keys'] or 'esc' in escape_up['held_since']
                or not .02 <= escape_ended - escape_started < .2):
            raise AssertionError('Esc 按下状态、释放状态或持续时间与实际输入不符')
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
        saved_escape = [(event['timestamp'], json.loads(event['payload'])) for event in saved
                        if json.loads(event['payload']).get('key_event', {}).get('key') == 'esc']
        summarized_escape = [(event['timestamp'], json.loads(event['payload'])) for event in state_events
                             if json.loads(event['payload']).get('key_event', {}).get('key') == 'esc']
        if saved_escape != escape_events or summarized_escape != escape_events:
            raise AssertionError('Esc 输入事件未能完整保存到数据库和 AGY 操作摘要')
        (output / 'samples.json').write_text(json.dumps({
            'source': '真实 Qt 验证窗口的 Windows Raw Input',
            'samples': [{'timestamp': timestamp, 'payload': payload} for timestamp, payload in samples],
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        report = {'verified': True, 'samples': len(samples), 't_durations': durations,
                  'esc_events': len(escape_events), 'esc_duration': escape_ended - escape_started,
                  'unheld_motion_samples': len(motion), 'summary_events': len(state_events),
                  'trajectory_groups': len(trajectories), 'output': str(output.resolve())}
        (output / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False))
    finally:
        if pressed:
            win32api.keybd_event(0x54, 0, win32con.KEYEVENTF_KEYUP, 0)
        if escape_pressed:
            win32api.keybd_event(win32con.VK_ESCAPE, 0, win32con.KEYEVENTF_KEYUP, 0)
        if mouse_pressed:
            Controller().release(Button.left)
        capture.close()
        win32api.SetCursorPos(original_cursor)
        window.close()


if __name__ == '__main__':
    main()
