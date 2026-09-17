"""Bounded, passive flight recorder. Never captures frames or sends game input."""
import hashlib
import json
import logging
import os
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, Full, Queue
import re
import sys
import threading
import time
import uuid
import xml.etree.ElementTree as ET


def redact(text):
    text = str(text)[:16000]
    text = re.sub(r'[\w.*+-]+@[\w.-]+', '[account]', text)
    text = re.sub(r'\b\d{7,}\b', '[id]', text)
    text = re.sub(r'(?i)((?:password|token|secret)\s*[=:]\s*)\S+', r'\1[redacted]', text)
    return text


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


class FaultRecorder(logging.Handler):
    def __init__(self, project_root, exit_event=None, retention=100):
        super().__init__(logging.INFO)
        self.root = Path(project_root)
        self.folder = self.root / 'logs' / 'faults'
        self.exit_event = exit_event or threading.Event()
        self.retention = retention
        self.max_disk_bytes = 64 * 1024 * 1024
        self.events = deque(maxlen=2000)
        self.pending = Queue(maxsize=8)
        self.guard = threading.Lock()
        self.recent_faults = {}
        self.started_at = time.time()
        self.session = uuid.uuid4().hex[:12]
        self.dropped = 0
        self.last_error = None
        self.thread = None

    def start(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        logging.getLogger('ok').addHandler(self)
        self.thread = threading.Thread(target=self._work, name='FaultRecorder', daemon=True)
        self.thread.start()
        return self

    def append(self, event):
        event = dict(event, utc=utc_now(), monotonic_ns=time.monotonic_ns(), epoch=time.time())
        with self.guard:
            self.events.append(event)

    def snapshot(self):
        cutoff = time.time() - 120
        with self.guard:
            return [dict(event) for event in self.events if event['epoch'] >= cutoff]

    def emit(self, record):
        try:
            if record.threadName == 'FaultRecorder':
                return
            message = redact(self.format(record))
            self.append({'kind': 'log', 'level': record.levelname,
                         'thread': record.threadName, 'message': message[:4096]})
            warning = any(marker in message.lower() for marker in (
                'no frame for', 'disconnected', 'invalid params', 'leave failed'))
            if record.levelno >= logging.ERROR or warning:
                signature = re.sub(r'0x[0-9a-fA-F]+|\d+', '#', message.split('Traceback')[0])
                fingerprint = hashlib.sha256(signature.encode()).hexdigest()[:16]
                now = time.monotonic()
                with self.guard:
                    self.recent_faults = {k: v for k, v in self.recent_faults.items() if now - v < 60}
                    if fingerprint in self.recent_faults:
                        return
                    self.recent_faults[fingerprint] = now
                payload = {'kind': 'script_fault', 'utc': utc_now(), 'fingerprint': fingerprint,
                           'message': message, 'timeline': self.snapshot()}
                try:
                    self.pending.put_nowait(payload)
                except Full:
                    self.dropped += 1
        except Exception:
            # Diagnostics must never change task behavior or recurse into logging.
            self.dropped += 1

    def _atomic_json(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f'.{self.session}.tmp')
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)

    def save(self, payload, identity=None):
        identity = identity or uuid.uuid4().hex
        path = self.folder / f'incident-{identity}.json'
        if not path.exists():
            self._atomic_json(path, dict(payload, schema=1, session=self.session,
                                         process_id=os.getpid()))
        self.prune()
        return path

    def prune(self):
        files = sorted(self.folder.glob('incident-*.json'), key=lambda p: p.stat().st_mtime,
                       reverse=True)
        cutoff = time.time() - 14 * 86400
        retained_bytes = 0
        for index, path in enumerate(files):
            # Delete only this recorder's files, never traverse or delete directories.
            if not path.is_symlink() and path.resolve().parent == self.folder.resolve():
                retained_bytes += path.stat().st_size
                if (index >= self.retention or path.stat().st_mtime < cutoff
                        or retained_bytes > self.max_disk_bytes):
                    path.unlink()

    def scan_game_crashes(self):
        self.last_error = None
        device_file = self.root / 'configs' / 'devices.json'
        if not device_file.exists():
            return
        devices = json.loads(device_file.read_text(encoding='utf-8'))
        executable = Path(devices.get('pc_full_path') or '')
        # Resolve only the configured Client executable; never scan entire drives.
        if executable.name.lower() != 'client-win64-shipping.exe':
            return
        crash_root = executable.parent.parent.parent / 'Saved' / 'Crashes'
        if not crash_root.exists():
            return
        # iterdir surfaces access errors; glob may silently look like no faults.
        for directory in crash_root.iterdir():
            if not directory.is_dir():
                continue
            path = directory / 'CrashContext.runtime-xml'
            if not path.exists():
                continue
            try:
                stat = path.stat()
                if stat.st_mtime < time.time() - 14 * 86400 or stat.st_size > 2 * 1024 * 1024:
                    continue
                identity = hashlib.sha256(str(path).encode()).hexdigest()[:24]
                if (self.folder / f'incident-{identity}.json').exists():
                    continue
                # A newly created report may still be being written; retry next scan.
                if time.time() - stat.st_mtime < 3:
                    continue
                tree = ET.fromstring(path.read_bytes())
                props = tree.find('RuntimeProperties')
                if props is None or not props.findtext('ErrorMessage'):
                    continue
                summary = {name: redact(props.findtext(name, '')) for name in (
                    'CrashType', 'ErrorMessage', 'SecondsSinceStart', 'PCallStack',
                    'MemoryStats.bIsOOM', 'Misc.PrimaryGPUBrand')}
                summary['crashed_threads'] = [node.findtext('ThreadName', '')
                    for node in props.findall('Threads/Thread')
                    if node.findtext('IsCrashed', '').lower() in ('true', '1')]
                historical = stat.st_mtime < self.started_at
                self.save({'kind': 'game_crash', 'utc': utc_now(), 'report_path': str(path),
                           'report_mtime': stat.st_mtime, 'historical': historical,
                           'summary': summary,
                           'timeline': [] if historical else self.snapshot()}, identity)
            except (OSError, ET.ParseError, ValueError) as error:
                self.last_error = f'Crash report unavailable: {type(error).__name__}'

    def checkpoint(self):
        self._atomic_json(self.folder / f'recent-{os.getpid()}.json', {
            'schema': 1, 'session': self.session, 'utc': utc_now(), 'timeline': self.snapshot(),
            'dropped_events': self.dropped, 'recorder_error': self.last_error,
            'note': 'Task requests only; not proof the game received or accepted input.',
        })
        recent = sorted(self.folder.glob('recent-*.json'), key=lambda p: p.stat().st_mtime,
                        reverse=True)
        for path in recent[10:]:
            if not path.is_symlink() and path.resolve().parent == self.folder.resolve():
                path.unlink()

    def _work(self):
        next_scan = 0
        while not self.exit_event.is_set() or not self.pending.empty():
            try:
                try:
                    payload = self.pending.get(timeout=0.5)
                except Empty:
                    payload = None
                if payload is not None:
                    self.save(payload)
                if time.monotonic() >= next_scan:
                    next_scan = time.monotonic() + 15
                    try:
                        self.scan_game_crashes()
                    except Exception as error:
                        self.last_error = f'Crash scan failed: {type(error).__name__}'
                    self.checkpoint()
                    self.prune()
            except Exception as error:
                self.last_error = f'Recorder write failed: {type(error).__name__}'
        try:
            self.checkpoint()
        except OSError:
            pass
        logging.getLogger('ok').removeHandler(self)


