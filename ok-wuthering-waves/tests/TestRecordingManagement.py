import sqlite3

import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit
from qfluentwidgets import MessageBox

from TestGameplayLearningUi import learning_page
from src.workflow.store import WorkflowStateStore


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


def test_recording_name_editor_refresh_selection_and_delete(learning_page):
    app, page, learning, manager = learning_page
    store = learning.get_store()
    identifier = store.start_recording()
    store.finish_recording(identifier, 'complete')
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
