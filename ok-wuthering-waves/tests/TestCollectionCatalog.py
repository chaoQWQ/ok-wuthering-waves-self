import gettext
import hashlib
import os
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from ok import Config

from src.task.MapOverlayTask import collection_type_ids, selected_collection_type_ids
from src.utils.CollectionCatalog import load_collection_catalog, encode_collection_types
from src.utils.CollectionTypeDialog import CollectionTypeDialog
from src.utils.MapItemOverlay import MapItemOverlay


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / 'assets' / 'stitched' / 'map_items.db'


class TestCollectionCatalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.catalog = load_collection_catalog(DATABASE)
        cls.translate = staticmethod(gettext.translation(
            'ok', localedir=ROOT / 'i18n', languages=['zh_CN']
        ).gettext)

    def test_catalog_matches_installed_resource(self):
        digest = hashlib.sha256(DATABASE.read_bytes()).digest()
        catalog = load_collection_catalog(DATABASE)
        with closing(sqlite3.connect(DATABASE.as_uri() + '?immutable=1', uri=True)) as conn:
            count = conn.execute(
                'SELECT COUNT(DISTINCT type_id) FROM location '
                'WHERE type_id IS NOT NULL AND type_id != \'\''
            ).fetchone()[0]
        self.assertEqual(len(catalog), count)
        by_id = {entry.type_id: entry for entry in catalog}
        self.assertEqual(by_id['fsc'].name, '伏霜虫')
        self.assertEqual(by_id['zscj'].name, '终声残卷')
        self.assertGreater(by_id['zscj'].count, 0)
        self.assertEqual(digest, hashlib.sha256(DATABASE.read_bytes()).digest())

    def test_resource_replacement_is_read_on_next_open(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'map_items.db'
            shutil.copyfile(DATABASE, target)
            self.assertEqual(load_collection_catalog(target), self.catalog)
            replacement = Path(directory) / 'replacement.db'
            shutil.copyfile(DATABASE, replacement)
            os.replace(replacement, target)
            self.assertEqual(load_collection_catalog(target), self.catalog)

    def test_legacy_and_resource_type_selection(self):
        self.assertEqual(collection_type_ids(['Chests']), (
            'qzx_01', 'qzx_02', 'qzx_03', 'qzx_04',
        ))
        ids = ['fsc', 'zscj', 'qzx_01']
        self.assertEqual(selected_collection_type_ids({
            'Collection types': encode_collection_types(ids),
        }), tuple(ids))
        self.assertEqual(collection_type_ids(['Chests', 'type:qzx_01']),
                         collection_type_ids(['Chests']))
        self.assertEqual(collection_type_ids([]), ('__no_collection_type__',))
        with self.assertRaises(ValueError):
            collection_type_ids(['type:'])

    def test_real_dialog_search_selection_and_cancel(self):
        parent = QWidget()
        parent.resize(1000, 800)
        parent.show()
        dialog = CollectionTypeDialog(self.catalog, ['fls'], self.translate, parent)
        dialog.show()
        QTest.qWait(250)
        self.assertEqual(dialog.title_label.text(), '收集物类型')
        self.assertEqual(dialog.selected_type_ids(), ['fls'])
        dialog.search_edit.setText('终声残卷')
        visible = [item for item in dialog.entries if not item.isHidden()]
        self.assertTrue(all('终声残卷' in item.text() for item in visible))
        dialog.search_edit.setText('zscj')
        visible = [item for item in dialog.entries if not item.isHidden()]
        self.assertEqual(len(visible), 1)
        self.assertEqual(visible[0].data(Qt.ItemDataRole.UserRole), 'zscj')
        dialog.type_list.setCurrentItem(visible[0])
        dialog.type_list.setFocus()
        QTest.keyClick(dialog.type_list, Qt.Key.Key_Space)
        self.assertEqual(set(dialog.selected_type_ids()), {'fls', 'zscj'})
        dialog.search_edit.clear()
        self.assertEqual(len([item for item in dialog.entries if not item.isHidden()]),
                         len(self.catalog))
        screenshot = os.environ.get('COLLECTION_DIALOG_SCREENSHOT')
        if screenshot:
            self.app.processEvents()
            self.assertTrue(dialog.grab().save(screenshot))
        QTest.mouseClick(dialog.cancelButton, Qt.MouseButton.LeftButton)
        QTest.qWait(150)
        self.assertEqual(dialog.result(), 0)
        parent.close()

    def test_save_uses_type_ids_and_preserves_unavailable_selection(self):
        parent = QWidget()
        parent.resize(1000, 800)
        parent.show()
        # 使用实际资源中的类别检查资源暂缺时保留选择的行为。
        catalog = [entry for entry in self.catalog if entry.type_id != 'fls']
        dialog = CollectionTypeDialog(catalog, ['fls', 'zscj'], self.translate, parent)
        dialog.show()
        QTest.qWait(250)
        QTest.mouseClick(dialog.yesButton, Qt.MouseButton.LeftButton)
        QTest.qWait(150)
        self.assertEqual(dialog.result(), 1)
        self.assertEqual(set(collection_type_ids(encode_collection_types(
            dialog.selected_type_ids()))), {'fls', 'zscj'})
        parent.close()

    def test_selection_persists_with_real_config(self):
        with tempfile.TemporaryDirectory() as directory:
            defaults = {'Collection types': ['Chests']}
            config = Config('collection', defaults, folder=directory)
            config['Collection types'] = encode_collection_types(['fsc', 'zscj'])
            restored = Config('collection', defaults, folder=directory)
            self.assertEqual(selected_collection_type_ids(restored), ('fsc', 'zscj'))

    def test_resource_types_feed_navigation_query(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'map_items.db'
            shutil.copyfile(DATABASE, target)
            overlay = MapItemOverlay(str(target))
            try:
                for type_id in ('fsc', 'zscj'):
                    with closing(sqlite3.connect(str(target))) as conn:
                        location_id, state_id, x, y = conn.execute(
                            'SELECT id,state_id,x,y FROM location WHERE type_id=? LIMIT 1',
                            (type_id,),
                        ).fetchone()
                    results = overlay.query_nearby(
                        x, y, 1, type_filter=collection_type_ids(
                            encode_collection_types([type_id])),
                        state_id=state_id, with_location_id=True,
                    )
                    self.assertTrue(any(row[0] == location_id for row in results))
                    self.assertTrue(all(row[2] == type_id for row in results))
            finally:
                overlay.close()

    def test_empty_selection_and_missing_resource(self):
        parent = QWidget()
        dialog = CollectionTypeDialog(self.catalog, [], self.translate, parent)
        self.assertEqual(dialog.selected_type_ids(), [])
        dialog.close()
        parent.close()
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / 'map_items.db'
            with self.assertRaises(FileNotFoundError):
                load_collection_catalog(missing)
            self.assertFalse(missing.exists())


if __name__ == '__main__':
    unittest.main()
