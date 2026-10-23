import sqlite3
import json
from pathlib import Path

import cv2
import pytest
from PySide6.QtCore import QEvent, QPoint, QTimer, Qt
from PySide6.QtGui import QHelpEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit
from qfluentwidgets import CheckBox, MessageBox, Theme, setTheme

from TestGameplayLearningUi import learning_page
from TestGameplayLearning import observer, observations, save_observation
from src.gui.RecordingMaterialsDialog import RecordingMaterialsDialog
from src.workflow.models import Condition, Step, Workflow
from src.workflow.store import WorkflowStateStore
from src.workflow.video import SegmentedVideoWriter


@pytest.fixture
def recorded_materials(tmp_path, observations):
    store = WorkflowStateStore(tmp_path / 'materials')
    recording = store.start_recording()
    store.set_environment(recording, 'fixture', 'zh_CN')
    before = save_observation(store, recording, observations['quest_yangyang_interaction'], 'quest_yangyang_interaction')
    after = save_observation(store, recording, observations['quest_after_story_combat'], 'quest_after_story_combat')
    root = Path(__file__).resolve().parents[1]
    evidence = json.loads((root / 'tests/data/workflow_input_capture.json').read_text(encoding='utf-8'))
    sample = evidence['samples'][0]
    store.event(recording, sample['timestamp'], 'input', sample['payload'])
    writer = SegmentedVideoWriter(store.resolve(recording))
    try:
        writer.write(cv2.imread(str(root / 'tests/images/quest_yangyang_interaction.png')), 1.0)
    finally:
        writer.close()
    step = Step(step_id='observed_transition', action='wait',
                preconditions=Condition(objective=before.objective), completion=Condition(objective=after.objective),
                evidence_before=before.frame_id, evidence_after=after.frame_id)
    workflow = Workflow(title='真实截图条件检查', content_id='material_removal', period='once',
                        recording_id=recording, game_version='fixture', steps=[step])
    store.finish_recording(recording, 'complete')
    store.save_workflow(workflow)
    analysis_directory = store.resolve('agy')
    analysis_directory.mkdir()
    request = {'recording_id': recording, 'observations': [before.model_dump(), after.model_dump()],
               'events': store.events(recording)}
    (analysis_directory / f'{recording}.json').write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
    return store, recording, workflow


def test_existing_database_migration_and_name_persistence(tmp_path):
    with sqlite3.connect(tmp_path / 'workflows.sqlite3') as db:
        db.execute("CREATE TABLE recordings (id TEXT PRIMARY KEY, started REAL NOT NULL, status TEXT NOT NULL, "
                   "reason TEXT NOT NULL DEFAULT '', analyzed INTEGER NOT NULL DEFAULT 0)")
        db.execute("INSERT INTO recordings VALUES ('existing', 1, 'complete', '', 0)")
    store = WorkflowStateStore(tmp_path)
    assert store.list_recordings()[0]['name'] == ''
    store.rename_recording('existing', '  剧情第一章  ')
    assert WorkflowStateStore(tmp_path).list_recordings()[0]['name'] == '剧情第一章'
    for name in ('', '   ', '名称' * 81):
        with pytest.raises(ValueError):
            store.rename_recording('existing', name)
    assert store.recording('existing')['name'] == '剧情第一章'


def test_materials_export_original_evidence_and_find_analysis(recorded_materials):
    store, recording, workflow = recorded_materials
    original = store.observations(recording)
    materials = store.recording_materials(recording)
    assert materials['directory'] == store.resolve(recording)
    assert materials['observations'] == 2
    assert materials['input_events'] == 1
    files = materials['files']
    assert all(item['path'].is_file() for item in files)
    assert sum(item['kind'] == '关键截图' for item in files) == 2
    assert sum(item['kind'] == '录像' for item in files) == 1
    assert sum(item['kind'] == 'AGY 分析请求' for item in files) == 1
    exported = json.loads(store.resolve(f'{recording}/observations.json').read_text(encoding='utf-8'))
    assert exported == [item.model_dump() for item in original]
    events = json.loads(store.resolve(f'{recording}/events.json').read_text(encoding='utf-8'))
    assert events == [{**event, 'payload': json.loads(event['payload'])} for event in store.events(recording)]
    details = json.loads(store.resolve(f'{recording}/recording-details.json').read_text(encoding='utf-8'))
    assert details['id'] == recording
    assert details['environment'] == store.environment(recording)
    assert store.workflow(workflow.revision, executable=False) == workflow


