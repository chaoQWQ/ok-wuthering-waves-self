import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ok.util.logger import Logger


logger = Logger.get_logger('GameplayRecording')


def publish_diagnostic(entry):
    getattr(logger, entry['level'])(json.dumps(entry, ensure_ascii=False))


class RecordingDiagnostics:
    def __init__(self, root, session_id, component, publish=publish_diagnostic):
        self.session_id = session_id
        self.component = component
        self.directory = Path(root) / 'diagnostics' / session_id
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / f'{component}.jsonl'
        self.lock = threading.Lock()
        self.publish = publish

    def write(self, event, message, *, level='info', **details):
        entry = {
            'time': datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='milliseconds'),
            'monotonic': time.monotonic(), 'session_id': self.session_id,
            'component': self.component, 'level': level, 'event': event, 'message': message, **details,
        }
        line = json.dumps(entry, ensure_ascii=False)
        with self.lock:
            with self.path.open('a', encoding='utf-8') as output:
                output.write(line + '\n')
        self.publish(entry)

    def summary(self, directory, **details):
        payload = {
            'session_id': self.session_id,
            'session_logs': [f'diagnostics/{self.session_id}/{name}.jsonl' for name in ('capture', 'worker')],
            **details,
        }
        (Path(directory) / 'diagnostics.json').write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
