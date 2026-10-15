import gettext
import os
from pathlib import Path

import cv2
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from qfluentwidgets import Theme, setTheme

from config import config
from ok import Box, Config
from ok.device.DeviceManager import DeviceManager
from ok.task.TaskExecutor import TaskExecutor
from ok.util.GlobalConfig import GlobalConfig
from ok.util.handler import ExitEvent
from src.task.MapOverlayTask import MapOverlayTask
from src.utils.KuroPositionDialog import KuroPositionDialog
from src.utils.MinimapDirectionWindow import MinimapDirectionWindow
from src.utils.PositionSources import KuroCaptureReader


ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / 'tests/data/kuro_position'


@pytest.fixture
def task(tmp_path):
    app = QApplication.instance() or QApplication([])
    setTheme(Theme.LIGHT, save=False)
    previous_folder = Config.config_folder
    Config.config_folder = str(tmp_path)
    exit_event = ExitEvent()
    global_config = GlobalConfig(config['global_configs'])
    manager = DeviceManager({}, exit_event, global_config)
    executor = TaskExecutor(manager, exit_event=exit_event, global_config=global_config,
                            config={'locale': 'zh_CN'})
    instance = MapOverlayTask(executor=executor, app=app)
    instance.config = Config('position-test', instance.default_config, folder=str(tmp_path))
    instance.config['_Kuro position directory'] = str(CAPTURE)
    instance._configure_position_sources()
    yield instance
    if instance._overlay_controller is not None:
        instance._overlay_controller.close()
    manager.close()
    executor.destroy()
    exit_event.set()
    Config.config_folder = previous_folder
    app.processEvents()


def test_real_task_and_dialog_reject_disconnected_capture(task, tmp_path):
    parent = QWidget()
    parent.resize(1100, 700)
    parent.show()
    translate = gettext.translation('ok', localedir=ROOT / 'i18n', languages=['zh_CN']).gettext
    dialog = KuroPositionDialog(task, parent, translate=translate)
    try:
        dialog.show()
        QTest.qWait(250)
        assert not dialog.yesButton.isEnabled()
        assert '断开' in dialog.state_label.text()
        dialog.confirm_role.setChecked(True)
        assert not dialog.yesButton.isEnabled()
        assert task._select_position() is None
        assert task.player_position_for_map() is None
        assert task.config_type['Position source']['options'] == ['Automatic', 'OCR only', 'Kuro only']
        dialog.accept()
        assert dialog.isVisible()
        output = Path(os.environ.get('POSITION_UI_OUTPUT', str(tmp_path / 'kuro-position-dialog.png')))
        assert dialog.grab().save(str(output))
        assert dialog.yesButton.text() == '绑定当前会话'
    finally:
        dialog.reject()
        assert not dialog.timer.isActive()
        parent.close()


def test_task_keeps_ocr_state_independent_of_kuro_position(task):
    reader = KuroCaptureReader(CAPTURE).poll(1791966785.2)
    sample = reader.latest
    task._accept_kuro_position(sample, in_big_map=True)
    assert task._last_valid == sample.overlay_position
    assert task._last_ocr_valid is None
    assert task._player_map_id == '8'
    assert task.player_position_for_map() is None
    text = task._get_position_detector().detect_position(cv2.imread(str(ROOT / 'tests/images/mini_map.png')))
    assert task._denoise(text) == (-1175, 2126, 7)
    assert task._last_valid == sample.overlay_position
    task.config['Collection account'] = '另外一个账号'
    task._configure_position_sources()
    assert task._last_valid is None
    assert task._last_ocr_valid is None


def test_direction_window_renders_delayed_position_note():
    app = QApplication.instance() or QApplication([])
    sample = KuroCaptureReader(CAPTURE).poll(1791966785.2).latest
    window = MinimapDirectionWindow()
    try:
        note = '库街区同步 · 2.0 秒前接收 · 实际延迟未知'
        window.render_direction((0, 0, 800, 600), 50, Box(25, 25, 180, 180),
                                distance=3300, position_note=note)
        QTest.qWait(30)
        assert window._position_note == note
        assert window.windowFlags() & Qt.WindowTransparentForInput
        assert not window.grab().isNull()
        window.render_direction((0, 0, 800, 600), 50, Box(25, 25, 180, 180),
                                distance=3300, position_note='')
        QTest.qWait(30)
        assert window._position_note == ''
        assert sample.z is None
    finally:
        window.close()
        app.processEvents()


def test_source_translations_are_compiled():
    for catalog in (ROOT / 'i18n').glob('*/LC_MESSAGES/ok.mo'):
        with catalog.open('rb') as source:
            translation = gettext.GNUTranslations(source)
        if catalog.parts[-3] != 'en_US':
            assert translation.gettext('Position source') != 'Position source'
            assert translation.gettext('Bind current session') != 'Bind current session'
