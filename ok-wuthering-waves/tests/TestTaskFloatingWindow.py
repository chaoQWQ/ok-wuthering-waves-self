import os
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from config import basic_config_option, config
from ok import og
from ok.util.config import Config
from ok.util.GlobalConfig import GlobalConfig, register_basic_options
from src.gui.TaskFloatingWindow import TaskFloatingWindow, start_task_floating_window


class RealSampleTask:

    def __init__(self, name="Real Sample Task"):
        self.name = name
        self.enabled = True
        self.start_time = time.time() - 10
        self.info = {
            "Status Log": "Running normally in testing scene",
            "JEV Invocations": "1",
            "Echo Key": "q"
        }


class RealExecutorStub:

    def __init__(self, global_config):
        self.global_config = global_config
        self.current_task = None


class RealAppStub:

    @staticmethod
    def tr(key):
        return str(key)


class TestTaskFloatingWindow(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.old_config_folder = Config.config_folder
        self.temp_dir = tempfile.TemporaryDirectory()
        Config.config_folder = self.temp_dir.name

        self.global_config = GlobalConfig(config.get("global_configs"))
        register_basic_options(self.global_config, enable_blur=False)
        og.global_config = self.global_config
        og.app = RealAppStub()
        self.executor = RealExecutorStub(self.global_config)
        og.executor = self.executor
        self.floating_window = TaskFloatingWindow()

    def tearDown(self):
        self.floating_window.close_window()
        Config.config_folder = self.old_config_folder
        self.temp_dir.cleanup()

    def test_window_flags_and_attributes(self):
        flags = self.floating_window.windowFlags()
        self.assertTrue(bool(flags & Qt.WindowStaysOnTopHint))
        self.assertTrue(bool(flags & Qt.FramelessWindowHint))
        self.assertTrue(bool(flags & Qt.Tool))
        self.assertTrue(self.floating_window.testAttribute(Qt.WA_TranslucentBackground))
        self.assertTrue(self.floating_window.testAttribute(Qt.WA_ShowWithoutActivating))

    def test_config_option_registered(self):
        basic_config = self.global_config.get_config("Basic Options")
        self.assertIn("Show Task Floating Window", basic_config)
        self.assertTrue(basic_config.get("Show Task Floating Window"))

    def test_window_hidden_when_no_task(self):
        self.executor.current_task = None
        self.floating_window.update_status()
        self.assertFalse(self.floating_window.isVisible())

    def test_window_shows_and_updates_when_task_running(self):
        task = RealSampleTask()
        self.executor.current_task = task
        self.floating_window.update_status()

        self.assertTrue(self.floating_window.isVisible())
        self.assertIn(task.name, self.floating_window.title_label.text())

        table = self.floating_window.table
        self.assertEqual(table.rowCount(), len(task.info))
        self.assertEqual(table.item(0, 0).text(), "Status Log")
        self.assertEqual(table.item(0, 1).text(), "Running normally in testing scene")

    def test_window_hides_when_disabled_in_config(self):
        task = RealSampleTask()
        self.executor.current_task = task
        self.floating_window.update_status()
        self.assertTrue(self.floating_window.isVisible())

        basic_config = self.global_config.get_config("Basic Options")
        basic_config["Show Task Floating Window"] = False
        self.floating_window.update_status()
        self.assertFalse(self.floating_window.isVisible())

    def test_close_button_dismisses_current_run(self):
        task = RealSampleTask()
        self.executor.current_task = task
        self.floating_window.update_status()
        self.assertTrue(self.floating_window.isVisible())

        self.floating_window.on_close_clicked()
        self.assertFalse(self.floating_window.isVisible())

        # 同一任务运行周期内再次触发更新应当保持隐藏
        self.floating_window.update_status()
        self.assertFalse(self.floating_window.isVisible())

        # 新任务或者新运行周期应当重新显示
        new_task = RealSampleTask(name="Second Quest Task")
        new_task.start_time = time.time()
        self.executor.current_task = new_task
        self.floating_window.update_status()
        self.assertTrue(self.floating_window.isVisible())
        self.assertIn("Second Quest Task", self.floating_window.title_label.text())

    def test_position_bottom_right(self):
        self.floating_window.position_bottom_right()
        screen = QApplication.primaryScreen()
        if screen is not None:
            available_geometry = screen.availableGeometry()
            expected_x = available_geometry.right() - self.floating_window.width() - 20 + 1
            expected_y = available_geometry.bottom() - self.floating_window.height() - 20 + 1
            self.assertEqual(self.floating_window.x(), expected_x)
            self.assertEqual(self.floating_window.y(), expected_y)
