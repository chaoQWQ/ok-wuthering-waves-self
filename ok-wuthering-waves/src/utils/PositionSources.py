import hashlib
import json
import math
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


SOURCE_MODES = ('Automatic', 'OCR only', 'Kuro only')
FRESH_SECONDS = 3.0
PAUSE_SECONDS = 8.0
EXPIRE_SECONDS = 12.0


@dataclass(frozen=True)
class PositionSample:
    source: str
    session_id: str
    sequence: int
    account_id: str
    map_id: str | None
    area_id: int | None
    x: float
    y: float
    z: float | None
    received_at: float
    observed_at: float | None

    @property
    def overlay_position(self):
        xy = (self.x / 100.0, self.y / 100.0)
        return xy if self.z is None else (*xy, self.z / 100.0)


@dataclass(frozen=True)
class PositionDecision:
    sample: PositionSample | None
    status: str
    reset_guidance: bool = False


class JsonlReader:
    def __init__(self, path):
        self.path = Path(path)
        self.offset = 0
        self.identity = None

    def read_new(self):
        stat = self.path.stat()
        identity = (stat.st_dev, stat.st_ino, stat.st_ctime_ns)
        replaced = self.identity is not None and (identity != self.identity or stat.st_size < self.offset)
        if replaced:
            self.offset = 0
        self.identity = identity
        records = []
        with self.path.open('rb') as source:
            source.seek(self.offset)
            while line := source.readline():
                # 写入中的 JSONL 行等待下一次读取，完整记录交给 json 解析。
                if not line.endswith(b'\n'):
                    break
                records.append((source.tell(), json.loads(line)))
                self.offset = source.tell()
        return records, replaced


class KuroCaptureReader:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.messages = JsonlReader(self.directory / 'kuro-coordinate-messages.jsonl')
        self.events = JsonlReader(self.directory / 'kuro-capture-events.jsonl')
        self.pending = []
        self.session_id = ''
        self.connected = False
        self.ambiguous = False
        self.connections = 0
        self.sequence = 0
        self.latest = None
        self.code = None
        self.message = '等待库街区位置同步连接'
        self.last_event_at = 0.0
        self.generation = uuid.uuid4().hex

    def poll(self, now=None):
        now = time.time() if now is None else now
        if not self.events.path.exists() or not self.messages.path.exists():
            self.connected = False
            self.latest = None
            self.message = '接收目录缺少坐标消息或连接记录'
            return self
        events, events_replaced = self.events.read_new()
        messages, messages_replaced = self.messages.read_new()
        if events_replaced or messages_replaced:
            self.generation = uuid.uuid4().hex
            self.connected = False
            self.latest = None
            self.session_id = ''
            self.connections = 0
            self.ambiguous = False
            self.pending.clear()
            self.last_event_at = 0.0
        for offset, event in events:
            kind = event['event']
            if kind not in ('websocket_start', 'websocket_end', 'connection_error'):
                continue
            if kind != 'websocket_end' and event.get('path') != '/ws-map':
                continue
            timestamp = datetime.fromisoformat(event['time']).timestamp()
            self.pending.append((timestamp, 0, offset, kind, event))
        for offset, message in messages:
            if message['event'] != 'server_message' or message['text'] == 'ping':
                continue
            timestamp = float(message['timestamp'])
            if not math.isfinite(timestamp):
                raise ValueError('库街区消息时间无效')
            self.pending.append((timestamp, 1, offset, 'position', json.loads(message['text'])))
        self.pending.sort(key=lambda record: record[:3])
        remaining = []
        for timestamp, _, offset, kind, data in self.pending:
            if timestamp > now:
                remaining.append((timestamp, 0 if kind != 'position' else 1, offset, kind, data))
                continue
            if timestamp < self.last_event_at:
                continue
            self.last_event_at = timestamp
            if kind == 'websocket_start':
                self.connections += 1
                self.ambiguous = self.ambiguous or self.connections > 1
                self.connected = not self.ambiguous
                identity = f'{self.generation}|{self.directory}|{self.events.identity}|{offset}|{timestamp}'
                self.session_id = hashlib.sha256(identity.encode()).hexdigest()
                self.latest = None
                self.sequence = 0
                self.code = None
                self.message = '检测到多个同步连接，请关闭位置同步后重新开启' if self.ambiguous else '等待坐标消息'
            elif kind in ('websocket_end', 'connection_error'):
                self.connections = max(0, self.connections - 1)
                self.connected = False
                self.latest = None
                self.message = '库街区位置同步连接已断开'
                if not self.connections:
                    self.ambiguous = False
            elif kind == 'position' and self.connected:
                if not isinstance(data, list) or len(data) != 5 or type(data[0]) is not int:
                    raise ValueError('库街区坐标消息格式无效')
                self.sequence += 1
                self.code = data[0]
                if self.code != 200:
                    self.latest = None
                    self.message = f'库街区位置不可用（{self.code}）：{data[1]}'
                    continue
                if any(type(value) not in (int, float) or not math.isfinite(value) for value in data[1:]):
                    raise ValueError('库街区坐标字段必须为有限数值')
                if type(data[1]) is not int or type(data[2]) is not int:
                    raise ValueError('库街区地图与区域编号必须为整数')
                self.latest = PositionSample('kuro', self.session_id, self.sequence, '',
                                             str(data[1]), data[2], float(data[3]), float(data[4]),
                                             None, timestamp, None)
                self.message = '已收到库街区坐标'
        self.pending = remaining
        return self