def test_materials_require_existing_directory_and_valid_recording(tmp_path):
    store = WorkflowStateStore(tmp_path)
    recording = store.start_recording()
    materials = store.recording_materials(recording)
    assert materials['recording']['status'] == 'recording'
    assert materials['input_events'] == 0
    store.finish_recording(recording, 'interrupted', '无法取得游戏画面')
    materials = store.recording_materials(recording)
    assert materials['recording']['reason'] == '无法取得游戏画面'
    store.delete_recordings([recording], delete_materials=True)
    with pytest.raises(ValueError):
        store.recording_materials(recording)
    assert not store.resolve(recording).exists()
    missing = store.start_recording()
    store.resolve(missing).rmdir()
    with pytest.raises(FileNotFoundError):
        store.recording_materials(missing)
    assert not store.resolve(missing).exists()
    with store.connect() as db:
        db.execute("INSERT INTO recordings(id,started,status) VALUES ('..',1,'complete')")
    with pytest.raises(ValueError):
        store.recording_materials('..')


def test_recording_materials_button_opens_matching_files(learning_page, recorded_materials):
    app, page, learning, manager = learning_page
    store, recording, workflow = recorded_materials
    learning.config['Recording folder'] = str(store.root)
    learning.get_store()
    page.refresh()
    assert not page.materials_button.isEnabled()
    page.recordings.selectRow(0)
    assert page.materials_button.isEnabled()
    opened = []

    def close_materials():
        dialog = app.activeModalWidget()
        opened.append(dialog)
        dialog.accept()

    QTimer.singleShot(150, close_materials)
    QTest.mouseClick(page.materials_button, Qt.LeftButton)
    assert len(opened) == 1
    dialog = opened[0]
    assert isinstance(dialog, RecordingMaterialsDialog)
    assert dialog.materials['recording']['id'] == recording
    assert '关键截图 2 张' in dialog.summary.text()
    assert '键鼠事件 1 条' in dialog.summary.text()
    assert not dialog.open_button.isEnabled()
    video_row = next(row for row, item in enumerate(dialog.materials['files']) if item['kind'] == '录像')
    dialog.table.selectRow(video_row)
    assert dialog.open_button.isEnabled() and dialog.location_button.isEnabled()
    assert dialog.selected_path() == store.resolve(f'{recording}/video_00000.mkv')
    dialog.show()
    QTest.qWait(100)
    assert dialog.grab().save(str(store.root.parent / 'recording-materials.png'))
    dialog.close()
    page.recordings.clearSelection()
    assert not page.materials_button.isEnabled()
    assert manager.interaction is None


def test_delete_preserves_material_and_removes_pending_analysis(tmp_path):
    store = WorkflowStateStore(tmp_path)
    identifier = store.start_recording()
    store.set_environment(identifier, '1.0', 'zh_CN')
    store.finish_recording(identifier, 'complete')
    assert store.pending_recordings() == [identifier]
    store.delete_recordings([identifier])
    reopened = WorkflowStateStore(tmp_path)
    assert reopened.list_recordings() == []
    assert reopened.pending_recordings() == []
    assert reopened.environment(identifier) == {'game_version': '1.0', 'language': 'zh_CN'}
    assert reopened.recording(identifier)['deleted'] == 1
    assert reopened.resolve(identifier).is_dir()
    with pytest.raises(ValueError):
        reopened.rename_recording(identifier, '名称')


def test_delete_batch_rolls_back_when_recording_is_active(tmp_path):
    store = WorkflowStateStore(tmp_path)
    first = store.start_recording()
    store.finish_recording(first, 'complete')
    active = store.start_recording()
    with pytest.raises(ValueError):
        store.delete_recordings([first, active])
    assert {item['id'] for item in store.list_recordings()} == {first, active}


def test_optional_material_deletion_clears_files_and_database(recorded_materials):
    store, recording, workflow = recorded_materials
    assert list(store.resolve(recording).glob('*.mkv'))
    assert len(store.observations(recording)) == 2
    assert store.events(recording)
    store.delete_recordings([recording], delete_materials=True)
    assert not store.resolve(recording).exists()
    assert not store.resolve(f'agy/{recording}.json').exists()
    assert store.observations(recording) == []
    assert store.events(recording) == []
    with pytest.raises(ValueError):
        store.environment(recording)
    assert store.recording(recording)['status'] == 'materials_deleted'
    assert store.list_workflows()[0]['status'] == 'materials_deleted'
    assert store.workflow(workflow.revision, executable=False) == workflow
    with pytest.raises(ValueError):
        store.workflow(workflow.revision)
    with pytest.raises(ValueError):
        store.save_workflow(workflow)
    with pytest.raises(ValueError):
        store.validate(workflow.revision, 'offline_checked', {})


def test_materials_are_usable_when_option_is_unchecked(recorded_materials):
    store, recording, workflow = recorded_materials
    store.delete_recordings([recording])
    assert list(store.resolve(recording).glob('*.mkv'))
    assert store.resolve(f'agy/{recording}.json').exists()
    assert len(store.observations(recording)) == 2
    assert store.events(recording)
    assert store.environment(recording)['language'] == 'zh_CN'
    assert store.list_workflows()[0]['status'] == 'compiled'
    assert store.workflow(workflow.revision, executable=False) == workflow


