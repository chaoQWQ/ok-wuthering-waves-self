import gettext
import sqlite3
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from qfluentwidgets import MessageBox

from src.utils.CollectionAccountManager import CollectionAccountManager
from src.utils.MapMarksDB import MapMarksDB


class TestCollectionAccountManager(unittest.TestCase):
    def test_rename_delete_isolation_and_backups(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'marks.db')
            backups = str(Path(directory) / 'backups')
            db = MapMarksDB(path)
            try:
                db.create_account('账号一')
                db.create_account('账号二')
                db.add('shared', '账号一')
                db.add('other', '账号二')
                db.add('legacy')
                db.rename_account('账号一', '主账号', backups)
                self.assertEqual(db.load_completed('主账号'), {'shared'})
                self.assertEqual(db.load_completed('账号一'), set())
                self.assertNotIn('账号一', db.list_accounts())
                self.assertEqual(db.account_counts(), {'default': 1, '主账号': 1, '账号二': 1})
                backup = db.delete_account('主账号', backups)
                self.assertNotIn('主账号', db.list_accounts())
                self.assertEqual(db.load_completed('主账号'), set())
                self.assertEqual(db.load_completed('账号二'), {'other'})
                self.assertEqual(db.load_completed(), {'legacy'})
                restored = MapMarksDB(backup)
                try:
                    self.assertEqual(restored.load_completed('主账号'), {'shared'})
                finally:
                    restored.close()
                self.assertEqual(len(list(Path(backups).glob('*.db'))), 2)
            finally:
                db.close()
            reopened = MapMarksDB(path)
            try:
                self.assertEqual(reopened.list_accounts(), ['default', '账号二'])
            finally:
                reopened.close()

    def test_invalid_names_and_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            db = MapMarksDB(str(Path(directory) / 'marks.db'))
            try:
                for value in (' ', 'a' * 65):
                    with self.assertRaises(ValueError):
                        db.create_account(value)
                db.create_account('one')
                db.create_account('two')
                db.add('mark', 'one')
                with self.assertRaises(sqlite3.IntegrityError):
                    db.create_account('one')
                with self.assertRaises(ValueError):
                    db.rename_account('one', 'two', directory)
                self.assertEqual(db.load_completed('one'), {'mark'})
            finally:
                db.close()

    def test_visible_dialog_actions_and_cancel_confirmation(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'marks.db')
            db = MapMarksDB(path)
            db.create_account('one')
            db.add('chest', 'one')
            db.close()
            parent = QWidget()
            parent.resize(900, 850)
            parent.show()
            changes = []
            translate = gettext.translation(
                'ok', localedir=Path(__file__).resolve().parents[1] / 'i18n',
                languages=['zh_CN'],
            ).gettext
            dialog = CollectionAccountManager(path, 'one', translate, changes.append, parent)
            dialog.show()
            QTest.qWait(100)
            try:
                self.assertTrue(dialog.isVisible())
                self.assertEqual(dialog.rename_button.text(), '编辑账号名称')
                self.assertEqual(dialog.delete_button.text(), '删除账号')
                self.assertEqual(dialog.selected_account(), 'one')
                dialog.name_edit.setText('renamed')
                QTest.mouseClick(dialog.rename_button, Qt.LeftButton)
                self.assertEqual(changes[-1], 'renamed')
                self.assertEqual(dialog.current_account, 'renamed')
                dialog.name_edit.setText('two')
                QTest.mouseClick(dialog.add_button, Qt.LeftButton)
                self.assertEqual(changes[-1], 'two')
                self.assertEqual(dialog.account_list.count(), 3)
                dialog.name_edit.setText('renamed')
                self.assertFalse(dialog.add_button.isEnabled())
                self.assertFalse(dialog.rename_button.isEnabled())
                dialog.search_edit.setText('renamed')
                self.assertFalse(dialog.yesButton.isEnabled())
                self.assertFalse(dialog.delete_button.isEnabled())
                visible = [dialog.account_list.item(i) for i in range(3)
                           if not dialog.account_list.item(i).isHidden()]
                self.assertEqual(len(visible), 1)
                dialog.account_list.setCurrentItem(visible[0])
                self.assertTrue(dialog.yesButton.isEnabled())
                dialog.use_selected()
                self.assertEqual(changes[-1], 'renamed')
                dialog.search_edit.clear()

                def cancel_confirmation():
                    message = next(child for child in dialog.findChildren(MessageBox)
                                   if child is not dialog and child.isVisible())
                    message.cancelButton.click()

                QTimer.singleShot(50, cancel_confirmation)
                QTest.mouseClick(dialog.delete_button, Qt.LeftButton)
                self.assertIn('renamed', dialog.accounts)
                def accept_confirmation():
                    message = next(child for child in dialog.findChildren(MessageBox)
                                   if child is not dialog and child.isVisible())
                    message.yesButton.click()

                QTimer.singleShot(50, accept_confirmation)
                QTest.mouseClick(dialog.delete_button, Qt.LeftButton)
                self.assertEqual(changes[-1], 'default')
                self.assertEqual(dialog.current_account, 'default')
                self.assertTrue(dialog.delete_button.isEnabled())
                self.assertFalse(dialog.rename_button.isEnabled())
                self.assertTrue(dialog.status_label.text())
                db = MapMarksDB(path)
                try:
                    self.assertEqual(db.list_accounts(), ['default', 'two'])
                finally:
                    db.close()
            finally:
                dialog.close()
                parent.close()
                dialog.deleteLater()
                parent.deleteLater()
                app.processEvents()

    def test_backup_failure_preserves_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'marks.db')
            db = MapMarksDB(path)
            try:
                db.create_account('one')
                db.add('chest', 'one')
                with self.assertRaises(FileExistsError):
                    db.rename_account('one', 'two', path)
                with self.assertRaises(FileExistsError):
                    db.delete_account('one', path)
                self.assertEqual(db.list_accounts(), ['default', 'one'])
                self.assertEqual(db.load_completed('one'), {'chest'})
            finally:
                db.close()

    def test_default_rename_delete_and_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'marks.db')
            db = MapMarksDB(path)
            db.add('legacy')
            db.create_account('second')
            db.rename_account('default', 'main', directory)
            db.close()
            db = MapMarksDB(path)
            try:
                self.assertEqual(db.list_accounts(), ['main', 'second'])
                self.assertEqual(db.load_completed('main'), {'legacy'})
                db.rename_account('main', 'default', directory)
                backup = db.delete_account('default', directory)
                self.assertEqual(db.list_accounts(), ['second'])
                restored = MapMarksDB(backup)
                try:
                    self.assertEqual(restored.load_completed(), {'legacy'})
                finally:
                    restored.close()
            finally:
                db.close()
            db = MapMarksDB(path)
            try:
                self.assertEqual(db.list_accounts(), ['second'])
                db.delete_account('second', directory)
                self.assertEqual(db.list_accounts(), ['default'])
                self.assertEqual(db.load_completed(), set())
            finally:
                db.close()

    def test_default_dialog_edit_and_delete(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'marks.db')
            db = MapMarksDB(path)
            db.add('legacy')
            db.create_account('second')
            db.close()
            parent = QWidget()
            parent.resize(900, 850)
            parent.show()
            changes = []
            dialog = CollectionAccountManager(path, 'default', app.tr, changes.append, parent)
            dialog.show()
            QTest.qWait(100)
            try:
                self.assertTrue(dialog.delete_button.isEnabled())
                dialog.name_edit.setText('main')
                self.assertTrue(dialog.rename_button.isEnabled())
                QTest.mouseClick(dialog.rename_button, Qt.LeftButton)
                self.assertEqual(changes[-1], 'main')
                self.assertNotIn('default', dialog.accounts)
                dialog.name_edit.setText('default')
                QTest.mouseClick(dialog.rename_button, Qt.LeftButton)
                self.assertEqual(changes[-1], 'default')

                def accept_confirmation():
                    message = next(child for child in dialog.findChildren(MessageBox)
                                   if child is not dialog and child.isVisible())
                    message.yesButton.click()

                QTimer.singleShot(50, accept_confirmation)
                QTest.mouseClick(dialog.delete_button, Qt.LeftButton)
                self.assertEqual(changes[-1], 'second')
                self.assertEqual(dialog.accounts, ['second'])
                self.assertEqual(dialog.current_account, 'second')
            finally:
                dialog.close()
                parent.close()
                dialog.deleteLater()
                parent.deleteLater()
                app.processEvents()


if __name__ == '__main__':
    unittest.main()
