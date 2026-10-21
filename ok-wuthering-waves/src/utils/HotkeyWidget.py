from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics, QKeyEvent
from qfluentwidgets import LineEdit

from ok import Logger, og
from ok.ui.qt.common.design_system import DesignToken, control_width
from ok.ui.qt.tasks.ConfigLabelAndWidget import ConfigLabelAndWidget

logger = Logger.get_logger(__name__)

MODIFIER_KEYS = {
    Qt.Key.Key_Control,
    Qt.Key.Key_Shift,
    Qt.Key.Key_Alt,
    Qt.Key.Key_Meta,
}

SPECIAL_KEY_MAP = {
    Qt.Key.Key_Space: "<space>",
    Qt.Key.Key_Tab: "<tab>",
    Qt.Key.Key_Return: "<enter>",
    Qt.Key.Key_Enter: "<enter>",
    Qt.Key.Key_Backspace: "<backspace>",
    Qt.Key.Key_Delete: "<delete>",
    Qt.Key.Key_Insert: "<insert>",
    Qt.Key.Key_Home: "<home>",
    Qt.Key.Key_End: "<end>",
    Qt.Key.Key_PageUp: "<page_up>",
    Qt.Key.Key_PageDown: "<page_down>",
    Qt.Key.Key_Left: "<left>",
    Qt.Key.Key_Right: "<right>",
    Qt.Key.Key_Up: "<up>",
    Qt.Key.Key_Down: "<down>",
    Qt.Key.Key_Minus: "-",
    Qt.Key.Key_Equal: "=",
    Qt.Key.Key_BracketLeft: "[",
    Qt.Key.Key_BracketRight: "]",
    Qt.Key.Key_Backslash: "\\",
    Qt.Key.Key_Semicolon: ";",
    Qt.Key.Key_Apostrophe: "'",
    Qt.Key.Key_Comma: ",",
    Qt.Key.Key_Period: ".",
    Qt.Key.Key_Slash: "/",
    Qt.Key.Key_QuoteLeft: "`",
    Qt.Key.Key_CapsLock: "<caps_lock>",
    Qt.Key.Key_NumLock: "<num_lock>",
    Qt.Key.Key_ScrollLock: "<scroll_lock>",
    Qt.Key.Key_Print: "<print_screen>",
    Qt.Key.Key_Pause: "<pause>",
}


def format_modifiers(modifiers: Qt.KeyboardModifiers, current_key: Optional[int] = None) -> list[str]:
    """提取修饰键列表。"""
    parts = []
    if (modifiers & Qt.KeyboardModifier.ControlModifier) or current_key == Qt.Key.Key_Control:
        parts.append("<ctrl>")
    if (modifiers & Qt.KeyboardModifier.AltModifier) or current_key == Qt.Key.Key_Alt:
        parts.append("<alt>")
    if (modifiers & Qt.KeyboardModifier.ShiftModifier) or current_key == Qt.Key.Key_Shift:
        parts.append("<shift>")
    if (modifiers & Qt.KeyboardModifier.MetaModifier) or current_key == Qt.Key.Key_Meta:
        parts.append("<cmd>")
    return parts


def qt_key_event_to_hotkey(event: QKeyEvent) -> tuple[Optional[str], bool]:
    """将 Qt 按键事件转换为 pynput 快捷键字符串。

    返回值：(hotkey_string, is_modifier_key)
    """
    key = event.key()
    if key in MODIFIER_KEYS:
        return None, True

    modifiers = event.modifiers()
    parts = format_modifiers(modifiers, None)

    key_name: Optional[str] = None
    if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F24:
        number = key - Qt.Key.Key_F1 + 1
        key_name = f"<f{number}>"
    elif Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
        key_name = chr(key).lower()
    elif Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
        key_name = chr(key)
    else:
        key_name = SPECIAL_KEY_MAP.get(key)

    if key_name is None:
        return None, False

    parts.append(key_name)
    return "+".join(parts), False


