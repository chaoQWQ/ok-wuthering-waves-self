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
                CREATE TABLE IF NOT EXISTS recording_sessions (
                    recording_id TEXT PRIMARY KEY REFERENCES recordings(id),
                    plan_status TEXT NOT NULL DEFAULT 'pending', plan_reason TEXT NOT NULL DEFAULT '',
                    revisions TEXT NOT NULL DEFAULT '[]');
                CREATE TABLE IF NOT EXISTS recording_material_cleanup (
                    recording_id TEXT PRIMARY KEY REFERENCES recordings(id),
                    paths TEXT NOT NULL);
            ''')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(recordings)')}
            if 'name' not in columns:
                db.execute("ALTER TABLE recordings ADD COLUMN name TEXT NOT NULL DEFAULT ''")
            if 'deleted' not in columns:
                db.execute('ALTER TABLE recordings ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0')
            for column, definition in (
                    ('parent_id', "TEXT NOT NULL DEFAULT ''"),
                    ('position', 'INTEGER NOT NULL DEFAULT 0'),
                    ('name_source', "TEXT NOT NULL DEFAULT ''"),
                    ('analysis_noop', 'INTEGER NOT NULL DEFAULT 0')):
                if column not in columns:
                    db.execute(f'ALTER TABLE recordings ADD COLUMN {column} {definition}')
            db.execute("UPDATE recordings SET name_source='manual' WHERE name!='' AND name_source=''")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS recording_child_position ON recordings(parent_id,position) "
                       "WHERE parent_id!=''")

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

    def start_recording(self, parent_id=''):
        identifier = uuid.uuid4().hex
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            position = 0
            if parent_id:
                parent = db.execute('SELECT r.status FROM recordings r JOIN recording_sessions s '
                                    'ON s.recording_id=r.id WHERE r.id=? AND r.deleted=0', (parent_id,)).fetchone()
                if parent is None or parent['status'] != 'recording':
                    raise ValueError('整次录制已经结束，不能添加分段')
                position = db.execute('SELECT COALESCE(MAX(position),0)+1 FROM recordings WHERE parent_id=?',
                                      (parent_id,)).fetchone()[0]
            (self.root / identifier).mkdir()
            db.execute('INSERT INTO recordings(id,started,status,parent_id,position) VALUES(?,?,?,?,?)',
                       (identifier, time.time(), 'recording', parent_id, position))
        return identifier

    def start_session(self):
        identifier = self.start_recording()
        with self.connect() as db:
            db.execute('INSERT INTO recording_sessions(recording_id) VALUES(?)', (identifier,))
        return identifier

    def session_segments(self, identifier):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                'SELECT * FROM recordings WHERE parent_id=? ORDER BY position', (identifier,))]

    def is_session(self, identifier):
        with self.connect() as db:
            return db.execute('SELECT 1 FROM recording_sessions WHERE recording_id=?', (identifier,)).fetchone() is not None

    def finish_session(self, identifier):
        if not self.is_session(identifier):
            raise ValueError('完整录制记录不存在')
        if self.recording(identifier)['status'] != 'recording':
            return
        segments = self.session_segments(identifier)
        gaps = [item['reason'] or '分段录制未完整结束' for item in segments if item['status'] != 'complete']
        gaps.extend(json.loads(item['payload'])['reason'] for item in self.events(identifier)
                    if item['kind'] == 'capture_gap')
        if segments:
            environments = [self.environment(item['id']) for item in segments]
            if any(item != environments[0] for item in environments):
                gaps.append('录制期间游戏版本或语言发生变化')
            self.set_environment(identifier, **environments[0])
        self.finish_recording(identifier, 'interrupted' if gaps else 'complete', '；'.join(dict.fromkeys(gaps)))

    def pending_sessions(self):
        with self.connect() as db:
            return [row['id'] for row in db.execute('''SELECT r.id FROM recordings r
                JOIN recording_sessions s ON s.recording_id=r.id
                WHERE r.status!='recording' AND s.plan_status='pending' AND r.deleted=0
                AND (r.analyzed=1 OR r.status!='complete'
                    OR NOT EXISTS (SELECT 1 FROM recordings c WHERE c.parent_id=r.id)) ORDER BY r.started''')]

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
        self.cleanup_recording_materials()

    def observe(self, recording_id, observation):
        with self.connect() as db:
            db.execute('INSERT INTO observations VALUES(?,?,?,?)',
                       (observation.frame_id, recording_id, observation.timestamp, observation.model_dump_json()))

    def observations(self, recording_id):
        with self.connect() as db:
            rows = db.execute('SELECT payload FROM observations WHERE recording_id=? OR recording_id IN '
                              '(SELECT id FROM recordings WHERE parent_id=?) ORDER BY timestamp',
                              (recording_id, recording_id)).fetchall()
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
            return [dict(row) for row in db.execute('SELECT * FROM events WHERE recording_id=? OR recording_id IN '
                                                  '(SELECT id FROM recordings WHERE parent_id=?) ORDER BY timestamp,id',
                                                  (recording_id, recording_id))]

    def pending_recordings(self):
        with self.connect() as db:
            return [row['id'] for row in db.execute(
                "SELECT id FROM recordings WHERE status='complete' AND analyzed=0 AND deleted=0 AND parent_id='' "
                "AND (id NOT IN (SELECT recording_id FROM recording_sessions) OR EXISTS "
                "(SELECT 1 FROM recordings c WHERE c.parent_id=recordings.id)) ORDER BY started")]

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
                "SELECT id,name,started,status,analyzed,reason FROM recordings WHERE deleted=0 AND parent_id='' "
                'ORDER BY started DESC LIMIT 100')]

    def rename_recording(self, identifier, name):
        name = name.strip()
        if not name or len(name) > 160:
            raise ValueError('记录名称需要包含 1 至 160 个字符')
        with self.connect() as db:
            result = db.execute("UPDATE recordings SET name=?,name_source='manual' WHERE id=? AND deleted=0", (name, identifier))
            if result.rowcount != 1:
                raise ValueError('录制记录不存在')

    def analysis_name(self, identifier, name):
        name = name.strip()
        if not name or len(name) > 160:
            raise ValueError('分析生成的记录名称需要包含 1 至 160 个字符')
        with self.connect() as db:
            db.execute("UPDATE recordings SET name=?,name_source='analysis' WHERE id=? AND name_source!='manual'",
                       (name, identifier))

    def save_session_plan(self, identifier, revisions, reason=''):
        status = 'blocked' if reason else 'ready'
        with self.connect() as db:
            db.execute('UPDATE recording_sessions SET plan_status=?,plan_reason=?,revisions=? WHERE recording_id=?',
                       (status, reason, json.dumps(revisions), identifier))
            db.execute('UPDATE recordings SET analyzed=1,reason=? WHERE id=?', (reason, identifier))

    def session_plan(self, identifier):
        with self.connect() as db:
            row = db.execute('SELECT * FROM recording_sessions WHERE recording_id=?', (identifier,)).fetchone()
        if row is None:
            raise ValueError('完整录制记录不存在')
        return {**dict(row), 'revisions': json.loads(row['revisions'])}

    def list_contents(self):
        with self.connect() as db:
            legacy = [dict(row) for row in db.execute('''SELECT w.revision,w.title,w.status,w.created FROM workflows w
                JOIN recordings r ON r.id=json_extract(w.payload,'$.recording_id') WHERE r.parent_id=''
                AND r.id NOT IN (SELECT recording_id FROM recording_sessions)''')]
            sessions = db.execute('''SELECT r.*,s.plan_status,s.plan_reason FROM recordings r
                JOIN recording_sessions s ON s.recording_id=r.id WHERE r.deleted=0 ORDER BY r.started''').fetchall()
        for row in sessions:
            status = {'pending': 'analyzing', 'blocked': 'needs_attention', 'ready': 'offline_checked'}[row['plan_status']]
            if row['status'] == 'recording':
                status = 'recording'
            elif row['status'] in ('interrupted', 'error'):
                status = 'needs_attention'
            elif row['status'] == 'materials_deleted':
                status = 'materials_deleted'
            legacy.append({'revision': f'recording:{row["id"]}', 'title': row['name'] or '整次录制（等待分析命名）',
                           'status': status, 'created': row['started'], 'reason': row['plan_reason'] or row['reason']})
        return sorted(legacy, key=lambda item: item['created'], reverse=True)

    def execution_workflows(self, selections):
        from src.workflow.validator import WorkflowValidator
        workflows = []
        seen = set()
        for selection in selections:
            if selection.startswith('recording:'):
                identifier = selection.removeprefix('recording:')
                plan = self.session_plan(identifier)
                if self.recording(identifier)['deleted'] or plan['plan_status'] != 'ready':
                    raise ValueError(plan['plan_reason'] or '整次录制尚未完成分析和连续性检查')
                revisions = WorkflowValidator(self).session_revisions(identifier)
                if revisions != plan['revisions']:
                    raise ValueError('完整流程与已检查的执行顺序不符')
            else:
                workflow = self.workflow(selection)
                if self.recording(workflow.recording_id)['parent_id'] or self.is_session(workflow.recording_id):
                    raise ValueError('请选择整次录制记录执行')
                revisions = [selection]
            for revision in revisions:
                if revision not in seen:
                    workflows.append(self.workflow(revision))
                    seen.add(revision)
        return workflows

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
        segments = self.session_segments(identifier) if self.is_session(identifier) else []
        with self.connect() as db:
            environment = db.execute('SELECT game_version,language FROM recording_environment WHERE recording_id=?',
                                     (identifier,)).fetchone()
        exports = {
            'recording-details.json': ('记录信息', {**recording, 'environment': dict(environment) if environment else None}),
            'observations.json': ('画面与任务识别', [item.model_dump() for item in observations]),
            'events.json': ('键鼠与记录事件', events),
        }
        if self.is_session(identifier):
            exports['segments.json'] = ('内部片段顺序', segments)
            exports['execution-plan.json'] = ('完整执行顺序', self.session_plan(identifier))
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
        observation_count = len(observations)
        input_count = sum(event['kind'] == 'input' for event in events)
        for segment in segments:
            child = self.recording_materials(segment['id'])
            materials.extend(child['files'])
        materials = list({item['path']: item for item in materials}.values())
        return {
            'recording': recording, 'directory': directory, 'files': materials,
            'observations': observation_count, 'input_events': input_count, 'segments': segments,
        }

    def delete_recordings(self, identifiers, delete_materials=False):
        identifiers = set(identifiers)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for identifier in tuple(identifiers):
                identifiers.update(row['id'] for row in db.execute(
                    'SELECT id FROM recordings WHERE parent_id=?', (identifier,)))
            directories = {}
            for identifier in identifiers:
                row = db.execute('SELECT status,deleted FROM recordings WHERE id=?', (identifier,)).fetchone()
                if row is None or (row['deleted'] and not delete_materials) or row['status'] == 'recording':
                    raise ValueError('记录不存在或仍在录制，无法删除')
                if delete_materials:
                    directory = self.resolve(identifier)
                    if (directory.parent != self.root or directory.name != identifier
                            or (self.root / identifier).is_symlink() or (self.root / identifier).is_junction()):
                        raise ValueError('录制材料目录无效')
                    directories[identifier] = directory
            analysis_files = []
            if delete_materials:
                analysis_directory = self.resolve('agy')
                if (analysis_directory.parent != self.root or analysis_directory.name != 'agy'
                        or (self.root / 'agy').is_symlink() or (self.root / 'agy').is_junction()):
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
                                analysis_files.append((payload['recording_id'], path))
            for identifier in identifiers:
                result = db.execute("UPDATE recordings SET deleted=1 WHERE id=? AND status!='recording'",
                                    (identifier,))
                if result.rowcount != 1:
                    raise ValueError('记录不存在或仍在录制，无法删除')
                if delete_materials:
                    pending = db.execute('SELECT 1 FROM recording_material_cleanup WHERE recording_id=?',
                                         (identifier,)).fetchone()
                    status = db.execute('SELECT status FROM recordings WHERE id=?', (identifier,)).fetchone()['status']
                    if status == 'materials_deleted' or pending is not None:
                        continue
                    paths = [{'kind': 'recording', 'path': str(directories[identifier].relative_to(self.root))}]
                    paths.extend({'kind': 'analysis', 'path': str(path.relative_to(self.root))}
                                 for owner, path in analysis_files if owner == identifier)
                    db.execute('INSERT INTO recording_material_cleanup VALUES(?,?)',
                               (identifier, json.dumps(paths, ensure_ascii=False)))
                    db.execute("UPDATE recordings SET status='materials_deleting' WHERE id=?", (identifier,))
                    db.execute("UPDATE workflows SET status='materials_deleting' WHERE json_extract(payload,'$.recording_id')=?",
                               (identifier,))
        if delete_materials:
            self.cleanup_recording_materials(identifiers)

    def cleanup_recording_materials(self, identifiers=None):
        with self.connect() as db:
            pending = [dict(row) for row in db.execute('SELECT * FROM recording_material_cleanup ORDER BY recording_id')]
        for row in pending:
            identifier = row['recording_id']
            if identifiers is not None and identifier not in identifiers:
                continue
            for item in json.loads(row['paths']):
                original = self.root / item['path']
                path = self.resolve(item['path'])
                if original.is_symlink() or original.is_junction():
                    raise ValueError('待清理材料不能使用符号链接或目录连接')
                if item['kind'] == 'recording':
                    if path.parent != self.root or path.name != identifier:
                        raise ValueError('待清理录制材料目录无效')
                    if path.exists():
                        shutil.rmtree(path)
                elif item['kind'] == 'analysis':
                    analysis = self.root / 'agy'
                    if (path.parent != analysis or original.parent != analysis or analysis.is_symlink()
                            or analysis.is_junction() or not path.name.endswith(('.json', '.md', '.result.txt'))):
                        raise ValueError('待清理分析材料路径无效')
                    path.unlink(missing_ok=True)
                else:
                    raise ValueError('未知的材料清理类型')
            with self.connect() as db:
                db.execute('DELETE FROM observations WHERE recording_id=?', (identifier,))
                db.execute('DELETE FROM events WHERE recording_id=?', (identifier,))
                db.execute('DELETE FROM recording_environment WHERE recording_id=?', (identifier,))
                db.execute("UPDATE recordings SET status='materials_deleted' WHERE id=?", (identifier,))
                db.execute("UPDATE workflows SET status='materials_deleted' WHERE json_extract(payload,'$.recording_id')=?",
                           (identifier,))
                db.execute('DELETE FROM recording_material_cleanup WHERE recording_id=?', (identifier,))

    def analyzed(self, recording_id, reasons, no_action_needed=False):
        with self.connect() as db:
            db.execute('UPDATE recordings SET analyzed=1,reason=?,analysis_noop=? WHERE id=?',
                       ('；'.join(reasons), int(no_action_needed), recording_id))

    def save_workflow(self, workflow, status='compiled'):
        if status != 'compiled':
            raise ValueError('新流程必须经过独立验证')
        with self.connect() as db:
            row = db.execute('SELECT status FROM recordings WHERE id=?', (workflow.recording_id,)).fetchone()
            if row is None or row['status'] in ('materials_deleting', 'materials_deleted'):
                raise ValueError('流程所需的录制材料不存在')
            db.execute('INSERT OR IGNORE INTO workflows VALUES(?,?,?,?,?,?,?)',
                       (workflow.revision, workflow.content_id, workflow.period, workflow.title,
                        status, workflow.model_dump_json(), time.time()))

    def validate(self, revision, kind, evidence):
        if kind not in ('offline_checked', 'runtime_verified'):
            raise ValueError('未知验证状态')
        with self.connect() as db:
            row = db.execute('SELECT status FROM workflows WHERE revision=?', (revision,)).fetchone()
            if row is None or row['status'] in ('materials_deleting', 'materials_deleted') or (kind == 'runtime_verified' and row['status'] not in ('offline_checked', 'runtime_verified')):
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

    def list_workflows(self, recording_id=None):
        with self.connect() as db:
            query = 'SELECT revision,title,status,created FROM workflows'
            parameters = ()
            if recording_id is not None:
                query += " WHERE json_extract(payload,'$.recording_id')=?"
                parameters = (recording_id,)
            return [dict(row) for row in db.execute(query + ' ORDER BY created DESC', parameters)]

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
