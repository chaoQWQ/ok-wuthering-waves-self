import sys
import time

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import FluentIcon, ToolButton, isDarkTheme, qconfig

from ok import Logger, og
from ok.core.events import communicate
from ok.ui.qt.tasks.TooltipTableWidget import TooltipTableWidget
from ok.ui.qt.widget.UpdateConfigWidgetItem import value_to_string
from src.utils.ManualRouteRecorder import HotkeyEdgePoller

logger = Logger.get_logger(__name__)


class TaskFloatingWindow(QWidget):

    def __init__(self, main_window=None, key_reader=None):
        super().__init__(None)
        self.main_window = main_window
        self.key_reader = key_reader
        self._current_hotkey_str = None
        self._hotkey_poller = None
        self._hotkey_warned = False
        self.setObjectName("TaskFloatingWindow")
        self.setWindowFlags(
            Qt.WindowStaysOnTopHint |
            Qt.FramelessWindowHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        self.current_info_run = None
        self.dismissed_info_run = None
        self.user_moved = False
        self.drag_position = None

        self.setFixedSize(440, 260)
        self._init_ui()
        self._apply_theme_style()

        qconfig.themeChanged.connect(self._apply_theme_style)

        if main_window is not None:
            main_window.destroyed.connect(self.close_window)
        communicate.quit.connect(self.close_window)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_status)
        self.timer.start(1000)

        self.hotkey_timer = QTimer(self)
        self.hotkey_timer.timeout.connect(self.poll_hotkey)
        self.hotkey_timer.start(50)

    def _init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.card = QFrame(self)
        self.card.setObjectName("TaskFloatingCard")
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(8)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)

        self.title_label = QLabel(self.card)
        self.title_label.setObjectName("TaskFloatingTitleLabel")
        self.title_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        header_layout.addWidget(self.title_label)

        self.close_button = ToolButton(FluentIcon.CLOSE, self.card)
        self.close_button.setFixedSize(24, 24)
        self.close_button.setToolTip(og.app.tr("Close"))
        self.close_button.clicked.connect(self.on_close_clicked)
        header_layout.addWidget(self.close_button)

        card_layout.addLayout(header_layout)

        self.table = TooltipTableWidget(width_percentages=[0.36, 0.64])
        self.table.setColumnCount(2)
        info_headers = [og.app.tr("Info"), og.app.tr("Value")]
        self.table.setHorizontalHeaderLabels(info_headers)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setWordWrap(True)
        card_layout.addWidget(self.table)

        root_layout.addWidget(self.card)

    def _apply_theme_style(self):
        dark_mode = isDarkTheme()
        if dark_mode:
            card_qss = """
                #TaskFloatingCard {
                    background-color: rgba(32, 32, 32, 0.94);
                    border: 1px solid rgba(255, 255, 255, 0.12);
                    border-radius: 8px;
                }
                #TaskFloatingTitleLabel {
                    color: #FFFFFF;
                    font-size: 13px;
                    font-weight: 600;
                }
            """
        else:
            card_qss = """
                #TaskFloatingCard {
                    background-color: rgba(252, 252, 252, 0.96);
                    border: 1px solid rgba(0, 0, 0, 0.12);
                    border-radius: 8px;
                }
                #TaskFloatingTitleLabel {
                    color: #1A1A1A;
                    font-size: 13px;
                    font-weight: 600;
                }
            """
        self.card.setStyleSheet(card_qss)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self.drag_position is not None:
            self.move(event.globalPosition().toPoint() - self.drag_position)
            self.user_moved = True
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.drag_position = None
        super().mouseReleaseEvent(event)

    def position_bottom_right(self):
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        available_geometry = screen.availableGeometry()
        margin = 20
        target_x = available_geometry.right() - self.width() - margin + 1
        target_y = available_geometry.bottom() - self.height() - margin + 1
        self.move(target_x, target_y)

    def ensure_topmost(self):
        if sys.platform == "win32":
            import ctypes
            hwnd = int(self.winId())
            ctypes.windll.user32.SetWindowPos(
                hwnd, -1, 0, 0, 0, 0,
                0x0001 | 0x0002 | 0x0010 | 0x0040
            )

    def on_close_clicked(self):
        self.dismissed_info_run = self.current_info_run
        self.hide()

    def close_window(self):
        if self.timer.isActive():
            self.timer.stop()
        if hasattr(self, "hotkey_timer") and self.hotkey_timer.isActive():
            self.hotkey_timer.stop()
        self.hide()
        self.close()

    def poll_hotkey(self):
        basic_config = og.global_config.get_config("Basic Options")
        hotkey_str = str(basic_config.get("Task Floating Window Toggle Hotkey", "<ctrl>+<f11>") or "").strip()
        if hotkey_str != self._current_hotkey_str:
            self._current_hotkey_str = hotkey_str
            self._hotkey_warned = False
            if hotkey_str and hotkey_str.lower() != "none":
                self._hotkey_poller = HotkeyEdgePoller(hotkey_str, key_reader=self.key_reader)
            else:
                self._hotkey_poller = None

        if self._hotkey_poller is not None and self._hotkey_poller.supported:
            if self._hotkey_poller.poll():
                self.toggle_window()
        elif hotkey_str and hotkey_str.lower() != "none" and not self._hotkey_warned:
            self._hotkey_warned = True
            logger.warning(f"Unsupported task floating window toggle hotkey: {hotkey_str!r}")

    def toggle_window(self):
        basic_config = og.global_config.get_config("Basic Options")
        current_enabled = basic_config.get("Show Task Floating Window", True)
        if self.isVisible():
            basic_config["Show Task Floating Window"] = False
            self.hide()
        elif self.dismissed_info_run is not None and self.dismissed_info_run == self.current_info_run:
            basic_config["Show Task Floating Window"] = True
            self.dismissed_info_run = None
            self.update_status()
        else:
            new_state = not current_enabled
            basic_config["Show Task Floating Window"] = new_state
            if new_state:
                self.dismissed_info_run = None
                self.update_status()
            else:
                if self.isVisible():
                    self.hide()

        if self.main_window is not None:
            setting_tab = getattr(self.main_window, "setting_tab", None)
            if setting_tab is not None:
                for group in getattr(setting_tab, "config_groups", []):
                    group.update_config()

    @staticmethod
    def time_elapsed(start_time):
        if start_time > 0:
            elapsed_time = time.time() - start_time
            hours, remainder = divmod(elapsed_time, 3600)
            minutes, seconds = divmod(remainder, 60)
            return f"{int(hours)}h {int(minutes)}m {int(seconds)}s"
        return ""

    def update_status(self):
        basic_config = og.global_config.get_config("Basic Options")
        if not basic_config.get("Show Task Floating Window", True):
            if self.isVisible():
                self.hide()
            return

        current_task = getattr(og.executor, "current_task", None)
        if current_task is None or not getattr(current_task, "enabled", False):
            if self.isVisible():
                self.hide()
            return

        task_start_time = getattr(current_task, "start_time", 0)
        current_info_run = (id(current_task), task_start_time)
        if current_info_run != self.current_info_run:
            self.dismissed_info_run = None
            self.current_info_run = current_info_run

        if self.current_info_run == self.dismissed_info_run:
            if self.isVisible():
                self.hide()
            return

        if not self.isVisible():
            if not self.user_moved:
                self.position_bottom_right()
            self.show()
            self.ensure_topmost()

        status_text = og.app.tr("Running")
        task_name_text = og.app.tr(current_task.name)
        time_text = self.time_elapsed(task_start_time)
        elapsed_label = og.app.tr("Time Elapsed")
        self.title_label.setText(f"{status_text}: {task_name_text} {elapsed_label}: {time_text}")

        info_dict = getattr(current_task, "info", {})
        self._update_table_content(info_dict)

    def _update_table_content(self, info_dict):
        items_count = len(info_dict)
        self.table.setRowCount(items_count)
        for row_index, (info_key, info_value) in enumerate(info_dict.items()):
            key_cell = self.table.item(row_index, 0)
            if key_cell is None:
                key_cell = QTableWidgetItem()
                key_cell.setFlags(key_cell.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row_index, 0, key_cell)
            translated_key = og.app.tr(str(info_key))
            if key_cell.text() != translated_key:
                key_cell.setText(translated_key)

            value_cell = self.table.item(row_index, 1)
            if value_cell is None:
                value_cell = QTableWidgetItem()
                value_cell.setFlags(value_cell.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row_index, 1, value_cell)
            formatted_value = og.app.tr(value_to_string(info_value))
            if value_cell.text() != formatted_value:
                value_cell.setText(formatted_value)


_task_floating_window = None


def start_task_floating_window(main_window=None, key_reader=None):
    global _task_floating_window
    if _task_floating_window is None:
        _task_floating_window = TaskFloatingWindow(main_window, key_reader=key_reader)
    return _task_floating_window
