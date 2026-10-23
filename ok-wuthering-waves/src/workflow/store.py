import json
import shutil
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from src.workflow.models import Observation, Workflow


class WorkflowStateStore:
    def __init__(self, directory):
        self.root = Path(directory).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'workflows.sqlite3'
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS recordings (
                    id TEXT PRIMARY KEY, started REAL NOT NULL, status TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '', analyzed INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS recording_environment (
                    recording_id TEXT PRIMARY KEY REFERENCES recordings(id),
                    game_version TEXT NOT NULL, language TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS observations (
                    id TEXT PRIMARY KEY, recording_id TEXT NOT NULL REFERENCES recordings(id),
                    timestamp REAL NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS observation_time ON observations(recording_id,timestamp);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, recording_id TEXT NOT NULL REFERENCES recordings(id),
                    timestamp REAL NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workflows (
                    revision TEXT PRIMARY KEY, content_id TEXT NOT NULL, period TEXT NOT NULL,
                    title TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS account_progress (
                    account TEXT NOT NULL, content_id TEXT NOT NULL, period TEXT NOT NULL,
                    revision TEXT NOT NULL, step_id TEXT, status TEXT NOT NULL,
                    evidence TEXT NOT NULL, updated REAL NOT NULL,
                    PRIMARY KEY(account,content_id,period));
                CREATE TABLE IF NOT EXISTS step_attempts (
                    id TEXT PRIMARY KEY, account TEXT NOT NULL, revision TEXT NOT NULL,
                    step_id TEXT NOT NULL, started REAL NOT NULL, status TEXT NOT NULL,
                    evidence TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS accounts (
                    identity TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS login_attempts (
                    id INTEGER PRIMARY KEY, account TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS validations (
                    id INTEGER PRIMARY KEY, revision TEXT NOT NULL, kind TEXT NOT NULL,
                    evidence TEXT NOT NULL, created REAL NOT NULL);
            ''')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(recordings)')}
            if 'name' not in columns:
                db.execute("ALTER TABLE recordings ADD COLUMN name TEXT NOT NULL DEFAULT ''")
            if 'deleted' not in columns:
                db.execute('ALTER TABLE recordings ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA journal_mode=WAL')
        try:
            with db:
                yield db
        finally:
            db.close()

    def resolve(self, relative):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('证据路径超出记录目录')
        return path

    def start_recording(self):
        identifier = uuid.uuid4().hex
        (self.root / identifier).mkdir()
        with self.connect() as db:
            db.execute('INSERT INTO recordings(id,started,status) VALUES(?,?,?)',
                       (identifier, time.time(), 'recording'))
        return identifier

    def finish_recording(self, identifier, status, reason=''):
        with self.connect() as db:
            db.execute('UPDATE recordings SET status=?,reason=? WHERE id=?', (status, reason, identifier))

    def set_environment(self, identifier, game_version, language):
        with self.connect() as db:
            db.execute('INSERT INTO recording_environment VALUES(?,?,?)', (identifier, game_version, language))

    def environment(self, identifier):
        with self.connect() as db:
            row = db.execute('SELECT game_version,language FROM recording_environment WHERE recording_id=?', (identifier,)).fetchone()
        if row is None:
            raise ValueError('录制材料缺少游戏版本和语言信息')
        return dict(row)

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE recordings SET status='interrupted', reason='进程退出时录制尚未结束' WHERE status='recording'")
            db.execute("UPDATE step_attempts SET status='interrupted' WHERE status='prepared'")
            db.execute("UPDATE account_progress SET status='interrupted' WHERE status='running'")

    def observe(self, recording_id, observation):
        with self.connect() as db:
            db.execute('INSERT INTO observations VALUES(?,?,?,?)',
                       (observation.frame_id, recording_id, observation.timestamp, observation.model_dump_json()))

    def observations(self, recording_id):
        with self.connect() as db:
            rows = db.execute('SELECT payload FROM observations WHERE recording_id=? ORDER BY timestamp',
                              (recording_id,)).fetchall()
        return [Observation.model_validate_json(row['payload']) for row in rows]

    def event(self, recording_id, timestamp, kind, payload):
        with self.connect() as db:
            db.execute('INSERT INTO events(recording_id,timestamp,kind,payload) VALUES(?,?,?,?)',
                       (recording_id, timestamp, kind, json.dumps(payload, ensure_ascii=False)))

    def events_batch(self, recording_id, events):
        with self.connect() as db:
            db.executemany('INSERT INTO events(recording_id,timestamp,kind,payload) VALUES(?,?,?,?)',
                           ((recording_id, timestamp, kind, json.dumps(payload, ensure_ascii=False))
                            for timestamp, kind, payload in events))

    def events(self, recording_id):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM events WHERE recording_id=? ORDER BY timestamp,id',
                                                  (recording_id,))]

    def pending_recordings(self):
        with self.connect() as db:
            return [row['id'] for row in db.execute(
                "SELECT id FROM recordings WHERE status='complete' AND analyzed=0 AND deleted=0 ORDER BY started")]

    def recording(self, identifier):
        with self.connect() as db:
            row = db.execute('SELECT * FROM recordings WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise ValueError('录制记录不存在')
        return dict(row)

    def record_login(self, account, status):
        with self.connect() as db:
            db.execute('INSERT INTO login_attempts(account,status,created) VALUES(?,?,?)',
                       (account, status, time.time()))

    def save_account(self, account):
        with self.connect() as db:
            db.execute('INSERT INTO accounts VALUES(?,?) ON CONFLICT(identity) DO UPDATE SET payload=excluded.payload',
                       (account.identity, account.model_dump_json()))

    def list_recordings(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                'SELECT id,name,started,status,analyzed,reason FROM recordings WHERE deleted=0 ORDER BY started DESC LIMIT 100')]

    def rename_recording(self, identifier, name):
        name = name.strip()
        if not name or len(name) > 160:
            raise ValueError('记录名称需要包含 1 至 160 个字符')
        with self.connect() as db:
            result = db.execute('UPDATE recordings SET name=? WHERE id=? AND deleted=0', (name, identifier))
            if result.rowcount != 1:
                raise ValueError('录制记录不存在')

    def recording_materials(self, identifier):
        recording = self.recording(identifier)
        if recording['deleted']:
            raise ValueError('录制记录已删除')
        directory = self.resolve(identifier)
        if directory.parent != self.root or directory.name != identifier or (self.root / identifier).is_symlink():
            raise ValueError('录制材料目录无效')
        if not directory.is_dir():
            raise FileNotFoundError('录制材料目录不存在')
        observations = self.observations(identifier)
        events = [{**event, 'payload': json.loads(event['payload'])} for event in self.events(identifier)]
        with self.connect() as db:
            environment = db.execute('SELECT game_version,language FROM recording_environment WHERE recording_id=?',
                                     (identifier,)).fetchone()
        exports = {
            'recording-details.json': ('记录信息', {**recording, 'environment': dict(environment) if environment else None}),
            'observations.json': ('画面与任务识别', [item.model_dump() for item in observations]),
            'events.json': ('键鼠与记录事件', events),
        }
        materials = []
        for name, (kind, payload) in exports.items():
            path = directory / name
            if path.is_symlink():
                raise ValueError('材料导出文件不能使用符号链接')
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
            materials.append({'kind': kind, 'path': path})
        for path in sorted(directory.iterdir()):
            if path.suffix.lower() in ('.png', '.mkv'):
                if path.is_symlink() or not path.is_file():
                    raise ValueError('录制材料文件无效')
                materials.append({'kind': '关键截图' if path.suffix.lower() == '.png' else '录像', 'path': path})
        summary = directory / 'diagnostics.json'
        if summary.is_symlink():
            raise ValueError('录制诊断文件不能使用符号链接')
        if summary.is_file():
            materials.append({'kind': '片段诊断汇总', 'path': summary})
            diagnostics = json.loads(summary.read_text(encoding='utf-8'))
            for relative in diagnostics['session_logs']:
                path = self.resolve(relative)
                if (not path.is_relative_to(self.root / 'diagnostics') or path.suffix != '.jsonl'
                        or (self.root / relative).is_symlink()):
                    raise ValueError('录制会话日志路径无效')
                if path.is_file():
                    materials.append({'kind': '录制会话日志', 'path': path})
        analysis_directory = self.resolve('agy')
        if analysis_directory.parent != self.root or (self.root / 'agy').is_symlink():
            raise ValueError('分析材料目录无效')
        for request in sorted(analysis_directory.glob('*.json')):
            if request.is_symlink():
                raise ValueError('分析请求文件不能使用符号链接')
            payload = json.loads(request.read_text(encoding='utf-8'))
            if payload.get('recording_id') != identifier:
                continue
            for suffix, kind in (('.json', 'AGY 分析请求'), ('.md', 'AGY 分析说明'),
                                 ('.result.txt', 'AGY 分析结果')):
                path = request.with_suffix(suffix)
                if path.is_symlink():
                    raise ValueError('分析材料文件不能使用符号链接')
                if path.is_file():
                    materials.append({'kind': kind, 'path': path})
        return {
            'recording': recording, 'directory': directory, 'files': materials,
            'observations': len(observations), 'input_events': sum(event['kind'] == 'input' for event in events),
        }

    def delete_recordings(self, identifiers, delete_materials=False):
        identifiers = set(identifiers)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            directories = {}
            for identifier in identifiers:
                row = db.execute('SELECT status,deleted FROM recordings WHERE id=?', (identifier,)).fetchone()
                if row is None or row['deleted'] or row['status'] == 'recording':
                    raise ValueError('记录不存在或仍在录制，无法删除')
                if delete_materials:
                    directory = self.resolve(identifier)
                    if directory.parent != self.root or directory.name != identifier or (self.root / identifier).is_symlink():
                        raise ValueError('录制材料目录无效')
                    directories[identifier] = directory
            analysis_files = []
            if delete_materials:
                analysis_directory = self.resolve('agy')
                if analysis_directory.parent != self.root or analysis_directory.name != 'agy' or (self.root / 'agy').is_symlink():
                    raise ValueError('分析材料目录无效')
                for request in analysis_directory.glob('*.json'):
                    if request.is_symlink() or request.resolve().parent != analysis_directory:
                        raise ValueError('分析材料路径无效')
                    payload = json.loads(request.read_text(encoding='utf-8'))
                    if payload['recording_id'] in identifiers:
                        for suffix in ('.json', '.md', '.result.txt'):
                            path = request.with_suffix(suffix)
                            if path.is_symlink() or path.resolve().parent != analysis_directory:
                                raise ValueError('分析材料路径无效')
                            if path.exists():
                                analysis_files.append(path)
            for identifier in identifiers:
                result = db.execute("UPDATE recordings SET deleted=1 WHERE id=? AND deleted=0 AND status!='recording'",
                                    (identifier,))
                if result.rowcount != 1:
                    raise ValueError('记录不存在或仍在录制，无法删除')
                if delete_materials:
                    directory = directories[identifier]
                    if directory.exists():
                        shutil.rmtree(directory)
                    db.execute('DELETE FROM observations WHERE recording_id=?', (identifier,))
                    db.execute('DELETE FROM events WHERE recording_id=?', (identifier,))
                    db.execute('DELETE FROM recording_environment WHERE recording_id=?', (identifier,))
                    db.execute("UPDATE recordings SET status='materials_deleted' WHERE id=?", (identifier,))
                    db.execute("UPDATE workflows SET status='materials_deleted' WHERE json_extract(payload,'$.recording_id')=?",
                               (identifier,))
            for path in analysis_files:
                path.unlink()

    def analyzed(self, recording_id, reasons):
        with self.connect() as db:
            db.execute('UPDATE recordings SET analyzed=1,reason=? WHERE id=?',
                       ('；'.join(reasons), recording_id))

    def save_workflow(self, workflow, status='compiled'):
        if status != 'compiled':
            raise ValueError('新流程必须经过独立验证')
        with self.connect() as db:
            row = db.execute('SELECT status FROM recordings WHERE id=?', (workflow.recording_id,)).fetchone()
            if row is None or row['status'] == 'materials_deleted':
                raise ValueError('流程所需的录制材料不存在')
            db.execute('INSERT OR IGNORE INTO workflows VALUES(?,?,?,?,?,?,?)',
                       (workflow.revision, workflow.content_id, workflow.period, workflow.title,
                        status, workflow.model_dump_json(), time.time()))

    def validate(self, revision, kind, evidence):
        if kind not in ('offline_checked', 'runtime_verified'):
            raise ValueError('未知验证状态')
        with self.connect() as db:
            row = db.execute('SELECT status FROM workflows WHERE revision=?', (revision,)).fetchone()
            if row is None or row['status'] == 'materials_deleted' or (kind == 'runtime_verified' and row['status'] not in ('offline_checked', 'runtime_verified')):
                raise ValueError('流程尚未通过必要检查')
            if row['status'] != 'runtime_verified':
                db.execute('UPDATE workflows SET status=? WHERE revision=?', (kind, revision))
            db.execute('INSERT INTO validations(revision,kind,evidence,created) VALUES(?,?,?,?)',
                       (revision, kind, json.dumps(evidence, ensure_ascii=False), time.time()))

    def workflow(self, revision, executable=True):
        with self.connect() as db:
            row = db.execute('SELECT * FROM workflows WHERE revision=?', (revision,)).fetchone()
        if row is None or (executable and row['status'] not in ('offline_checked', 'runtime_verified')):
            raise ValueError('流程不存在或尚未通过录像检查')
        workflow = Workflow.model_validate_json(row['payload'])
        if workflow.revision != revision:
            raise ValueError('流程内容与已检查版本不符')
        return workflow

    def list_workflows(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT revision,title,status,created FROM workflows ORDER BY created DESC')]

    def list_progress(self):
        with self.connect() as db:
            rows = db.execute('''SELECT p.*,a.payload AS account_payload,w.title
                FROM account_progress p LEFT JOIN accounts a ON a.identity=p.account
                LEFT JOIN workflows w ON w.revision=p.revision ORDER BY p.updated DESC''').fetchall()
        result = []
        for row in rows:
            item = dict(row)
            profile = json.loads(item.pop('account_payload') or '{}')
            item['account_name'] = profile.get('login_name', item['account'][:12])
            result.append(item)
        return result

    def progress(self, account, workflow, period=None):
        with self.connect() as db:
            row = db.execute('SELECT * FROM account_progress WHERE account=? AND content_id=? AND period=?',
                             (account, workflow.content_id, period or workflow.execution_period())).fetchone()
        return dict(row) if row else None

    def checkpoint(self, account, workflow, step_id, status, evidence, period=None):
        with self.connect() as db:
            db.execute('''INSERT INTO account_progress VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(account,content_id,period) DO UPDATE SET
                revision=excluded.revision,step_id=excluded.step_id,status=excluded.status,
                evidence=excluded.evidence,updated=excluded.updated
                WHERE account_progress.status != 'completed' ''',
                       (account, workflow.content_id, period or workflow.execution_period(), workflow.revision, step_id,
                        status, json.dumps(evidence, ensure_ascii=False), time.time()))

    def prepare(self, account, workflow, step):
        identifier = uuid.uuid4().hex
        with self.connect() as db:
            db.execute('INSERT INTO step_attempts VALUES(?,?,?,?,?,?,?)',
                       (identifier, account, workflow.revision, step.step_id, time.time(), 'prepared', ''))
        return identifier

    def finish_attempt(self, identifier, status, evidence):
        with self.connect() as db:
            db.execute('UPDATE step_attempts SET status=?,evidence=? WHERE id=?',
                       (status, json.dumps(evidence, ensure_ascii=False), identifier))
