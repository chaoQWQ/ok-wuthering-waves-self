import argparse
import ctypes
import json
import time
from pathlib import Path

import psutil
import win32api
import win32con
import win32gui
import win32process

from config import config
from ok.util.handler import ExitEvent
from src.workflow.compiler import AgyVisionProvider, WorkflowCompiler
from src.workflow.recorder import GameplayRecorderService, WindowsGameplaySource
from src.workflow.store import WorkflowStateStore
from src.workflow.video import read_video


class NativeCaptureWindow:
    def __init__(self, hwnd, exit_event):
        self.hwnd = hwnd
        self.app_exit_event = exit_event

    @property
    def exists(self):
        return bool(win32gui.IsWindow(self.hwnd))

    @property
    def width(self):
        return win32gui.GetClientRect(self.hwnd)[2]

    @property
    def height(self):
        return win32gui.GetClientRect(self.hwnd)[3]

    @property
    def capture_target_signature(self):
        return self.hwnd, self.width, self.height


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=int, default=10)
    parser.add_argument('--analyze', action='store_true')
    parser.add_argument('--hwnd', type=int)
    parser.add_argument('--minimize-seconds', type=float, default=0)
    arguments = parser.parse_args()
    if not 1 <= arguments.seconds <= 300:
        raise ValueError('验证时长必须在 1 至 300 秒之间')
    if arguments.minimize_seconds and not 6 <= arguments.minimize_seconds <= 20:
        raise ValueError('最小化验证时长必须在 6 至 20 秒之间')
    result = ctypes.windll.shcore.SetProcessDpiAwareness(2)
    if result != 0:
        raise OSError(f'无法启用物理像素坐标：HRESULT {result}')
    root = Path(__file__).resolve().parents[1]
    output = root / 'working_images' / f'workflow_runtime_{time.time_ns()}'
    output.mkdir(parents=True)
    exit_event = ExitEvent()
    handles = []
    win32gui.EnumWindows(lambda hwnd, _: handles.append(hwnd)
                         if win32gui.GetClassName(hwnd) == config['windows']['hwnd_class'] else None, None)
    if arguments.hwnd is not None:
        handles = [hwnd for hwnd in handles if hwnd == arguments.hwnd]
    if len(handles) != 1:
        raise RuntimeError('需要唯一的鸣潮窗口，可以使用 --hwnd 指定窗口句柄')
    window = NativeCaptureWindow(handles[0], exit_event)
    placement = win32gui.GetWindowPlacement(window.hwnd) if arguments.minimize_seconds else None
    recorder = None
    try:
        if placement is not None and win32gui.IsIconic(window.hwnd):
            win32gui.ShowWindow(window.hwnd, win32con.SW_RESTORE)
        if not window.exists:
            raise RuntimeError('未找到正在运行的鸣潮窗口，无法进行真实录制验证')
        _, pid = win32process.GetWindowThreadProcessId(window.hwnd)
        executable = psutil.Process(pid).exe()
        if Path(executable).name.casefold() != config['windows']['exe'].casefold():
            raise RuntimeError('指定窗口的程序名称与鸣潮客户端不符')
        metadata = win32api.GetFileVersionInfo(executable, '\\')
        version = '.'.join(map(str, (metadata['FileVersionMS'] >> 16, metadata['FileVersionMS'] & 65535,
                                     metadata['FileVersionLS'] >> 16, metadata['FileVersionLS'] & 65535)))
        store = WorkflowStateStore(output)
        recorder = GameplayRecorderService(store, root, config['template_matching'],
                                            {'use_openvino': True, 'use_npu': False}, exit_event,
                                            {'game_version': version, 'language': 'zh_CN'})
        recorder.start(WindowsGameplaySource(window))
        capture_log = recorder.diagnostics.path
        worker_log = recorder.diagnostics.directory / 'worker.jsonl'
        deadline = time.monotonic() + arguments.seconds
        minimized = False
        restore_at = None
        while time.monotonic() < deadline:
            if recorder.error is not None:
                raise recorder.error
            if arguments.minimize_seconds and not minimized and recorder.observation_count >= 2:
                win32gui.ShowWindow(window.hwnd, win32con.SW_MINIMIZE)
                minimized = True
                restore_at = time.monotonic() + arguments.minimize_seconds
            if restore_at is not None and time.monotonic() >= restore_at:
                win32gui.ShowWindow(window.hwnd, win32con.SW_RESTORE)
                restore_at = None
            exit_event.wait(.2)
        recorder.stop()
        capture_count = recorder.captured_frames
        observation_count = recorder.observation_count
        last_scene = recorder.latest['scene'] if recorder.latest else None
        if capture_count == 0 or observation_count == 0:
            raise RuntimeError('没有取得实际游戏画面和观察结果')
        capture_entries = [json.loads(line) for line in capture_log.read_text(encoding='utf-8').splitlines()]
        worker_entries = [json.loads(line) for line in worker_log.read_text(encoding='utf-8').splitlines()]
        capture_events = {entry['event'] for entry in capture_entries}
        worker_events = {entry['event'] for entry in worker_entries}
        if not {'session_start', 'session_stopped', 'capture_state'} <= capture_events:
            raise RuntimeError('采集诊断日志不完整')
        if not {'worker_ready', 'segment_started', 'segment_finished'} <= worker_events:
            raise RuntimeError('录制处理诊断日志不完整')
        if arguments.minimize_seconds and not {'capture_missing', 'capture_recovered'} <= capture_events:
            raise RuntimeError('窗口最小化没有触发实际的画面中断与恢复，尚未验证该条件')
        recorder = None
        recordings = store.list_recordings()
        videos = sorted(output.glob('*/video_*.mkv'))
        frames = sum(1 for path in videos for _ in read_video(path))
        report = {'recordings': recordings, 'video_files': len(videos), 'decoded_frames': frames,
                  'captured_frames': capture_count, 'observations': observation_count, 'last_scene': last_scene,
                  'output': str(output), 'game_input_sent': False}
        report['capture_events'] = sorted(capture_events)
        report['worker_events'] = sorted(worker_events)
        report['minimize_seconds'] = arguments.minimize_seconds
        report['diagnostic_summaries'] = [str(path) for path in output.glob('*/diagnostics.json')]
        if arguments.analyze:
            provider = AgyVisionProvider(output / 'agy', exit_event)
            report['workflows'] = []
            for recording_id in store.pending_recordings():
                learned = WorkflowCompiler(store, provider).compile(recording_id, **store.environment(recording_id))
                report['workflows'].extend(item.revision for item in learned)
        (output / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False))
    finally:
        try:
            if recorder is not None:
                recorder.stop()
        finally:
            if placement is not None and win32gui.IsWindow(window.hwnd):
                win32gui.SetWindowPlacement(window.hwnd, placement)
            exit_event.set()


if __name__ == '__main__':
    main()
