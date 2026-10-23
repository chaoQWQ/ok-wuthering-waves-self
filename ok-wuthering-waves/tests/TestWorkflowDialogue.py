import hashlib
import os
import time
from pathlib import Path

import cv2
import pytest

from TestGameplayLearning import observer, save_observation
from src.workflow.dialogue import RecordedDialogue, ensure_allowed_target
from src.workflow.input import WorkflowStopped
from src.workflow.models import Condition, Step, Workflow
from src.workflow.store import WorkflowStateStore


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def decision_recording(tmp_path, observer):
    store = WorkflowStateStore(tmp_path)
    recording = store.start_recording()
    frames = []
    for index, name in enumerate(('quest_teleport_closer_dialog', 'quest_after_story_combat')):
        frame = cv2.imread(str(ROOT / 'tests/images' / f'{name}.png'))
        assert frame is not None
        observation = observer.observe(frame, float(index + 1), True)
        frames.append(save_observation(store, recording, observation, name))
    before, after = frames
    assert before.scene == 'menu'
    step = Step(step_id='confirm_travel', action='select', target='确认',
                preconditions=Condition(scene=before.scene, texts=['确认']),
                completion=Condition(scene=after.scene, objective=after.objective),
                evidence_before=before.frame_id, evidence_after=after.frame_id)
    workflow = Workflow(title='快速旅行确认', content_id='travel_confirmation', period='once', game_version='fixture',
                        recording_id=recording, steps=[step])
    store.finish_recording(recording, 'complete')
    return store, workflow, step, before, after


def test_actual_menu_screenshot_resolves_only_text_inside_original_ocr_polygon(decision_recording, observer):
    store, workflow, step, before, after = decision_recording
    dialogue = RecordedDialogue(store, workflow, observer)
    for target in ('确认', '取消'):
        control, = [item for item in before.controls if item.text == target and item.confidence >= .8]
        assert dialogue.text_at(before, (control.x, control.y)) == target
    assert dialogue.text_at(before, (.01, .01)) is None
    assert dialogue.choice(step, before) is None
    assert dialogue.choice(step, after) is None


def test_recorded_selection_checks_original_screenshot_hash(decision_recording, observer):
    store, workflow, step, before, after = decision_recording
    path = store.resolve(before.screenshot)
    path.write_bytes(store.resolve(after.screenshot).read_bytes())
    with pytest.raises(WorkflowStopped, match='原始截图已经变化'):
        RecordedDialogue(store, workflow, observer).text_at(before, (.65, .63))


@pytest.mark.parametrize('target', ['购买道具', '充值', '兑换', '删除', '账号设置', '发送消息', 'Purchase', 'Exchange'])
def test_selection_rejects_unapproved_operations(target):
    with pytest.raises(WorkflowStopped, match='不属于允许'):
        ensure_allowed_target(target)


def test_selection_allows_observed_story_target():
    ensure_allowed_target('确认')


