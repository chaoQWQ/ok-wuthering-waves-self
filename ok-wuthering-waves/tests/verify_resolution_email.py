import argparse
import json
from datetime import datetime
from pathlib import Path

import win32api
import win32con
import win32gui

from src.utils.DailyEmail import EMAIL_ENABLED, send_resolution_report
from src.utils.ResolutionEmail import ResolutionEmail
from ok.core.events import communicate


def verify(deliver):
    monitor = ResolutionEmail()
    hwnd = win32gui.CreateWindowEx(0, 'STATIC', 'OK-WW resolution verification',
                                  win32con.WS_POPUP, 0, 0, 1920, 1080,
                                  0, 0, win32api.GetModuleHandle(None), None)
    second = 0
    try:
        assert monitor.observe_handle(hwnd) is None
        assert monitor.previous == (1920, 1080)
        win32gui.SetWindowPos(hwnd, 0, 10, 20, 1920, 1080, win32con.SWP_NOACTIVATE)
        assert monitor.observe_handle(hwnd) is None
        win32gui.SetWindowPos(hwnd, 0, 10, 20, 1920, 1440, win32con.SWP_NOACTIVATE)
        change = monitor.observe_handle(hwnd)
        assert change == ((1920, 1080), (1920, 1440))
        assert monitor.observe_handle(hwnd) is None
        win32gui.SetWindowPos(hwnd, 0, 10, 20, 2560, 1440, win32con.SWP_NOACTIVATE)
        assert monitor.observe_handle(hwnd) == ((1920, 1440), (2560, 1440))
        win32gui.SetWindowPos(hwnd, 0, 10, 20, 1920, 1080, win32con.SWP_NOACTIVATE)
        assert monitor.observe_handle(hwnd) == ((2560, 1440), (1920, 1080))
        second = win32gui.CreateWindowEx(0, 'STATIC', 'OK-WW replacement verification',
                                        win32con.WS_POPUP, 0, 0, 640, 480,
                                        0, 0, win32api.GetModuleHandle(None), None)
        assert monitor.observe_handle(second) is None
        assert monitor.observe_handle(0) is None
        assert monitor.previous is None
        assert send_resolution_report(*change, datetime.now().astimezone(), '',
                                      {EMAIL_ENABLED: False}) is False
        if deliver:
            config = json.loads((Path(__file__).resolve().parents[1] / 'configs' /
                                 'MultiAccountDailyTask.json').read_text(encoding='utf-8'))
            assert config.get(EMAIL_ENABLED), 'Email Notification must be enabled'
            assert send_resolution_report(*change, datetime.now().astimezone(),
                                          '邮件功能验证：本次变化来自测试窗口', config)
        print(json.dumps({'native_window_changes': 'passed', 'duplicate_suppression': 'passed',
                          'movement_ignored': 'passed', 'window_replacement': 'passed',
                          'email_delivery': 'passed' if deliver else 'not_requested'}))
    finally:
        communicate.window.disconnect(monitor.on_window)
        if second:
            win32gui.DestroyWindow(second)
        win32gui.DestroyWindow(hwnd)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--deliver', action='store_true')
    verify(parser.parse_args().deliver)
