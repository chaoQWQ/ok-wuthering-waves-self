from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget


class RecordingNotification(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.NoFocus)
        self.label = QLabel(self)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setTextFormat(Qt.PlainText)
        self.label.setWordWrap(True)
        self.label.setStyleSheet('background: rgba(25, 32, 44, 240); color: white;'
                                 'border: 1px solid #657a98; border-radius: 12px; padding: 22px;')
        font = self.label.font()
        font.setPointSize(18)
        font.setBold(True)
        self.label.setFont(font)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.label)
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.hide)

    @staticmethod
    def foreground_screen():
        if QApplication.platformName() == 'windows':
            import win32api
            import win32con
            import win32gui
            handle = win32gui.GetForegroundWindow()
            if handle:
                monitor = win32api.MonitorFromWindow(handle, win32con.MONITOR_DEFAULTTONEAREST)
                origin = win32api.GetMonitorInfo(monitor)['Monitor'][:2]
                # Windows 保留屏幕原点，Qt 按 DPI 缩放屏幕宽高。
                for screen in QApplication.screens():
                    if (screen.geometry().x(), screen.geometry().y()) == origin:
                        return screen
                raise RuntimeError('无法找到前台窗口所在的屏幕')
        screen = QApplication.primaryScreen()
        if screen is None:
            raise RuntimeError('无法取得录制提示所需的屏幕')
        return screen

    def show_message(self, message):
        screen = self.foreground_screen()
        self.setScreen(screen)
        geometry = screen.geometry()
        self.label.setText(message)
        self.setFixedWidth(min(440, geometry.width() - 32))
        self.adjustSize()
        self.move(geometry.center() - self.rect().center())
        self.show()
        self.raise_()
        self.hide_timer.start(2000)
