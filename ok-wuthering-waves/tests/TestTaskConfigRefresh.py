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
from src.task.MapOverlayTask import MapOverlayTask
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
            finally:
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