@pytest.mark.skipif(os.environ.get('QT_QPA_PLATFORM') != 'windows', reason='需要真实 Windows 验证窗口')
@pytest.mark.parametrize('targets', [('确认',), ('确认', '取消')])
def test_native_recorded_clicks_resolve_selection_and_reject_conflicting_branches(tmp_path, observer, targets):
    import win32api
    import win32con
    import win32gui
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPixmap
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QLabel

    from src.workflow.input_capture import WindowsInputCapture

    app = QApplication.instance() or QApplication([])
    image_path = ROOT / 'tests/images/quest_teleport_closer_dialog.png'
    source_frame = cv2.imread(str(image_path))
    source = observer.observe(source_frame, time.monotonic(), True)
    controls = {target: next(item for item in source.controls if item.text == target and item.confidence >= .8)
                for target in targets}
    height, width = source_frame.shape[:2]
    window = QLabel()
    window.setWindowTitle('对白证据验证窗口：展示已有真实游戏截图')
    window.setWindowFlag(Qt.WindowStaysOnTopHint)
    window.setPixmap(QPixmap(str(image_path)))
    window.setFixedSize(width, height)
    window.show()
    window.activateWindow()
    window.raise_()
    QTest.qWait(200)
    hwnd = int(window.winId())
    client_width, client_height = win32gui.GetClientRect(hwnd)[2:]
    original_cursor = win32api.GetCursorPos()
    capture = WindowsInputCapture(hwnd)
    before_path, after_path = tmp_path / 'before.png', tmp_path / 'after.png'
    pressed = False
    try:
        point = win32gui.ClientToScreen(hwnd, (client_width // 2, client_height // 2))
        if win32gui.GetAncestor(win32gui.WindowFromPoint(point), win32con.GA_ROOT) != hwnd:
            raise RuntimeError('验证窗口不可见，停止发送输入')
        win32api.SetCursorPos(point)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        QTest.qWait(100)
        if win32gui.GetForegroundWindow() != hwnd:
            raise RuntimeError('验证窗口没有位于前台，停止发送输入')
        capture.start()
        capture.enabled.set()
        QTest.qWait(100)
        before_time = time.monotonic()
        assert window.grab().save(str(before_path))
        for target in targets:
            control = controls[target]
            if win32gui.GetForegroundWindow() != hwnd:
                raise RuntimeError('验证窗口失去前台，停止发送输入')
            win32api.SetCursorPos(win32gui.ClientToScreen(
                hwnd, (round(control.x * client_width), round(control.y * client_height))))
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            pressed = True
            QTest.qWait(30)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            pressed = False
            QTest.qWait(60)
        window.setPixmap(QPixmap(str(ROOT / 'tests/images/quest_after_story_combat.png')).scaled(width, height))
        QTest.qWait(50)
        after_time = time.monotonic()
        assert window.grab().save(str(after_path))
        time.sleep(.05)
        samples = capture.drain(after_time)
        capture.enabled.clear()
    finally:
        if pressed:
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        capture.close()
        window.close()
        app.processEvents()
        win32api.SetCursorPos(original_cursor)
    before = observer.observe(cv2.imread(str(before_path)), before_time, True)
    after = observer.observe(cv2.imread(str(after_path)), after_time, True)
    assert before.scene == 'menu'
    clicks = [(timestamp, payload) for timestamp, payload in samples
              if payload.get('mouse', {}).get('button_flags', 0) & 1]
    assert len(clicks) == len(targets)
    assert all(before_time <= timestamp <= after_time for timestamp, _ in clicks)
    store = WorkflowStateStore(tmp_path / 'recording')
    recording = store.start_recording()
    for observation, path in ((before, before_path), (after, after_path)):
        destination = store.resolve(f'{recording}/{observation.frame_id}.png')
        destination.write_bytes(path.read_bytes())
        observation.screenshot = str(destination.relative_to(store.root))
        observation.image_sha256 = hashlib.sha256(destination.read_bytes()).hexdigest()
        store.observe(recording, observation)
    store.event(recording, before_time, 'verification_source', {
        'source': '真实 Windows Qt 验证窗口，展示已有真实游戏截图'})
    store.events_batch(recording, [(timestamp, 'input', payload) for timestamp, payload in samples])
    step = Step(step_id='confirm_travel', action='select', target='确认',
                preconditions=Condition(scene='menu', texts=['确认']),
                completion=Condition(scene=after.scene, objective=after.objective),
                evidence_before=before.frame_id, evidence_after=after.frame_id)
    workflow = Workflow(title='快速旅行确认', content_id='native_travel_confirmation', period='once',
                        game_version='fixture', recording_id=recording, steps=[step])
    store.finish_recording(recording, 'complete')
    dialogue = RecordedDialogue(WorkflowStateStore(store.root), workflow, observer)
    if len(targets) == 1:
        assert dialogue.choice(step, before).text == '确认'
    else:
        with pytest.raises(WorkflowStopped, match='记录了不同选项'):
            dialogue.choice(step, before)
