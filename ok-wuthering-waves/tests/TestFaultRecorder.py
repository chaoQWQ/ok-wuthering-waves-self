import json
import logging
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.utils.FaultRecorder import (
    FaultRecorder,
    configure_cli_stdout,
    record_input,
    redact,
    summarize,
)


class TestFaultRecorder(unittest.TestCase):
    def test_cli_stdout_is_configured_for_unicode_json(self):
        stream = Mock()
        self.assertTrue(configure_cli_stdout(stream))
        stream.reconfigure.assert_called_once_with(encoding='utf-8', errors='replace')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.recorder = FaultRecorder(self.root)

    def emit(self, message, level=logging.ERROR):
        self.recorder.emit(logging.LogRecord('ok', level, __file__, 1, message, (), None))

    def test_fault_includes_preceding_input_and_deduplicates(self):
        self.recorder.append({'kind': 'input_request', 'key': '2'})
        self.emit('PostMessage invalid window 12345')
        self.emit('PostMessage invalid window 98765')
        self.assertEqual(1, self.recorder.pending.qsize())
        payload = self.recorder.pending.get_nowait()
        self.assertEqual('2', payload['timeline'][0]['key'])
        path = self.recorder.save(payload)
        self.assertEqual('script_fault', json.loads(path.read_text(encoding='utf-8'))['kind'])
        self.assertFalse(list(path.parent.glob('*.tmp')))

    def test_ordinary_info_does_not_create_incident(self):
        self.emit('combat started', logging.INFO)
        self.assertTrue(self.recorder.pending.empty())
        self.emit('windows_graphics:no frame for 10 sec', logging.WARNING)
        self.assertEqual(1, self.recorder.pending.qsize())

    def test_bounded_buffers_drop_without_blocking(self):
        for i in range(2100):
            self.recorder.append({'kind': 'input_request', 'key': 'r'})
        self.assertEqual(2000, len(self.recorder.snapshot()))
        for suffix in 'abcdefghijk':
            self.emit('failure ' + suffix)
        self.assertEqual(8, self.recorder.pending.qsize())
        self.assertEqual(3, self.recorder.dropped)

    def test_redaction_keeps_crash_address_but_masks_account(self):
        result = redact('user@example.com 112674946 token=abc address 0x0000000000000010')
        self.assertNotIn('112674946', result)
        self.assertNotIn('user@example.com', result)
        self.assertNotIn('abc', result)
        self.assertIn('0x0000000000000010', result)

    def create_crash(self, text):
        client = self.root / 'game' / 'Client'
        path = client / 'Saved' / 'Crashes' / 'UE-test' / 'CrashContext.runtime-xml'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        os.utime(path, (time.time() - 10, time.time() - 10))
        config = self.root / 'configs' / 'devices.json'
        config.parent.mkdir(exist_ok=True)
        config.write_text(json.dumps({'pc_full_path': str(client / 'Binaries' / 'Win64' /
                                                        'Client-Win64-Shipping.exe')}))
        return path

    def test_crash_import_is_idempotent_and_historical_has_no_current_inputs(self):
        self.create_crash('<FGenericCrashContext><RuntimeProperties><CrashType>Crash</CrashType>'
                          '<ErrorMessage>EXCEPTION_ACCESS_VIOLATION 0x10</ErrorMessage>'
                          '</RuntimeProperties></FGenericCrashContext>')
        self.recorder.append({'kind': 'input_request', 'key': 'r'})
        self.recorder.scan_game_crashes()
        self.recorder.scan_game_crashes()
        paths = list(self.recorder.folder.glob('incident-*.json'))
        self.assertEqual(1, len(paths))
        payload = json.loads(paths[0].read_text())
        self.assertTrue(payload['historical'])
        self.assertEqual([], payload['timeline'])
        self.assertEqual('game_crash', payload['kind'])

    def test_partial_crash_report_retried(self):
        path = self.create_crash('<FGenericCrashContext>')
        self.recorder.scan_game_crashes()
        self.assertFalse(list(self.recorder.folder.glob('incident-*.json')))
        path.write_text('<FGenericCrashContext><RuntimeProperties><ErrorMessage>crash</ErrorMessage>'
                        '</RuntimeProperties></FGenericCrashContext>')
        os.utime(path, (time.time() - 10, time.time() - 10))
        self.recorder.scan_game_crashes()
        self.assertEqual(1, len(list(self.recorder.folder.glob('incident-*.json'))))

    def test_retention_does_not_delete_review_state_or_other_files(self):
        self.recorder.retention = 2
        self.recorder.folder.mkdir(parents=True)
        state = self.recorder.folder / 'analysis-state.json'
        state.write_text('{}')
        for _ in range(3):
            self.recorder.save({'kind': 'script_fault'})
        self.assertEqual(2, len(list(self.recorder.folder.glob('incident-*.json'))))
        self.assertTrue(state.exists())

    def test_retention_respects_disk_budget(self):
        self.recorder.max_disk_bytes = 500
        for _ in range(4):
            self.recorder.save({'kind': 'script_fault', 'message': 'x' * 150})
        self.assertLessEqual(sum(p.stat().st_size for p in self.recorder.folder.glob('incident-*.json')), 500)

    def test_crash_write_permission_failure_is_visible(self):
        self.create_crash('<FGenericCrashContext><RuntimeProperties><ErrorMessage>crash</ErrorMessage>'
                          '</RuntimeProperties></FGenericCrashContext>')
        with patch.object(self.recorder, 'save', side_effect=PermissionError('denied')):
            self.recorder.scan_game_crashes()
        self.assertIn('PermissionError', self.recorder.last_error)

    def test_timeline_excludes_events_older_than_two_minutes(self):
        with patch('src.utils.FaultRecorder.time.time', return_value=time.time() - 121):
            self.recorder.append({'kind': 'input_request', 'key': 'old'})
        self.recorder.append({'kind': 'input_request', 'key': 'new'})
        self.assertEqual(['new'], [event['key'] for event in self.recorder.snapshot()])

    def test_summary_excludes_reviewed_records(self):
        path = self.recorder.save({'kind': 'script_fault', 'message': 'example', 'utc': 'now'})
        self.assertEqual(1, len(summarize(self.root)['new_groups']))
        (self.recorder.folder / 'analysis-state.json').write_text(json.dumps({'analyzed': [path.name]}))
        self.assertEqual([], summarize(self.root)['new_groups'])

    def test_record_input_uses_existing_state_without_calling_game(self):
        task = SimpleNamespace(chars=[], executor=SimpleNamespace(current_task=None))
        with patch('src.utils.FaultRecorder._recorder', self.recorder):
            record_input(task, 'key_down', 'r')
        self.assertEqual('r', self.recorder.snapshot()[0]['key'])

    def test_background_worker_flushes_and_detaches_on_shutdown(self):
        stop = threading.Event()
        recorder = FaultRecorder(self.root, stop).start()
        try:
            recorder.emit(logging.LogRecord('ok', logging.ERROR, __file__, 1, 'test failure', (), None))
        finally:
            stop.set()
            recorder.thread.join(timeout=3)
        self.assertFalse(recorder.thread.is_alive())
        self.assertNotIn(recorder, logging.getLogger('ok').handlers)
        self.assertEqual(1, len(list(recorder.folder.glob('incident-*.json'))))
        self.assertTrue(list(recorder.folder.glob('recent-*.json')))


if __name__ == '__main__':
    unittest.main()