def test_material_deletion_checks_entire_batch_before_removing_files(tmp_path):
    store = WorkflowStateStore(tmp_path / 'materials')
    complete = store.start_recording()
    store.finish_recording(complete, 'complete')
    active = store.start_recording()
    with pytest.raises(ValueError):
        store.delete_recordings([complete, active], delete_materials=True)
    assert store.resolve(complete).is_dir()
    assert store.recording(complete)['deleted'] == 0
    with store.connect() as db:
        db.execute("INSERT INTO recordings(id,started,status) VALUES ('..',1,'complete')")
    with pytest.raises(ValueError):
        store.delete_recordings(['..'], delete_materials=True)
    assert store.root.is_dir()
    assert store.path.is_file()


def test_recording_name_editor_refresh_selection_and_delete(learning_page):
    app, page, learning, manager = learning_page
    store = learning.get_store()
    identifier = store.start_recording()
    reason = '当前执行动作不包含任意键鼠轨迹回放，录制中包含科考摩托车赶路及工具轮盘选择。' * 4
    store.finish_recording(identifier, 'complete', reason)
    page.refresh()
    table = page.recordings
    assert table.columnCount() == 4
    assert not table.item(0, 1).flags() & Qt.ItemIsEditable
    table.selectRow(0)
    table.setCurrentCell(0, 0)
    table.editItem(table.item(0, 0))
    editor = table.findChild(QLineEdit)
    assert editor is not None
    editor.setText('每日剧情记录')
    page.refresh()
    assert table.findChild(QLineEdit).text() == '每日剧情记录'
    QTest.keyClick(editor, Qt.Key_Return)
    app.processEvents()
    assert store.recording(identifier)['name'] == '每日剧情记录'
    page.refresh()
    assert table.item(0, 0).text() == '每日剧情记录'
    assert table.selectionModel().selectedRows()
    assert page.delete_recordings_button.isEnabled()
    page.ensureWidgetVisible(table)
    cell = table.item(0, 3)
    assert cell.text() == reason
    assert cell.toolTip().replace('\n', '') == reason
    assert max(map(len, cell.toolTip().splitlines())) <= 48
    position = table.visualItemRect(cell).center()
    event = QHelpEvent(QEvent.ToolTip, position, table.viewport().mapToGlobal(position))
    app.sendEvent(table.viewport(), event)
    QTest.qWait(250)
    tooltip = table.delegate.tooltipDelegate._tooltip
    assert tooltip is not None and tooltip.isVisible()
    assert tooltip.label.text().replace('\n', '') == reason
    assert tooltip.width() < table.screen().availableGeometry().width()
    output = store.root.parent / 'recording-description-tooltip.png'
    assert tooltip.grab().save(str(output))
    table.delegate.tooltipDelegate.hideToolTip()

    def cancel_dialog():
        dialog = next(widget for widget in page.findChildren(MessageBox)
                      if isinstance(widget, MessageBox) and widget.isVisible())
        QTest.mouseClick(dialog.cancelButton, Qt.LeftButton)

    def accept_dialog():
        dialog = next(widget for widget in page.findChildren(MessageBox)
                      if isinstance(widget, MessageBox) and widget.isVisible())
        QTest.mouseClick(dialog.yesButton, Qt.LeftButton)

    QTimer.singleShot(100, cancel_dialog)
    QTest.mouseClick(page.delete_recordings_button, Qt.LeftButton)
    assert len(store.list_recordings()) == 1
    QTimer.singleShot(100, accept_dialog)
    QTest.mouseClick(page.delete_recordings_button, Qt.LeftButton)
    assert store.list_recordings() == []
    assert table.rowCount() == 0
    assert not page.delete_recordings_button.isEnabled()
    active = store.start_recording()
    page.refresh()
    table.selectRow(0)
    assert not page.delete_recordings_button.isEnabled()
    store.finish_recording(active, 'complete')
    page.refresh()
    assert page.delete_recordings_button.isEnabled()
    assert manager.interaction is None
    output = store.root.parent / 'recording-management.png'
    assert page.grab().save(str(output))


