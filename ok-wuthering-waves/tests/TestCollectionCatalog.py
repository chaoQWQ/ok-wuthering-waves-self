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
from src.utils.CollectionCatalog import load_collection_catalog, load_collection_maps, encode_collection_types
from src.utils.CollectionTypeDialog import CollectionTypeDialog, MapCollectionTypeDialog
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
                         len([entry for entry in self.catalog if entry.category_id == '3']))
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

    def test_maps_and_counts_match_database(self):
        maps = load_collection_maps(DATABASE)
        by_key = {entry.key: entry for entry in maps}
        self.assertEqual(by_key['912:1'].state_name, '梦枢天罗')
        self.assertEqual(by_key['912:1'].country_name, '瑝珑')
        self.assertNotIn('zscj', {entry.type_id for entry in by_key['912:1'].types})
        with closing(sqlite3.connect(DATABASE.as_uri() + '?immutable=1', uri=True)) as conn:
            for entry in maps:
                expected = dict(conn.execute(
                    'SELECT type_id,COUNT(*) FROM location '
                    'WHERE state_id=? AND country_id=? GROUP BY type_id',
                    (entry.state_id, entry.country_id),
                ).fetchall())
                self.assertEqual({item.type_id: item.count for item in entry.types}, expected)

    def test_map_dialog_switches_and_keeps_independent_selections(self):
        parent = QWidget()
        parent.resize(1000, 900)
        parent.show()
        maps = load_collection_maps(DATABASE)
        original = {'8:1': ['fls'], '912:1': ['qzx_01'], '8:4': ['zscj']}
        dialog = MapCollectionTypeDialog(maps, ['qzx_01'], original,
                                         '912:1', self.translate, parent)
        dialog.show()
        QTest.qWait(250)
        self.assertEqual(dialog.map_combo.currentText(), '梦枢天罗')
        self.assertEqual(dialog.selected_type_ids(), ['qzx_01'])
        self.assertEqual(len(dialog.entries), len(dialog.maps['912:1'].types))
        dialog.map_combo.setCurrentIndex(dialog.map_combo.findData('8:1'))
        self.assertEqual(dialog.selected_type_ids(), ['fls'])
        dialog.region_combo.setCurrentIndex(dialog.region_combo.findData(4))
        self.assertEqual(dialog.current_key, '8:4')
        self.assertEqual(dialog.selected_type_ids(), ['zscj'])
        dialog.region_combo.setCurrentIndex(dialog.region_combo.findData(1))
        dialog.map_combo.setCurrentIndex(dialog.map_combo.findData('912:1'))
        self.assertEqual(dialog.selected_type_ids(), ['qzx_01'])
        dialog.search_edit.setText('fls')
        self.assertFalse(any(not item.isHidden() for item in dialog.entries))
        dialog.search_edit.clear()
        screenshot = os.environ.get('COLLECTION_MAP_SCREENSHOT')
        if screenshot:
            self.app.processEvents()
            self.assertTrue(dialog.grab().save(screenshot))
        item = next(item for item in dialog.entries
                    if item.data(Qt.ItemDataRole.UserRole) == 'qzx_01')
        dialog.type_list.setCurrentItem(item)
        QTest.keyClick(dialog.type_list, Qt.Key.Key_Space)
        self.assertEqual(dialog.selected_map_types()['912:1'], [])
        self.assertEqual(original['912:1'], ['qzx_01'])
        QTest.mouseClick(dialog.yesButton, Qt.MouseButton.LeftButton)
        QTest.qWait(150)
        self.assertEqual(dialog.result(), 1)
        with tempfile.TemporaryDirectory() as directory:
            defaults = {'_Collection map types': {}, '_Collection map': ''}
            config = Config('collection', defaults, folder=directory)
            config['_Collection map types'] = dialog.selected_map_types()
            config['_Collection map'] = dialog.current_key
            restored = Config('collection', defaults, folder=directory)
            self.assertEqual(restored['_Collection map types']['912:1'], [])
            self.assertEqual(restored['_Collection map types']['8:1'], ['fls'])
        parent.close()

    def test_navigation_isolates_map_and_region_selection(self):
        selections = {'8:1': ['fls'], '8:4': ['zscj'], '912:1': []}
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'map_items.db'
            shutil.copyfile(DATABASE, target)
            overlay = MapItemOverlay(str(target))
            try:
                for state_id in (None, 8, 906, 912):
                    with closing(sqlite3.connect(str(target))) as conn:
                        rows = conn.execute(
                            'SELECT id,state_id,country_id,type_id FROM location'
                        ).fetchall()
                    expected = {
                        location_id for location_id, map_id, country_id, type_id in rows
                        if (state_id is None or map_id == state_id)
                        and type_id in selections.get(f'{map_id}:{country_id}', ['qzx_01'])
                    }
                    results = overlay.query_nearby(
                        0, 0, 1e12, type_filter=['qzx_01'], state_id=state_id,
                        with_location_id=True, map_type_filters=selections,
                    )
                    self.assertEqual({row[0] for row in results}, expected)
            finally:
                overlay.close()

    def test_official_categories_match_dream_map(self):
        types = load_collection_catalog(DATABASE, 912, 1)
        grouped = {}
        for entry in types:
            grouped.setdefault(entry.category_name, {})[entry.type_id] = entry.count
        self.assertEqual(grouped['收集物'], {
            'qzx_01': 22, 'qzx_02': 13, 'qzx_03': 26, 'qzx_04': 2, 'cx_03': 19,
        })
        self.assertEqual(len(grouped['探索']), 11)
        self.assertEqual(len(grouped['采集物']), 2)
        self.assertEqual(len(grouped['强敌']), 3)
        self.assertEqual(len(grouped['挑战']), 2)
        self.assertEqual(len(grouped['BOSS']), 1)
        self.assertTrue(all(entry.category_id != 'unclassified' for entry in self.catalog))

    def test_category_switch_keeps_hidden_choices(self):
        parent = QWidget()
        parent.resize(1100, 900)
        parent.show()
        dialog = MapCollectionTypeDialog(load_collection_maps(DATABASE), [], {},
                                         '912:1', self.translate, parent)
        dialog.show()
        QTest.qWait(250)
        visible = [item for item in dialog.entries if not item.isHidden()]
        self.assertEqual(len(visible), 5)
        self.assertEqual(dialog.category_list.currentItem().data(Qt.ItemDataRole.UserRole), '3')
        dialog.type_list.setCurrentItem(visible[0])
        QTest.keyClick(dialog.type_list, Qt.Key.Key_Space)
        selected_id = visible[0].data(Qt.ItemDataRole.UserRole)
        category = dialog.category_entries['ts']
        QTest.mouseClick(dialog.category_list.viewport(), Qt.MouseButton.LeftButton,
                         pos=dialog.category_list.visualItemRect(category).center())
        self.assertEqual(len([item for item in dialog.entries if not item.isHidden()]), 11)
        self.assertIn(selected_id, dialog.selected_type_ids())
        dialog.search_edit.setText('信标')
        self.assertEqual(len([item for item in dialog.entries if not item.isHidden()]), 2)
        dialog.search_edit.clear()
        dialog.category_list.setCurrentItem(dialog.category_entries['3'])
        self.assertIn(selected_id, dialog.selected_map_types()['912:1'])
        screenshot = os.environ.get('COLLECTION_CATEGORY_SCREENSHOT')
        if screenshot:
            self.app.processEvents()
            self.assertTrue(dialog.grab().save(screenshot))
        QTest.mouseClick(dialog.cancelButton, Qt.MouseButton.LeftButton)
        QTest.qWait(150)
        self.assertEqual(dialog.result(), 0)
        parent.close()


if __name__ == '__main__':
    unittest.main()
