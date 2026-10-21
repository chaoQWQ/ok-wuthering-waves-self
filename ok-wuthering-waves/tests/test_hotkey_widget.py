import unittest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from src.utils.HotkeyWidget import (
    HotkeyLineEdit,
    LabelAndHotkey,
    patch_config_item_factory,
    qt_key_event_to_hotkey,
)


class TestHotkeyWidget(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ok import og
        cls.app = QApplication.instance() or QApplication([])
        og.app = cls.app

    def test_qt_key_event_to_hotkey_function_keys(self):
        # 单独按 F9
        ev_f9 = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_F9, Qt.KeyboardModifier.NoModifier)
        hotkey, is_mod = qt_key_event_to_hotkey(ev_f9)
        self.assertFalse(is_mod)
        self.assertEqual(hotkey, "<f9>")

        # Ctrl+F9
        ev_ctrl_f9 = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_F9, Qt.KeyboardModifier.ControlModifier)
        hotkey, is_mod = qt_key_event_to_hotkey(ev_ctrl_f9)
        self.assertFalse(is_mod)
        self.assertEqual(hotkey, "<ctrl>+<f9>")

        # Ctrl+F8
        ev_ctrl_f8 = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_F8, Qt.KeyboardModifier.ControlModifier)
        hotkey, is_mod = qt_key_event_to_hotkey(ev_ctrl_f8)
        self.assertFalse(is_mod)
        self.assertEqual(hotkey, "<ctrl>+<f8>")

        # Ctrl+F10
        ev_ctrl_f10 = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_F10, Qt.KeyboardModifier.ControlModifier)
        hotkey, is_mod = qt_key_event_to_hotkey(ev_ctrl_f10)
        self.assertFalse(is_mod)
        self.assertEqual(hotkey, "<ctrl>+<f10>")

        # Ctrl+Alt+Shift+F12
        mods = (
            Qt.KeyboardModifier.ControlModifier
            | Qt.KeyboardModifier.AltModifier
            | Qt.KeyboardModifier.ShiftModifier
        )
        ev_multi = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_F12, mods)
        hotkey, is_mod = qt_key_event_to_hotkey(ev_multi)
        self.assertFalse(is_mod)
        self.assertEqual(hotkey, "<ctrl>+<alt>+<shift>+<f12>")

    def test_qt_key_event_to_hotkey_modifiers_alone(self):
        for mod_key, mod_flag in [
            (Qt.Key.Key_Control, Qt.KeyboardModifier.ControlModifier),
            (Qt.Key.Key_Alt, Qt.KeyboardModifier.AltModifier),
            (Qt.Key.Key_Shift, Qt.KeyboardModifier.ShiftModifier),
            (Qt.Key.Key_Meta, Qt.KeyboardModifier.MetaModifier),
        ]:
            ev = QKeyEvent(QKeyEvent.Type.KeyPress, mod_key, mod_flag)
            hotkey, is_mod = qt_key_event_to_hotkey(ev)
            self.assertTrue(is_mod)
            self.assertIsNone(hotkey)

    def test_qt_key_event_to_hotkey_letters_and_digits(self):
        ev_ctrl_a = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        hotkey, is_mod = qt_key_event_to_hotkey(ev_ctrl_a)
        self.assertFalse(is_mod)
        self.assertEqual(hotkey, "<ctrl>+a")

        ev_1 = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_1, Qt.KeyboardModifier.NoModifier)
        hotkey, is_mod = qt_key_event_to_hotkey(ev_1)
        self.assertFalse(is_mod)
        self.assertEqual(hotkey, "1")

    def test_hotkey_line_edit_recording_and_commit(self):
        edit = HotkeyLineEdit()
        edit.setText("<ctrl>+<f9>")
        self.assertEqual(edit.text(), "<ctrl>+<f9>")

        # 获得焦点开始录制
        edit._start_recording()
        self.assertTrue(edit._recording)
        self.assertEqual(edit.text(), "")

        # 按下 Ctrl 键显示中间状态
        ev_ctrl = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Control, Qt.KeyboardModifier.ControlModifier)
        edit.keyPressEvent(ev_ctrl)
        self.assertEqual(edit.text(), "<ctrl>+...")

        # 录制结果接收
        emitted = []
        edit.hotkey_committed.connect(emitted.append)

        # 按下 F9 完成组合键
        ev_ctrl_f9 = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_F9, Qt.KeyboardModifier.ControlModifier)
        edit.keyPressEvent(ev_ctrl_f9)
        self.assertFalse(edit._recording)
        self.assertEqual(edit.text(), "<ctrl>+<f9>")
        self.assertEqual(emitted, ["<ctrl>+<f9>"])

    def test_hotkey_line_edit_escape_cancels(self):
        edit = HotkeyLineEdit()
        edit.setText("<ctrl>+<f8>")
        edit._start_recording()
        self.assertEqual(edit.text(), "")

        ev_esc = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        edit.keyPressEvent(ev_esc)
        self.assertFalse(edit._recording)
        self.assertEqual(edit.text(), "<ctrl>+<f8>")

    def test_hotkey_line_edit_backspace_clears(self):
        edit = HotkeyLineEdit()
        edit.setText("<ctrl>+<f10>")
        edit._start_recording()

        emitted = []
        edit.hotkey_committed.connect(emitted.append)

        ev_backspace = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Backspace, Qt.KeyboardModifier.NoModifier)
        edit.keyPressEvent(ev_backspace)
        self.assertFalse(edit._recording)
        self.assertEqual(edit.text(), "")
        self.assertEqual(emitted, [""])

    def test_label_and_hotkey_widget(self):
        config = {"Advance hotkey": "<ctrl>+<f9>"}
        widget = LabelAndHotkey({}, config, "Advance hotkey")
        self.assertEqual(widget.line_edit.text(), "<ctrl>+<f9>")

        # 模拟通过按键录制设置新快捷键
        widget.line_edit._start_recording()
        ev = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_F11, Qt.KeyboardModifier.ControlModifier)
        widget.line_edit.keyPressEvent(ev)

        self.assertEqual(widget.line_edit.text(), "<ctrl>+<f11>")
        self.assertEqual(config["Advance hotkey"], "<ctrl>+<f11>")

    def test_patch_config_item_factory(self):
        patch_config_item_factory()
        import ok.ui.qt.tasks.ConfigItemFactory as cif

        class DummyTask:
            pass

        class DummyConfig(dict):
            def get_default(self, key):
                return self.get(key)

        config = DummyConfig({"Advance hotkey": "<ctrl>+<f9>"})
        widget = cif.config_widget(
            {"Advance hotkey": {"type": "hotkey"}},
            {},
            config,
            "Advance hotkey",
            "<ctrl>+<f9>",
            DummyTask(),
        )
        self.assertIsInstance(widget, LabelAndHotkey)


    def test_map_overlay_task_hotkey_widgets(self):
        import tempfile
        from ok import Config, og
        from ok.device.DeviceManager import DeviceManager
        from ok.task.TaskExecutor import TaskExecutor
        from ok.ui.qt.tasks.TaskCard import TaskCard
        from ok.util.GlobalConfig import GlobalConfig
        from ok.util.handler import ExitEvent
        from config import config
        from src.task.MapOverlayTask import MapOverlayTask

        with tempfile.TemporaryDirectory() as directory:
            Config.config_folder = directory
            exit_event = ExitEvent()
            global_config = GlobalConfig(config['global_configs'])
            manager = DeviceManager({}, exit_event, global_config)
            executor = TaskExecutor(
                manager, exit_event=exit_event,
                global_config=global_config, config={'locale': 'zh_CN'}
            )
            og.app = self.app
            og.executor = executor
            task = MapOverlayTask(executor=executor, app=self.app)
            task.config = Config("test-hotkey-task", task.default_config, folder=directory)
            card = TaskCard(task, False)

            advance_widget = card.config_widget_by_key.get("Advance hotkey")
            map_toggle_widget = card.config_widget_by_key.get("Map display toggle hotkey")
            chest_confirm_widget = card.config_widget_by_key.get("Chest confirm hotkey")

            self.assertIsInstance(advance_widget, LabelAndHotkey)
            self.assertIsInstance(map_toggle_widget, LabelAndHotkey)
            self.assertIsInstance(chest_confirm_widget, LabelAndHotkey)

            self.assertEqual(advance_widget.line_edit.text(), "<ctrl>+<f9>")
            self.assertEqual(map_toggle_widget.line_edit.text(), "<ctrl>+<f8>")
            self.assertEqual(chest_confirm_widget.line_edit.text(), "<ctrl>+<f10>")


if __name__ == "__main__":
    unittest.main()