@pytest.mark.parametrize('theme', [Theme.LIGHT, Theme.DARK])
def test_recording_selection_stays_visible_after_hover_and_refresh(learning_page, theme):
    app, page, learning, manager = learning_page
    setTheme(theme, save=False)
    store = learning.get_store()
    for _ in range(3):
        identifier = store.start_recording()
        store.finish_recording(identifier, 'complete')
    page.refresh()
    table = page.recordings
    page.ensureWidgetVisible(table)
    QTest.qWait(50)
    cell = table.item(1, 0)
    identifier = cell.data(Qt.UserRole)
    QTest.mouseClick(table.viewport(), Qt.LeftButton, pos=table.visualItemRect(cell).center())
    QTest.mouseMove(page.delete_recordings_button, page.delete_recordings_button.rect().center())
    app.processEvents()
    marker = QPoint(5, table.visualItemRect(table.item(1, 0)).center().y())
    selected_color = table.viewport().grab().toImage().pixelColor(marker)
    assert selected_color.saturation() > 100
    QTest.qWait(1200)
    rows = {index.row() for index in table.selectionModel().selectedRows()}
    assert rows == {1}
    assert table.item(1, 0).data(Qt.UserRole) == identifier
    assert table.delegate.selectedRows == rows
    assert table.viewport().grab().toImage().pixelColor(marker) == selected_color
    assert page.delete_recordings_button.isEnabled()

    QTest.mouseClick(table.viewport(), Qt.LeftButton, Qt.ControlModifier,
                     table.visualItemRect(table.item(2, 0)).center())
    selected = {table.item(index.row(), 0).data(Qt.UserRole)
                for index in table.selectionModel().selectedRows()}
    assert len(selected) == 2
    latest = store.start_recording()
    store.finish_recording(latest, 'complete')
    page.refresh()
    QTest.mouseMove(page.delete_recordings_button, page.delete_recordings_button.rect().center())
    app.processEvents()
    rows = {index.row() for index in table.selectionModel().selectedRows()}
    assert {table.item(row, 0).data(Qt.UserRole) for row in rows} == selected
    assert table.delegate.selectedRows == rows
    output = Path('working_images') / f'recording-selection-{theme.value.lower()}.png'
    assert table.grab().save(str(output))
    table.clearSelection()
    assert not table.delegate.selectedRows
    assert not page.delete_recordings_button.isEnabled()
    assert manager.interaction is None


def test_workflow_selection_stays_visible_after_refresh(learning_page, recorded_materials):
    app, page, learning, manager = learning_page
    store, recording, workflow = recorded_materials
    learning.config['Recording folder'] = str(store.root)
    learning.get_store()
    page.refresh()
    table = page.table
    page.ensureWidgetVisible(table)
    QTest.mouseClick(table.viewport(), Qt.LeftButton,
                     pos=table.visualItemRect(table.item(0, 0)).center())
    QTest.mouseMove(page.execute_button, page.execute_button.rect().center())
    QTest.qWait(1200)
    assert table.item(0, 0).data(Qt.UserRole) == workflow.revision
    assert {index.row() for index in table.selectionModel().selectedRows()} == {0}
    assert table.delegate.selectedRows == {0}
    assert manager.interaction is None


def test_material_deletion_checkbox_and_activity_guard(learning_page):
    app, page, learning, manager = learning_page
    store = learning.get_store()
    recording = store.start_recording()
    store.finish_recording(recording, 'complete')
    page.refresh()
    page.recordings.selectRow(0)
    assert page.can_delete_recording_materials()

    def confirm_with_materials():
        dialog = next(widget for widget in page.findChildren(MessageBox) if widget.isVisible())
        checkbox = dialog.findChild(CheckBox)
        assert checkbox.isEnabled()
        assert not checkbox.isChecked()
        QTest.mouseClick(checkbox, Qt.LeftButton, pos=QPoint(10, checkbox.height() // 2))
        assert checkbox.isChecked()
        output = store.root.parent / 'recording-materials-dialog.png'
        assert page.grab().save(str(output))
        QTest.mouseClick(dialog.yesButton, Qt.LeftButton)

    QTimer.singleShot(350, confirm_with_materials)
    QTest.mouseClick(page.delete_recordings_button, Qt.LeftButton)
    assert not store.resolve(recording).exists()
    assert store.recording(recording)['status'] == 'materials_deleted'

    recording = store.start_recording()
    store.finish_recording(recording, 'complete')
    page.refresh()
    page.recordings.selectRow(0)
    learning.enable()
    assert not page.can_delete_recording_materials()

    def confirm_without_materials():
        dialog = next(widget for widget in page.findChildren(MessageBox) if widget.isVisible())
        checkbox = dialog.findChild(CheckBox)
        assert not checkbox.isEnabled()
        assert not checkbox.isChecked()
        QTest.mouseClick(dialog.yesButton, Qt.LeftButton)

    QTimer.singleShot(100, confirm_without_materials)
    QTest.mouseClick(page.delete_recordings_button, Qt.LeftButton)
    assert store.resolve(recording).exists()
    assert store.recording(recording)['deleted'] == 1
    learning.disable()
    assert page.can_delete_recording_materials()
    assert manager.interaction is None
