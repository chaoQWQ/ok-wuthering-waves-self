import argparse
import json
import logging
import os
import tempfile
import threading
import time

import win32api
import win32con
import win32gui

from ok.device.interaction_methods.post_message import PostMessageInteraction
from src.utils import FaultRecorder as diagnostics
from src.utils.wgc_compat import _enable_post_message_foreground_compat
from src.char.Qingxiao import Qingxiao


def verify(duration):
    with tempfile.TemporaryDirectory() as folder:
        exit_event = threading.Event()
        recorder = diagnostics.FaultRecorder(folder, exit_event).start()
        diagnostics._recorder = recorder
        assert _enable_post_message_foreground_compat()
        interaction = PostMessageInteraction(None, None)
        hwnd = win32gui.CreateWindowEx(
            0, 'STATIC', 'OK-WW diagnostic verification', win32con.WS_POPUP,
            0, 0, 100, 100, 0, 0, win32api.GetModuleHandle(None), None)
        logger = logging.getLogger('ok')
        old_level = logger.level
        logger.setLevel(logging.INFO)
        try:
            recorder.observe_window_handle(hwnd)
            window_events = [event for event in recorder.snapshot() if event['kind'] == 'window_state']
            assert window_events[-1]['game_exists'] is True
            assert 'foreground_program' in window_events[-1]
            before = len(window_events)
            recorder.observe_window_handle(hwnd)
            assert len([event for event in recorder.snapshot() if event['kind'] == 'window_state']) == before
            character = Qingxiao(None, 0)
            diagnostics.record_character_state(character, 'rotation_start', liberation_branch=True)
            assert any(event.get('character') == 'Qingxiao' and event.get('action') == 'rotation_start'
                       for event in recorder.snapshot())
            # 消息提交到本测试创建的隐藏窗口。
            interaction.post(win32con.WM_NULL, hwnd=hwnd)
            interaction.post(win32con.WM_ACTIVATE, hwnd=hwnd)
            win32gui.PumpWaitingMessages()
            initial = recorder.snapshot()
            assert any(event.get('outcome') == 'submitted' for event in initial)
            assert any(event.get('reason') == 'activation_guard' for event in initial)
            win32gui.DestroyWindow(hwnd)
            recorder.observe_window_handle(hwnd)
            assert any(event['kind'] == 'window_state' and event['game_exists'] is False
                       for event in recorder.snapshot())
            interaction.post(win32con.WM_NULL, hwnd=hwnd)
            failed = recorder.snapshot()
            assert any(event.get('outcome') == 'submission_error' and event.get('error')
                       for event in failed)
            deadline = time.monotonic() + 5
            while not recorder.pending.empty() or not list(recorder.folder.glob('incident-*.json')):
                assert time.monotonic() < deadline
                time.sleep(0.05)
            incidents = [json.loads(path.read_text(encoding='utf-8'))
                         for path in recorder.folder.glob('incident-*.json')]
            assert any(any(event.get('outcome') == 'submission_error'
                           for event in incident['timeline']) for incident in incidents)

            start = time.monotonic()
            requests = 0
            while time.monotonic() - start < duration:
                recorder.count({'kind': 'input_request', 'operation': 'click', 'key': 'middle'})
                requests += 1
                time.sleep(0.002)
            recorder.checkpoint()
            events = recorder.snapshot()
            counters = [event for event in events if event['kind'] == 'input_request']
            assert all(event['count'] > 0 for event in counters)
            assert len(counters) <= 122
            assert len(counters) < requests
            if duration >= 125:
                assert counters[-1]['last_epoch'] - counters[0]['epoch'] > 118
                assert not any(event.get('message') == win32con.WM_ACTIVATE for event in events)
            assert recorder.evicted_events == 0
            assert recorder.last_error is None
            saved = json.loads((recorder.folder / f'recent-{os.getpid()}.json').read_text(encoding='utf-8'))
            assert saved['timeline']
            print(json.dumps({'requests': requests, 'summary_count': len(counters),
                              'duration': duration, 'native_submission': 'passed',
                              'native_error': 'passed', 'incident_file': 'passed',
                              'window_changes': 'passed', 'character_context': 'passed',
                              'retention': 'passed' if duration >= 125 else 'short_run'}, ensure_ascii=False))
        finally:
            if win32gui.IsWindow(hwnd):
                win32gui.DestroyWindow(hwnd)
            exit_event.set()
            recorder.thread.join(timeout=5)
            diagnostics._recorder = None
            logger.setLevel(old_level)
        assert not recorder.thread.is_alive()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--duration', type=float, default=125)
    arguments = parser.parse_args()
    verify(arguments.duration)