_recorder = None


def start_fault_recorder(project_root, exit_event):
    global _recorder
    if os.environ.get('OK_WW_FAULT_RECORDING', '1') == '0':
        return None
    if _recorder is None or (_recorder.thread and not _recorder.thread.is_alive()):
        try:
            _recorder = FaultRecorder(project_root, exit_event).start()
        except OSError as error:
            logging.getLogger('ok').warning('Fault recorder unavailable: %s', error)
    return _recorder


def record_input(task, operation, key=None):
    """Record task requests in RAM, not keystrokes typed by the user."""
    if _recorder is None:
        return
    try:
        chars = getattr(task, 'chars', None) or []
        current = next((char for char in chars if char and char.is_current_char), None)
        executor = getattr(task, 'executor', None)
        owner = getattr(executor, 'current_task', None)
        _recorder.append({'kind': 'input_request', 'operation': operation,
                          'key': str(key)[:20] if key is not None else None,
                          'task': type(task).__name__, 'owner_task': type(owner).__name__,
                          'character': type(current).__name__ if current else None,
                          'character_module': type(current).__module__ if current else None})
    except Exception:
        pass


def summarize(project_root):
    folder = Path(project_root) / 'logs' / 'faults'
    state_file = folder / 'analysis-state.json'
    state = json.loads(state_file.read_text(encoding='utf-8')) if state_file.exists() else {}
    analyzed = set(state.get('analyzed', []))
    groups = {}
    unreadable = []
    paths = sorted(p for p in folder.iterdir() if p.name.startswith('incident-')
                   and p.suffix == '.json') if folder.exists() else []
    for path in paths:
        if path.name in analyzed:
            continue
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            message = data.get('message') or data.get('summary', {}).get('ErrorMessage', '')
            key = data.get('fingerprint') or hashlib.sha256(message.encode()).hexdigest()[:16]
            group = groups.setdefault(key, {'kind': data.get('kind'), 'message': message,
                                             'incidents': [], 'last_utc': ''})
            group['incidents'].append(path.name)
            group['last_utc'] = max(group['last_utc'], data.get('utc', ''))
        except (OSError, ValueError, TypeError, AttributeError):
            unreadable.append(path.name)
    return {'new_groups': list(groups.values()), 'unreadable': unreadable,
            'folder': str(folder), 'state_file': str(state_file)}


def configure_cli_stdout(stream=None):
    """Keep diagnostic JSON printable on Windows consoles using GBK defaults."""
    stream = stream or sys.stdout
    reconfigure = getattr(stream, 'reconfigure', None)
    if reconfigure is None:
        return False
    try:
        reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, OSError, ValueError):
        return False
    return True


if __name__ == '__main__':
    configure_cli_stdout()
    import argparse
    parser = argparse.ArgumentParser(description='Collect existing crash reports and summarize unreviewed faults.')
    parser.add_argument('--collect', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    collection_error = None
    if args.collect:
        recorder = FaultRecorder(root)
        recorder.scan_game_crashes()
        collection_error = recorder.last_error
    print(json.dumps(dict(summarize(root), collection_error=collection_error), ensure_ascii=False, indent=2))
    if collection_error:
        raise SystemExit(1)
