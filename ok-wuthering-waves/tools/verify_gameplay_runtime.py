import argparse
import json
import time
from pathlib import Path

from config import config
from ok import Config
from ok.device.DeviceManager import DeviceManager
from ok.util.GlobalConfig import GlobalConfig
from ok.util.handler import ExitEvent
from src.workflow.compiler import AgyVisionProvider, WorkflowCompiler
from src.workflow.recorder import GameplayRecorderService, WindowsGameplaySource
from src.workflow.store import WorkflowStateStore
from src.workflow.video import read_video


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=int, default=10)
    parser.add_argument('--analyze', action='store_true')
    arguments = parser.parse_args()
    if not 1 <= arguments.seconds <= 300:
        raise ValueError('验证时长必须在 1 至 300 秒之间')
    root = Path(__file__).resolve().parents[1]
    output = root / 'working_images' / f'workflow_runtime_{time.time_ns()}'
    output.mkdir(parents=True)
    Config.config_folder = str(output / 'configs')
    exit_event = ExitEvent()
    global_config = GlobalConfig(config['global_configs'])
    windows = config['windows']
    manager = DeviceManager(config, exit_event, global_config)
    window = manager.hwnd_window
    window.to_handle_mute = False
    recorder = None
    try:
        deadline = time.monotonic() + 5
        while not window.exists and time.monotonic() < deadline:
            exit_event.wait(.2)
        if not window.exists:
            raise RuntimeError('未找到正在运行的鸣潮窗口，无法进行真实录制验证')
        import win32api
        metadata = win32api.GetFileVersionInfo(window.exe_full_path, '\\')
        version = '.'.join(map(str, (metadata['FileVersionMS'] >> 16, metadata['FileVersionMS'] & 65535,
                                     metadata['FileVersionLS'] >> 16, metadata['FileVersionLS'] & 65535)))
        store = WorkflowStateStore(output)
        recorder = GameplayRecorderService(store, root, config['template_matching'],
                                            {'use_openvino': True, 'use_npu': False}, exit_event,
                                            {'game_version': version, 'language': 'zh_CN'})
        recorder.start(WindowsGameplaySource(window))
        deadline = time.monotonic() + arguments.seconds
        while time.monotonic() < deadline:
            if recorder.error is not None:
                raise recorder.error
            exit_event.wait(.2)
        recorder.stop()
        capture_count = recorder.captured_frames
        observation_count = recorder.observation_count
        last_scene = recorder.latest['scene'] if recorder.latest else None
        if capture_count == 0 or observation_count == 0:
            raise RuntimeError('没有取得实际游戏画面和观察结果')
        recorder = None
        recordings = store.list_recordings()
        videos = sorted(output.glob('*/video_*.mkv'))
        frames = sum(1 for path in videos for _ in read_video(path))
        report = {'recordings': recordings, 'video_files': len(videos), 'decoded_frames': frames,
                  'captured_frames': capture_count, 'observations': observation_count, 'last_scene': last_scene,
                  'output': str(output), 'game_input_sent': False}
        if arguments.analyze:
            provider = AgyVisionProvider(output / 'agy', exit_event)
            report['workflows'] = []
            for recording_id in store.pending_recordings():
                learned = WorkflowCompiler(store, provider).compile(recording_id, **store.environment(recording_id))
                report['workflows'].extend(item.revision for item in learned)
        (output / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False))
    finally:
        if recorder is not None:
            recorder.stop()
        window.stop()
        manager.close()
        exit_event.set()


if __name__ == '__main__':
    main()
