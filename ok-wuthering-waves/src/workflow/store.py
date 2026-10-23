import json
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
                "SELECT id FROM recordings WHERE status='complete' AND analyzed=0 ORDER BY started")]

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
                'SELECT id,started,status,analyzed,reason FROM recordings ORDER BY started DESC LIMIT 100')]

    def analyzed(self, recording_id, reasons):
        with self.connect() as db:
            db.execute('UPDATE recordings SET analyzed=1,reason=? WHERE id=?',
                       ('；'.join(reasons), recording_id))

    def save_workflow(self, workflow, status='compiled'):
        if status != 'compiled':
            raise ValueError('新流程必须经过独立验证')
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO workflows VALUES(?,?,?,?,?,?,?)',
                       (workflow.revision, workflow.content_id, workflow.period, workflow.title,
                        status, workflow.model_dump_json(), time.time()))

    def validate(self, revision, kind, evidence):
        if kind not in ('offline_checked', 'runtime_verified'):
            raise ValueError('未知验证状态')
        with self.connect() as db:
            row = db.execute('SELECT status FROM workflows WHERE revision=?', (revision,)).fetchone()
            if row is None or (kind == 'runtime_verified' and row['status'] not in ('offline_checked', 'runtime_verified')):
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
