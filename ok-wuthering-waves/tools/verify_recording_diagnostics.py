import json
import os
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'windows')

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from config import config
from ok.util.handler import ExitEvent
from src.workflow.recorder import GameplayRecorderService, WindowsGameplaySource
from src.workflow.store import WorkflowStateStore
from src.workflow.video import read_video
from tools.verify_gameplay_runtime import NativeCaptureWindow


class DiagnosticWindow(QWidget):
    def __init__(self, image):
        super().__init__()
        self.picture = QPixmap(str(image))
        if self.picture.isNull():
            raise ValueError('诊断验证所需的真实游戏截图无法读取')
        self.setWindowTitle('录制诊断验证窗口：真实截图展示')
        self.resize(1280, 720)
        self.sequence = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.timer.start(50)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(self.rect(), self.picture)
        self.sequence += 1
        painter.fillRect(300, 0, 650, 35, Qt.black)
        painter.setPen(Qt.white)
        painter.drawText(310, 24, f'录制诊断验证窗口 · 真实截图展示 · 当前刷新 {self.sequence}')
        painter.end()


def main():
    root = Path(__file__).resolve().parents[1]
    app = QApplication.instance() or QApplication([])
    window = DiagnosticWindow(root / 'tests/images/quest_yangyang_interaction.png')
    window.show()
    QTest.qWait(200)
    output = root / 'working_images' / f'recording_diagnostics_{time.time_ns()}'
    store = WorkflowStateStore(output)
    exit_event = ExitEvent()
    source = NativeCaptureWindow(int(window.winId()), exit_event)
    recorder = GameplayRecorderService(store, root, config['template_matching'],
                                       {'use_openvino': True, 'use_npu': False}, exit_event,
                                       {'game_version': 'diagnostic-window', 'language': 'zh_CN'})
    try:
        recorder.start(WindowsGameplaySource(source))
        capture_log = recorder.diagnostics.path
        worker_log = recorder.diagnostics.directory / 'worker.jsonl'
        deadline = time.monotonic() + 50
        minimized = False
        restore_at = None
        restored_at = None
        while time.monotonic() < deadline:
            if recorder.error is not None:
                raise recorder.error
            if not minimized and recorder.observation_count >= 2:
                window.showMinimized()
                minimized = True
                restore_at = time.monotonic() + 7
            if restore_at is not None and time.monotonic() >= restore_at:
                window.showNormal()
                restore_at = None
                restored_at = time.monotonic()
            if restored_at is not None and time.monotonic() - restored_at > 6:
                break
            QTest.qWait(50)
        recorder.stop()
        capture = [json.loads(line) for line in capture_log.read_text(encoding='utf-8').splitlines()]
        worker = [json.loads(line) for line in worker_log.read_text(encoding='utf-8').splitlines()]
        capture_events = {entry['event'] for entry in capture}
        worker_events = {entry['event'] for entry in worker}
        if not {'session_start', 'capture_missing', 'capture_recovered', 'session_stopped'} <= capture_events:
            raise AssertionError('真实窗口采集缺口及恢复没有完整记录')
        missing_events = [entry for entry in capture if entry['event'] == 'capture_missing']
        minimized_missing = [entry for entry in missing_events if entry['window']['window_minimized']]
        if not minimized_missing:
            raise AssertionError('没有记录到最小化期间的实际采集缺口')
        missing = minimized_missing[0]
        if missing['frame_request_seconds'] < 3.5:
            raise AssertionError('采集诊断未正确记录实际最小化状态或截图超时')
        finished = [entry for entry in worker if entry['event'] == 'segment_finished']
        if not {'capture_gap', 'stop_requested'} <= {entry['trigger'] for entry in finished}:
            raise AssertionError('片段结束原因记录不完整')
        recordings = store.list_recordings()
        if len(recordings) != 1:
            raise AssertionError('同次启停产生了多条界面记录')
        segments = store.session_segments(recordings[0]['id'])
        if not {'complete', 'interrupted'} <= {item['status'] for item in segments}:
            raise AssertionError('整次录制没有保留中断前后的内部片段')
        for recording in recordings:
            materials = store.recording_materials(recording['id'])
            if sum(item['kind'] == '录制会话日志' for item in materials['files']) != 2:
                raise AssertionError('材料窗口无法取得完整的会话日志')
        decoded = sum(1 for path in output.glob('*/video_*.mkv') for _ in read_video(path))
        if decoded != sum(entry['video_frames'] for entry in finished):
            raise AssertionError('视频诊断数量与实际解码帧数不符')
        report = {'verified': True, 'source': '真实 Windows Qt 验证窗口，展示已有真实游戏截图',
                  'capture_events': sorted(capture_events), 'worker_events': sorted(worker_events),
                  'recordings': recordings, 'decoded_frames': decoded,
                  'segments': segments,
                  'capture_missing_events': len(missing_events),
                  'missing_window': missing['window'], 'request_seconds': missing['frame_request_seconds'],
                  'output': str(output)}
        (output / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False))
    finally:
        try:
            recorder.stop()
        finally:
            window.timer.stop()
            window.close()
            exit_event.set()


if __name__ == '__main__':
    main()
