import tempfile
import unittest

from PySide6.QtWidgets import QApplication, QWidget
from qfluentwidgets import FluentIcon, NavigationItemPosition

from ok import Config, og
from ok.device.DeviceManager import DeviceManager
from ok.task.TaskExecutor import TaskExecutor
from ok.ui.qt.tasks.TaskCard import TaskCard
from ok.ui.qt.tasks.TriggerTaskTab import TriggerTaskTab
from ok.util.GlobalConfig import GlobalConfig
from ok.util.handler import ExitEvent

from config import config
from src.gui.MapExplorationTab import MapExplorationTab
from src.task.MapOverlayTask import MapOverlayTask
from src.utils.TaskConfigRefresh import refresh_task_config_widgets


class TestMapExplorationTab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        og.app = cls.app

    def test_map_exploration_tab_properties(self):
        tab = MapExplorationTab()
        self.assertEqual(tab.name, "地图探索")
        self.assertEqual(tab.icon, FluentIcon.GLOBE)
        self.assertFalse(tab.add_after_default_tabs)
        self.assertEqual(tab.position, NavigationItemPosition.SCROLL)

    def test_map_overlay_task_not_in_trigger_tab(self):
        with tempfile.TemporaryDirectory() as directory:
            Config.config_folder = directory
            exit_event = ExitEvent()
            global_config = GlobalConfig(config["global_configs"])
            manager = DeviceManager({}, exit_event, global_config)
            executor = TaskExecutor(
                manager, exit_event=exit_event,
                global_config=global_config, config={"locale": "zh_CN"}
            )
            og.executor = executor
            task = MapOverlayTask(executor=executor, app=self.app)
            task.config = Config("test-map-task", task.default_config, folder=directory)
            executor.trigger_tasks = [task]

            # 验证地图探索任务被设置为不可见，不出现在实时触发栏
            self.assertFalse(task.visible)

            trigger_tab = TriggerTaskTab()
            # trigger_tab 中应该没有 MapOverlayTask 的卡片
            trigger_card_tasks = [card.task for card in trigger_tab.card_widgets]
            self.assertNotIn(task, trigger_card_tasks)

    def test_map_exploration_tab_card_integration(self):
        with tempfile.TemporaryDirectory() as directory:
            Config.config_folder = directory
            exit_event = ExitEvent()
            global_config = GlobalConfig(config["global_configs"])
            manager = DeviceManager({}, exit_event, global_config)
            executor = TaskExecutor(
                manager, exit_event=exit_event,
                global_config=global_config, config={"locale": "zh_CN"}
            )
            og.executor = executor
            task = MapOverlayTask(executor=executor, app=self.app)
            task.config = Config("test-map-task-2", task.default_config, folder=directory)
            executor.trigger_tasks = [task]

            tab = MapExplorationTab()
            tab.executor = executor

            self.assertIsNotNone(tab.card)
            self.assertIsInstance(tab.card, TaskCard)
            self.assertEqual(tab.card.task, task)
            self.assertTrue(tab.card.isExpand)

            # 验证控件刷新机制兼容独立标签页
            parent = QWidget()
            parent.layout = None
            tab.setParent(parent)
            task.config["Collection account"] = "default"
            refresh_task_config_widgets(task, parent, self.app.tr)
            self.assertEqual(tab.card.config_widget_by_key["Collection account"].combo_box.currentText(), "default")


if __name__ == "__main__":
    unittest.main()