class HotkeyLineEdit(LineEdit):
    """捕获键盘按键输入的快捷键设置输入框。"""

    hotkey_committed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self._recording = False
        self._previous_hotkey = ""
        self.setClearButtonEnabled(True)
        self.setPlaceholderText(self._idle_placeholder())
        self.clearButton.clicked.connect(self._on_clear_clicked)

    def _idle_placeholder(self) -> str:
        return og.app.tr("Click to set hotkey") if og.app else "Click to set hotkey"

    def _recording_placeholder(self) -> str:
        return og.app.tr("Press hotkey combination...") if og.app else "Press hotkey combination..."

    def _on_clear_clicked(self):
        self._previous_hotkey = ""
        self.setText("")
        self.hotkey_committed.emit("")

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if not self._recording:
            self._start_recording()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        if not self._recording:
            self._start_recording()

    def focusOutEvent(self, event):
        if self._recording:
            self._cancel_recording()
        super().focusOutEvent(event)

    def _start_recording(self):
        self._recording = True
        self._previous_hotkey = self.text()
        self.setPlaceholderText(self._recording_placeholder())
        self.setText("")

    def _cancel_recording(self):
        self._recording = False
        self.setPlaceholderText(self._idle_placeholder())
        self.setText(self._previous_hotkey)

    def _commit_hotkey(self, hotkey_str: str):
        self._recording = False
        self._previous_hotkey = hotkey_str
        self.setPlaceholderText(self._idle_placeholder())
        self.setText(hotkey_str)
        self.clearFocus()
        self.hotkey_committed.emit(hotkey_str)

    def keyPressEvent(self, event: QKeyEvent):
        if not self._recording:
            super().keyPressEvent(event)
            return

        key = event.key()

        # 按下 Esc 键取消录制并恢复原配置
        if key == Qt.Key.Key_Escape:
            self._cancel_recording()
            self.clearFocus()
            event.accept()
            return

        # 无修饰键按下 Backspace 或 Delete 时清空快捷键
        if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete) and not event.modifiers():
            self._commit_hotkey("")
            event.accept()
            return

        hotkey_str, is_modifier = qt_key_event_to_hotkey(event)
        if is_modifier:
            parts = format_modifiers(event.modifiers(), key)
            if parts:
                self.setText("+".join(parts) + "+...")
            else:
                self.setText("")
            event.accept()
            return

        if hotkey_str is not None:
            self._commit_hotkey(hotkey_str)
            event.accept()
            return

        event.accept()

    def keyReleaseEvent(self, event: QKeyEvent):
        if self._recording:
            if event.key() in MODIFIER_KEYS:
                parts = format_modifiers(event.modifiers(), None)
                if parts:
                    self.setText("+".join(parts) + "+...")
                else:
                    self.setText("")
                event.accept()
                return
        super().keyReleaseEvent(event)

    def contextMenuEvent(self, event):
        event.ignore()


class LabelAndHotkey(ConfigLabelAndWidget):
    """带标签与键盘输入识别的快捷键配置项。"""

    MIN_INPUT_WIDTH = DesignToken.CONTROL_WIDTH
    HORIZONTAL_PADDING = 32

    def __init__(self, config_desc, config, key: str, options=None):
        super().__init__(config_desc, config, key)
        self.key = key
        options = options or {}
        self.minimum_input_width = options.get("minimum_width", self.MIN_INPUT_WIDTH)
        self.maximum_input_width = options.get("maximum_width")
        self.line_edit = HotkeyLineEdit()
        self.line_edit.setMinimumWidth(self.minimum_input_width)
        self.update_value()
        self.line_edit.hotkey_committed.connect(self.value_changed)
        self.add_widget(self.line_edit, stretch=0)

    def update_value(self):
        value = str(self.config.get(self.key) or "")
        self.line_edit._previous_hotkey = value
        self.line_edit.setText(value)
        self._update_width(value)

    def value_changed(self, value: str):
        self.update_config(value)
        self._update_width(value)

    def _update_width(self, value: str):
        font_metrics = QFontMetrics(self.line_edit.font())
        content_width = font_metrics.horizontalAdvance(value or "") + self.HORIZONTAL_PADDING
        width = max(self.minimum_input_width, control_width(content_width))
        if self.maximum_input_width is not None:
            width = min(width, self.maximum_input_width)
        self.line_edit.setFixedWidth(width)


_patched = False


def patch_config_item_factory():
    """将快捷键控件注册到 ok 框架的 ConfigItemFactory。"""
    global _patched
    if _patched:
        return

    import ok.ui.qt.tasks.ConfigItemFactory as cif

    original_config_widget = cif.config_widget

    def custom_config_widget(config_type, config_desc, config, key, value, task):
        the_type = config_type.get(key) if config_type is not None else None
        value = config.get_default(key)
        resolved_type = cif._resolve_type(the_type, value)
        if (
            resolved_type == "hotkey"
            or (isinstance(the_type, dict) and the_type.get("type") == "hotkey")
            or (the_type is None and isinstance(key, str) and key.endswith("hotkey"))
        ):
            return LabelAndHotkey(config_desc, config, key, the_type)
        return original_config_widget(config_type, config_desc, config, key, value, task)

    cif.config_widget = custom_config_widget
    try:
        import ok.ui.qt.tasks.ConfigCard as cc
        cc.config_widget = custom_config_widget
    except Exception:
        pass
    _patched = True
