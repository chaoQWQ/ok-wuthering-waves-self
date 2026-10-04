import gettext
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from src.utils.KuroRouteDialog import KuroRouteDialog
from src.utils.KuroRoutes import KuroRoute, KuroRoutesClient, RouteNavigator
from src.utils.MapMarksDB import MapMarksDB


ROOT = Path(__file__).resolve().parents[1]
ROUTE_ID = '1525166815673057280'


class TestKuroRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = KuroRoutesClient(8)
        cls.data = cls.client.detail(ROUTE_ID)
        cls.route = KuroRoute.from_data(cls.data)
        cls.app = QApplication.instance() or QApplication([])
        cls.translate = staticmethod(gettext.translation(
            'ok', localedir=ROOT / 'i18n', languages=['zh_CN']).gettext)

    def test_live_search_and_ordering(self):
        for sort_type in (1, 2):
            page = self.client.search('梦州满探', sort_type)
            self.assertGreater(page['total'], 0)
            self.assertTrue(all('梦州' in row['name'] and '满探' in row['name']
                                for row in page['records']))
        first = self.client.search(page=1)
        latest = self.client.search(sort_type=1)
        self.assertNotEqual([row['id'] for row in first['records']],
                            [row['id'] for row in latest['records']])
        second = self.client.search(page=2)
        self.assertEqual(first['current'], 1)
        self.assertEqual(second['current'], 2)
        self.assertFalse({row['id'] for row in first['records']} &
                         {row['id'] for row in second['records']})

    def test_live_detail_order_and_sections(self):
        self.assertEqual(self.route.route_id, ROUTE_ID)
        self.assertEqual(len(self.route.path.sections), len(self.data['sectionList']))
        for section, raw in zip(self.route.path.sections, self.data['sectionList']):
            self.assertEqual([node.position_id for node in section.nodes],
                             [str(point['positionId']) for point in raw['positionList']])
            for node, point in zip(section.nodes, raw['positionList']):
                self.assertEqual((node.x, node.y), (point['xposition'], point['yposition']))

    def test_live_unavailable_route_is_rejected(self):
        with self.assertRaises(ValueError):
            self.client.detail('0')

    def test_profiles_confirmation_skip_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            database = MapMarksDB(str(Path(directory) / 'marks.db'))
            try:
                navigation = RouteNavigator(self.route)
                first = navigation.target(database.load_completed('account-a'))
                for _ in range(100):
                    self.assertEqual(navigation.target(set()), first)
                database.add(first.position_id, 'account-a')
                second = navigation.target(database.load_completed('account-a'))
                self.assertNotEqual(first, second)
                navigation.skip()
                self.assertNotEqual(navigation.target(database.load_completed('account-a')), second)
                self.assertNotIn(second.position_id, database.load_completed('account-a'))
                resumed = RouteNavigator(self.route)
                self.assertEqual(resumed.target(database.load_completed('account-a')), second)
                other = RouteNavigator(self.route)
                self.assertEqual(other.target(database.load_completed('account-b')), first)
            finally:
                database.close()

    def test_section_selection_and_exhaustion(self):
        section = self.route.path.sections[1]
        navigation = RouteNavigator(self.route, section.section_id)
        self.assertEqual(navigation.target(set()), section.nodes[0])
        self.assertEqual(len(navigation.nodes), len(section.nodes))
        completed = {node.position_id for node in section.nodes}
        self.assertIsNone(navigation.target(completed))
        self.assertEqual(navigation.remaining(completed), 0)

    def test_real_task_controller_confirmation_and_map_gate(self):
        from config import config
        from ok import Config
        from ok.device.DeviceManager import DeviceManager
        from ok.task.TaskExecutor import TaskExecutor
        from ok.util.GlobalConfig import GlobalConfig
        from ok.util.handler import ExitEvent
        from src.task.MapOverlayTask import MapOverlayTask, OverlayController
        from src.utils.MapItemOverlay import MapItemOverlay
        from src.utils.NodeIconCache import NodeIconCache
        from ok import Box

        with tempfile.TemporaryDirectory() as directory:
            previous_folder = Config.config_folder
            Config.config_folder = directory
            exit_event = ExitEvent()
            controller = None
            manager = None
            executor = None
            try:
                global_config = GlobalConfig(config['global_configs'])
                manager = DeviceManager({}, exit_event, global_config)
                executor = TaskExecutor(manager, exit_event=exit_event,
                                        global_config=global_config, config={'locale': 'zh_CN'})
                task = MapOverlayTask(executor=executor, app=self.app)
                task.config = Config('route-test', task.default_config, folder=directory)
                task.config['_Kuro route'] = self.data
                task.config['Kuro route navigation'] = True
                restored = Config('route-test', task.default_config, folder=directory)
                self.assertTrue(restored['Kuro route navigation'])
                self.assertEqual(KuroRoute.from_data(restored['_Kuro route']), self.route)
                resource = Path(directory) / 'map_items.db'
                shutil.copyfile(ROOT / 'assets' / 'stitched' / 'map_items.db', resource)
                task._overlay = MapItemOverlay(str(resource))
                controller = OverlayController(task)
                task._overlay_controller = controller
                controller._marks_db = MapMarksDB(str(Path(directory) / 'marks.db'))
                controller._icon_cache = NodeIconCache(str(Path(directory) / 'icons'))
                controller._update_chest_search(None, self.route.path.state_id)
                first = controller._chest_target
                self.assertEqual(first.location_id, self.route.path.sections[0].nodes[0].position_id)
                position = (first.x / 100, first.y / 100)
                for _ in range(30):
                    controller._update_chest_search(position, self.route.path.state_id)
                    self.assertEqual(controller._chest_target.location_id, first.location_id)
                task._locked_map_id = str(self.route.path.state_id)
                minimap_items = task._build_kuro_minimap_items(
                    first.x, first.y, Box(20, 20, 150, 150), 0.01, set())
                self.assertIn(first.location_id, {item[6] for item in minimap_items})
                completed_items = task._build_kuro_minimap_items(
                    first.x, first.y, Box(20, 20, 150, 150), 0.01, {first.location_id})
                self.assertNotIn(first.location_id, {item[6] for item in completed_items})
                layers, items, boxes, targets = controller._build_visible_path(
                    first.x, first.y, 0.01, 400, 300, (0, 0, 800, 600))
                self.assertGreater(len(items), 0)
                self.assertTrue(all(target.kind == 'item' for target in targets))
                self.assertIsNotNone(controller._compute_target_marker(targets))
                controller.on_chest_confirm_hotkey()
                controller._process_chest_confirm(position, self.route.path.state_id)
                self.assertIn(first.location_id, controller._marks_db.load_completed('default'))
                controller._update_chest_search(position, self.route.path.state_id)
                second = controller._chest_target
                self.assertNotEqual(first.location_id, second.location_id)
                controller.on_advance_hotkey()
                controller._update_chest_search(position, self.route.path.state_id)
                self.assertNotEqual(second.location_id, controller._chest_target.location_id)
                self.assertNotIn(second.location_id, controller._marks_db.load_completed('default'))
                controller._update_chest_search(position, 912)
                self.assertIsNone(controller._chest_target)
                task.config['Collection account'] = 'other-account'
                controller._update_chest_search(position, self.route.path.state_id)
                self.assertEqual(controller._chest_target.location_id, first.location_id)
                self.assertEqual(controller._marks_db.load_completed('other-account'), set())
            finally:
                if controller:
                    controller.close()
                if 'task' in locals() and task._overlay:
                    task._overlay.close()
                if manager:
                    manager.close()
                if executor:
                    executor.destroy()
                exit_event.set()
                Config.config_folder = previous_folder

    def wait_until(self, predicate):
        deadline = time.monotonic() + 45
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(25)
        self.assertTrue(predicate())

    def test_real_dialog_search_select_load(self):
        parent = QWidget()
        parent.resize(1100, 900)
        parent.show()
        dialog = KuroRouteDialog(8, self.translate, parent)
        dialog.show()
        try:
            self.wait_until(lambda: not dialog.requests)
            self.assertGreater(dialog.route_list.count(), 0)
            dialog.search_edit.setText('梦州满探')
            QTest.keyClick(dialog.search_edit, Qt.Key.Key_Return)
            self.wait_until(lambda: not dialog.requests)
            self.assertGreater(dialog.route_list.count(), 0)
            self.assertTrue(all('梦州' in dialog.route_list.item(index).data(Qt.ItemDataRole.UserRole + 1)
                                for index in range(dialog.route_list.count())))
            rectangle = dialog.route_list.visualItemRect(dialog.route_list.item(0))
            QTest.mouseClick(dialog.route_list.viewport(), Qt.MouseButton.LeftButton,
                             pos=rectangle.center())
            self.wait_until(lambda: not dialog.requests)
            self.assertIsNotNone(dialog.route_data)
            self.assertTrue(dialog.yesButton.isEnabled())
            self.assertGreater(dialog.section_combo.count(), 1)
            screenshot = os.environ.get('KURO_ROUTE_SCREENSHOT')
            if screenshot:
                self.app.processEvents()
                self.assertTrue(dialog.grab().save(screenshot))
            dialog.section_combo.setCurrentIndex(1)
            selected = dialog.section_combo.currentData()
            QTest.mouseClick(dialog.yesButton, Qt.MouseButton.LeftButton)
            QTest.qWait(200)
            self.assertEqual(dialog.section_id, selected)
            self.assertEqual(dialog.result(), dialog.DialogCode.Accepted)
        finally:
            dialog.close()
            parent.close()
            dialog.deleteLater()
            parent.deleteLater()
            QTest.qWait(200)
            self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