class PositionSources:
    def __init__(self):
        self.context = None
        self.mode = 'Automatic'
        self.account = ''
        self.reader = None
        self.binding = None
        self.history = deque(maxlen=400)
        self.ocr_sequence = 0
        self.ocr_recovery = 0
        self.recovery_sample = None
        self.last_sample = None
        self.last_kuro = None
        self.pending_jump = None
        self.checked_key = None
        self.kuro_allowed = False
        self.kuro_conflict = False
        self.transition_at = 0.0
        self.decision = PositionDecision(None, '等待坐标')

    def configure(self, mode, directory, account):
        if mode not in SOURCE_MODES:
            raise ValueError(f'未知坐标来源：{mode}')
        context = (str(directory).strip(), str(account))
        if context != self.context:
            self.__init__()
            self.context = context
            self.account = str(account)
            if context[0]:
                self.reader = KuroCaptureReader(context[0])
        if self.mode != mode:
            self.last_sample = None
            self.ocr_recovery = 0
            self.decision = PositionDecision(None, '坐标来源已切换', True)
        self.mode = mode

    def bind(self, session_id, now=None):
        now = time.time() if now is None else now
        if self.reader is None:
            raise ValueError('请设置库街区接收目录')
        self.reader.poll(now)
        sample = self.reader.latest
        if (not self.reader.connected or sample is None or sample.session_id != session_id
                or not 0 <= now - sample.received_at <= PAUSE_SECONDS):
            raise ValueError('同步连接或坐标已经变化，请重新核对账号')
        self.binding = (self.account, session_id)
        self.last_kuro = None
        self.pending_jump = None
        self.checked_key = None
        self.kuro_conflict = False

    def unbind(self):
        self.binding = None
        self.last_sample = None
        self.decision = PositionDecision(None, '库街区账号绑定已解除', True)

    def kuro_status(self, now):
        if self.reader is None:
            return None, '未设置库街区接收目录'
        reader = self.reader.poll(now)
        if not reader.connected or reader.latest is None:
            return None, reader.message
        sample = reader.latest
        age = now - sample.received_at
        if age < 0:
            return None, '接收时间异常，请检查系统时间'
        if age > EXPIRE_SECONDS:
            return None, f'库街区坐标已过期（{age:.1f} 秒未更新）'
        if age > PAUSE_SECONDS:
            return None, f'库街区导航暂停（{age:.1f} 秒未更新）'
        if self.binding != (self.account, sample.session_id):
            return None, '请核对并绑定当前库街区角色与收集账号'
        label = '库街区同步（更新较慢）' if age > FRESH_SECONDS else '库街区同步'
        return sample, f'{label} · {age:.1f} 秒前接收 · 实际延迟未知'

    def select(self, ocr_position=None, ocr_map_id=None, known_maps=(), now=None):
        now = time.time() if now is None else now
        ocr = None
        if ocr_position is not None:
            self.ocr_sequence += 1
            ocr = PositionSample('ocr', 'screen', self.ocr_sequence, self.account,
                                 None if ocr_map_id is None else str(ocr_map_id), None,
                                 ocr_position[0] * 100.0, ocr_position[1] * 100.0,
                                 ocr_position[2] * 100.0 if len(ocr_position) > 2 else None, now, now)
            if self.history:
                previous = self.history[-1]
                if ((previous.map_id is not None and ocr.map_id is not None and previous.map_id != ocr.map_id)
                        or math.hypot(previous.x - ocr.x, previous.y - ocr.y) > 50000):
                    self.transition_at = now
                    self.history.clear()
            self.history.append(ocr)
        while self.history and now - self.history[0].received_at > 20:
            self.history.popleft()
        kuro, status = (None, '') if self.mode == 'OCR only' else self.kuro_status(now)
        if kuro is not None and kuro.map_id not in {str(value) for value in known_maps}:
            kuro, status = None, '库街区地图尚无对应资源，请下载地图资源'
        if kuro is not None:
            key = (kuro.session_id, kuro.sequence)
            comparable = [p for p in self.history if p.map_id == kuro.map_id]
            recent_map = next((p.map_id for p in reversed(self.history) if p.map_id is not None), None)
            self.kuro_conflict = (kuro.received_at <= self.transition_at or
                (recent_map is not None and recent_map != kuro.map_id) or
                (bool(comparable) and min(math.hypot(p.x - kuro.x, p.y - kuro.y)
                                         for p in comparable) > 5000))
            if key != self.checked_key:
                self.checked_key = key
                self.kuro_allowed = True
                if self.last_kuro is not None and (kuro.map_id != self.last_kuro.map_id or
                        math.hypot(kuro.x - self.last_kuro.x, kuro.y - self.last_kuro.y) > 50000):
                    pending = self.pending_jump
                    self.kuro_allowed = (pending is not None and pending.map_id == kuro.map_id
                                         and math.hypot(kuro.x - pending.x, kuro.y - pending.y) <= 10000)
                    self.pending_jump = None if self.kuro_allowed else kuro
                if self.kuro_allowed and not self.kuro_conflict:
                    self.last_kuro = kuro
            if self.kuro_conflict:
                kuro, status = None, '库街区位置与近期截图不一致，等待核对'
            elif not self.kuro_allowed:
                kuro, status = None, '位置变化较大，等待下一条同步消息确认'
        selected = None
        if self.mode != 'Kuro only' and ocr is not None:
            previous_ocr = self.recovery_sample
            if previous_ocr is not None and (ocr.map_id != previous_ocr.map_id or
                    now - previous_ocr.received_at > 1.0 or
                    math.hypot(ocr.x - previous_ocr.x, ocr.y - previous_ocr.y) >
                    300 + 10000 * max(0, now - previous_ocr.received_at)):
                self.ocr_recovery = 0
            self.recovery_sample = ocr
            self.ocr_recovery += 1
            if self.last_sample is None or self.last_sample.source == 'ocr' or self.ocr_recovery >= 3:
                selected = ocr
                status = '截图 OCR' + (f' · {status}' if self.kuro_conflict else '')
        else:
            self.ocr_recovery = 0
            self.recovery_sample = None
        if selected is None and self.mode != 'OCR only' and kuro is not None:
            selected = PositionSample(**{**kuro.__dict__, 'account_id': self.account})
        if selected is None and self.mode == 'OCR only':
            status = '截图未识别到坐标'
        previous = self.last_sample
        reset = selected is not None and (previous is None or
            (previous.source, previous.map_id, previous.account_id) !=
            (selected.source, selected.map_id, selected.account_id) or
            math.hypot(selected.x - previous.x, selected.y - previous.y) > 50000)
        if selected is not None:
            self.last_sample = selected
        self.decision = PositionDecision(selected, status, reset)
        return self.decision
