import json
import threading
from datetime import datetime, timedelta

import pytest

from src.workflow.diagnostics import RecordingDiagnostics
from src.workflow.store import WorkflowStateStore


def test_diagnostic_logs_preserve_concurrent_events_and_material_links(tmp_path):
    store = WorkflowStateStore(tmp_path)
    recording = store.start_recording()
    capture = RecordingDiagnostics(store.root, 'diagnostic-test', 'capture')
    worker = RecordingDiagnostics(store.root, 'diagnostic-test', 'worker')
    threads = [threading.Thread(target=capture.write, args=('thread_event', '并发日志写入'),
                                kwargs={'sequence': number}) for number in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    worker.write('worker_ready', '处理进程就绪', recording_id=recording)
    capture_entries = [json.loads(line) for line in capture.path.read_text(encoding='utf-8').splitlines()]
    assert len(capture_entries) == 20
    assert {entry['sequence'] for entry in capture_entries} == set(range(20))
    assert all(datetime.fromisoformat(entry['time']).utcoffset() == timedelta(hours=8)
               for entry in capture_entries)
    worker.summary(store.resolve(recording), recording_id=recording, status='recording')
    materials = store.recording_materials(recording)
    assert {item['path'] for item in materials['files'] if item['kind'] == '录制会话日志'} == {
        capture.path, worker.path}
    assert sum(item['kind'] == '片段诊断汇总' for item in materials['files']) == 1
    assert store.events(recording) == []


def test_diagnostic_material_links_reject_paths_outside_diagnostics(tmp_path):
    store = WorkflowStateStore(tmp_path)
    recording = store.start_recording()
    summary = store.resolve(f'{recording}/diagnostics.json')
    summary.write_text(json.dumps({'session_logs': ['../outside.jsonl']}), encoding='utf-8')
    with pytest.raises(ValueError):
        store.recording_materials(recording)
