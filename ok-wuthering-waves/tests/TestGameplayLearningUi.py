import os
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from qfluentwidgets import Theme, setTheme

from config import config
from ok import Config, og
from ok.device.DeviceManager import DeviceManager
from ok.task.TaskExecutor import TaskExecutor
from ok.util.GlobalConfig import GlobalConfig
from ok.util.handler import ExitEvent
from src.gui.WorkflowPage import WorkflowPage
from src.scene.WWScene import WWScene
from src.task.GameplayLearningTask import GameplayLearningTask
from src.task.MultiAccountWorkflowTask import MultiAccountWorkflowTask


@pytest.fixture
def learning_page(tmp_path):
    app = QApplication.instance() or QApplication([])
    assert QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc') >= 0
    app.setFont(QFont('Microsoft YaHei', 10))
    setTheme(Theme.LIGHT, save=False)
    previous = Config.config_folder
    previous_globals = og.app, og.executor, og.main_window
    Config.config_folder = str(tmp_path)
    exit_event = ExitEvent()
    global_config = GlobalConfig(config['global_configs'])
    manager = DeviceManager({}, exit_event, global_config)
    executor = TaskExecutor(manager, exit_event=exit_event, global_config=global_config, config={'locale': 'zh_CN'})
    executor.scene = WWScene()
    learning = GameplayLearningTask(executor=executor, app=app)
    execution = MultiAccountWorkflowTask(executor=executor, app=app)
    learning.after_init()
    execution.after_init()
    learning.config['Recording folder'] = str(tmp_path / 'recordings')
    executor.trigger_tasks.append(learning)
    executor.onetime_tasks.append(execution)
    og.app, og.executor = app, executor
    page = WorkflowPage()
    og.main_window = page
    page.executor = executor
    try:
        page.resize(1100, 900)
        page.refresh()
        page.show()
        QTest.qWait(200)
        yield app, page, learning, manager
    finally:
        page.dispose()
        page.close()
        manager.close()
        executor.destroy()
        exit_event.set()
        Config.config_folder = previous
        og.app, og.executor, og.main_window = previous_globals
        app.processEvents()


def test_real_tasks_and_learning_page_render_without_game_input(learning_page):
    app, page, learning, manager = learning_page
    assert not learning.enabled
    assert page.execute_button.isEnabled()
    assert len(page.cards) == 2
    assert page.status_label.text()
    assert page.hotkey_setting.line_edit.text() == '<ctrl>+<shift>+<f8>'
    assert manager.interaction is None
    output = Path('working_images/workflow-learning-page.png').resolve()
    output.parent.mkdir(exist_ok=True)
    assert page.grab().save(str(output))
    assert not page.grab().isNull()


def test_recording_shortcut_editor_persists_and_clears(learning_page):
    app, page, learning, manager = learning_page
    editor = page.hotkey_setting.line_edit
    QTest.mouseClick(editor, Qt.LeftButton)
    QTest.keyClick(editor, Qt.Key_F5, Qt.ControlModifier | Qt.AltModifier)
    page.refresh()
    assert learning.config['Recording Toggle Hotkey'] == '<ctrl>+<alt>+<f5>'
    assert editor.text() == '<ctrl>+<alt>+<f5>'
    assert not learning.enabled
    reloaded = GameplayLearningTask(executor=learning.executor, app=app)
    reloaded.after_init()
    assert reloaded.config['Recording Toggle Hotkey'] == '<ctrl>+<alt>+<f5>'
    QTest.mouseClick(editor, Qt.LeftButton)
    QTest.keyClick(editor, Qt.Key_F6)
    QTest.mouseClick(editor, Qt.LeftButton)
    QTest.keyClick(editor, Qt.Key_Escape)
    assert learning.config['Recording Toggle Hotkey'] == '<f6>'
    QTest.mouseClick(editor, Qt.LeftButton)
    QTest.keyClick(editor, Qt.Key_Delete)
    page.poll_recording_hotkey()
    assert learning.config['Recording Toggle Hotkey'] == ''
    assert page._hotkey_poller is None
    assert not learning.enabled
    assert not learning.validate('Recording Toggle Hotkey', '<ctrl>')[0]
    assert not learning.validate('Recording Toggle Hotkey', '<delete>')[0]
    assert manager.interaction is None


def test_native_global_recording_shortcut(learning_page):
    app, page, learning, manager = learning_page
    if app.platformName() != 'windows':
        pytest.skip('真实 Windows 键盘测试需要 QT_QPA_PLATFORM=windows')
    import win32api
    import win32con
    import win32gui

    def activate(widget):
        widget.show()
        widget.activateWindow()
        widget.raise_()
        QTest.qWait(150)
        handle = int(widget.winId())
        if win32gui.GetForegroundWindow() != handle:
            win32gui.SetForegroundWindow(handle)
            QTest.qWait(150)
        assert win32gui.GetForegroundWindow() == handle, 'Windows 未能激活测试窗口，停止发送按键'

    def press(codes, expected):
        assert win32gui.GetForegroundWindow() in (int(page.winId()), int(other.winId()))
        try:
            for code in codes:
                win32api.keybd_event(code, 0, 0, 0)
            QTest.qWait(220)
            key_states = [bool(win32api.GetAsyncKeyState(code) & 0x8000) for code in codes]
            assert learning.enabled is expected, f'keys_down={key_states}, ready={page._hotkey_ready}'
            QTest.qWait(220)
            assert learning.enabled is expected
        finally:
            for code in reversed(codes):
                win32api.keybd_event(code, 0, win32con.KEYEVENTF_KEYUP, 0)
            QTest.qWait(120)

    other = QWidget()
    try:
        activate(page)
        default_codes = (win32con.VK_CONTROL, win32con.VK_SHIFT, win32con.VK_F8)
        press(default_codes, True)
        press(default_codes, False)
        editor = page.hotkey_setting.line_edit
        QTest.mouseClick(editor, Qt.LeftButton)
        changed_codes = (win32con.VK_CONTROL, win32con.VK_MENU, win32con.VK_F5)
        press(changed_codes, False)
        assert learning.config['Recording Toggle Hotkey'] == '<ctrl>+<alt>+<f5>'
        press(default_codes, False)
        press(changed_codes, True)
        press(changed_codes, False)
        page.hide()
        other.setWindowTitle('录制快捷键验证')
        other.resize(360, 120)
        activate(other)
        press(changed_codes, True)
        press(changed_codes, False)
        learning.config['Recording Toggle Hotkey'] = ''
        QTest.qWait(100)
        press(changed_codes, False)
        assert manager.interaction is None
    finally:
        other.close()
