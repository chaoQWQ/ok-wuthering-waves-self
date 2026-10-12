import threading
from datetime import datetime

from ok import Logger, og
from ok.core.events import communicate
from src.utils.DailyEmail import send_resolution_report

logger = Logger.get_logger(__name__)


class ResolutionEmail:
    def __init__(self):
        self.previous = None
        self.hwnd = None
        communicate.window.connect(self.on_window)
        if og.device_manager is not None:
            self.observe_handle(og.device_manager.hwnd_window.hwnd)

    def on_window(self, visible, x, y, window_width, window_height, width, height, scaling):
        change = self.observe_handle(og.device_manager.hwnd_window.hwnd)
        if change is None:
            return
        previous, current = change
        task = og.executor.get_task_by_class_name('MultiAccountDailyTask')
        if task is None:
            return
        config = dict(task.config)
        current_task = og.executor.current_task
        task_name = current_task.name if current_task else ''
        detected = datetime.now().astimezone()
        logger.info(f'游戏窗口分辨率变化：{previous} → {current}')
        threading.Thread(target=self.notify, args=(previous, current, detected, task_name, config),
                         name='ResolutionEmail').start()

    def observe_handle(self, hwnd):
        import win32gui

        if not hwnd or not win32gui.IsWindow(hwnd):
            self.previous = None
            self.hwnd = None
            return
        if win32gui.IsIconic(hwnd):
            return
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        current = (right - left, bottom - top)
        if min(current) <= 0:
            return
        if self.hwnd != hwnd or self.previous is None:
            self.hwnd = hwnd
            self.previous = current
            return
        previous = self.previous
        self.previous = current
        if current == previous:
            return
        return previous, current

    @staticmethod
    def notify(previous, current, detected, task_name, config):
        if send_resolution_report(previous, current, detected, task_name, config):
            logger.info('游戏窗口分辨率变化邮件已发送')
