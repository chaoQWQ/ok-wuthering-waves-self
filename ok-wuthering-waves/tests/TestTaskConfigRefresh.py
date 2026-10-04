import tempfile
import unittest

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
from ok import Config, og
from ok.device.DeviceManager import DeviceManager
from ok.task.TaskExecutor import TaskExecutor
from ok.ui.qt.tasks.TaskCard import TaskCard
from ok.util.GlobalConfig import GlobalConfig
from ok.util.handler import ExitEvent

from config import config
from src.task.MapOverlayTask import ChestTarget, MapOverlayTask, OverlayController
from src.utils.TaskConfigRefresh import refresh_task_config_widgets


class TestTaskConfigRefresh(unittest.TestCase):
    def test_account_and_route_updates_preserve_visible_card(self):
        app = QApplication.instance() or QApplication([])
        saved_globals = (og.app, og.executor, og.main_window)
        saved_folder = Config.config_folder
        with tempfile.TemporaryDirectory() as directory:
            Config.config_folder = directory
            exit_event = ExitEvent()
            manager = None
            executor = None
            parent = QWidget()
            card = None
            controller = None
            try:
                global_config = GlobalConfig(config['global_configs'])
                manager = DeviceManager({}, exit_event, global_config)
                executor = TaskExecutor(manager, exit_event=exit_event,
                                        global_config=global_config, config={'locale': 'zh_CN'})
                og.app = app
                og.executor = executor
                og.main_window = parent
                task = MapOverlayTask(executor=executor, app=app)
                task.config = Config('refresh-test', task.default_config, folder=directory)
                card = TaskCard(task, False)
                layout = QVBoxLayout(parent)
                layout.addWidget(card)
                parent.resize(1000, 850)
                parent.show()
                card.setExpand(True)
                QTest.qWait(250)
                handle = int(card.winId())
                account_widget = card.config_widget_by_key['Collection account']
                combo = account_widget.combo_box
                for account in ('account-a', 'account-b', 'default'):
                    task.config_type['Collection account']['options'] = ['default', 'account-a', 'account-b']
                    task.config['Collection account'] = account
                    refresh_task_config_widgets(task, parent, app.tr)
                    app.processEvents()
                    self.assertEqual(combo.count(), 3)
                    self.assertEqual(combo.currentText(), account)
                    self.assertEqual(task.config['Collection account'], account)
                    self.assertTrue(card.isVisible())
                    self.assertEqual(int(card.winId()), handle)
                    self.assertEqual(parent.findChildren(TaskCard), [card])
                combo.setCurrentIndex(2)
                self.assertEqual(task.config['Collection account'], 'account-b')
                task.config['Kuro route navigation'] = True
                refresh_task_config_widgets(task, parent, app.tr)
                self.assertTrue(card.config_widget_by_key['Kuro route navigation'].switch_button.isChecked())
                self.assertTrue(card.isVisible())
                self.assertEqual(int(card.winId()), handle)
                restored = Config('refresh-test', task.default_config, folder=directory)
                self.assertEqual(restored['Collection account'], 'account-b')
                task.config['Kuro route navigation'] = False
                task.config['Chest search'] = True
                task._last_valid = (-831, 327, 0)
                task._player_map_id = 8
                task._locked_map_id = 8
                controller = OverlayController(task)
                controller._chest_target = ChestTarget('1287514641007132672', '基准奇藏箱',
                                                       'qzx_02', -81100, 32700, 2000)
                role = task.player_position_for_map()
                lines = controller.compute_status_lines((-831, 327, 0), minimap=False, game_scale=55)
                moved_view = controller.compute_status_lines((-600, 200, 0), minimap=False, game_scale=55)
                self.assertEqual(role, task.player_position_for_map())
                self.assertEqual(lines[0], moved_view[0])
                self.assertEqual(lines[-1], moved_view[-1])
                self.assertNotEqual(lines[-2], moved_view[-2])
                self.assertIn('最近一次大世界识别', lines[0])
                self.assertIn('视图中心', lines[-2])
                task._locked_map_id = 912
                self.assertIsNone(task.player_position_for_map())
                unknown = controller.compute_status_lines((-600, 200, 0), minimap=False, game_scale=55)
                self.assertIn('未知', unknown[0])
                self.assertIn('距离未知', unknown[-1])
            finally:
                if controller is not None:
                    controller.close()
                parent.close()
                if card is not None:
                    card.dispose()
                parent.deleteLater()
                QTest.qWait(100)
                if manager is not None:
                    manager.close()
                if executor is not None:
                    executor.destroy()
                exit_event.set()
                og.app, og.executor, og.main_window = saved_globals
                Config.config_folder = saved_folder


if __name__ == '__main__':
    unittest.main()
