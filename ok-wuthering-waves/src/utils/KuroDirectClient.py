import json
import math
import threading
import time
import uuid
from dataclasses import dataclass
from types import SimpleNamespace

import requests
from PySide6.QtCore import QObject, QMetaObject, QThread, QTimer, Qt, QUrl, QUrlQuery, Slot
from PySide6.QtWebSockets import QWebSocket

from src.utils.PositionSources import PositionSample


KURO_ACCOUNT_3 = '13452871892'
KURO_ROLE_3 = '112136276'
CREDENTIAL_TARGET = 'OK-WW/Kuro-position/account-3'


@dataclass(frozen=True)
class KuroCredential:
    token: str
    devcode: str


class KuroCredentialStore:
    def read(self):
        import pywintypes
        import win32cred

        try:
            credential = win32cred.CredRead(CREDENTIAL_TARGET, win32cred.CRED_TYPE_GENERIC)
        except pywintypes.error as error:
            if error.winerror == 1168:
                return None
            raise
        blob = credential['CredentialBlob']
        payload = json.loads(blob.decode('utf-16-le') if isinstance(blob, bytes) else blob)
        if payload['account'] != KURO_ACCOUNT_3:
            raise ValueError('库街区凭据关联的游戏账号不匹配')
        if not payload['token'] or not payload['devcode']:
            raise ValueError('库街区凭据内容不完整')
        return KuroCredential(payload['token'], payload['devcode'])

    def write(self, token):
        import win32cred

        token = token.strip()
        if not token:
            raise ValueError('库街区 token 不能为空')
        previous = self.read()
        devcode = previous.devcode if previous is not None else uuid.uuid4().hex
        payload = json.dumps({'account': KURO_ACCOUNT_3, 'token': token, 'devcode': devcode})
        win32cred.CredWrite({
            'Type': win32cred.CRED_TYPE_GENERIC,
            'TargetName': CREDENTIAL_TARGET,
            'UserName': KURO_ACCOUNT_3,
            'CredentialBlob': payload,
            'Persist': win32cred.CRED_PERSIST_LOCAL_MACHINE,
        })
        return KuroCredential(token, devcode)

    def delete(self):
        import win32cred

        if self.read() is not None:
            win32cred.CredDelete(CREDENTIAL_TARGET, win32cred.CRED_TYPE_GENERIC)


def parse_kuro_message(text, session_id, sequence, received_at):
    if text == 'ping':
        return None, None
    data = json.loads(text)
    if not isinstance(data, list) or len(data) != 5 or type(data[0]) is not int:
        raise ValueError('库街区坐标消息格式无效')
    code = data[0]
    if code != 200:
        return code, None
    if type(data[1]) is not int or type(data[2]) is not int:
        raise ValueError('库街区地图与区域编号必须为整数')
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in data[1:]):
        raise ValueError('库街区坐标字段必须为有限数值')
    sample = PositionSample('kuro', session_id, sequence, '', str(data[1]), data[2],
                            float(data[3]), float(data[4]), None, received_at, None)
    return code, sample


def verify_kuro_role(credential):
    response = requests.post(
        'https://api.kurobbs.com/map/core/gamer/role/getBindRoleInfo',
        headers={
            'token': credential.token,
            'source': 'android',
            'wiki_type': '10',
            'devcode': credential.devcode,
            'state_id': '8',
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        data={}, timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    if payload['code'] == 220:
        return '库街区 token 已失效，请重新设置'
    if payload['code'] != 200:
        return f'库街区角色核对失败（{payload["code"]}）'
    if str(payload['data']['roleId']) != KURO_ROLE_3:
        return '库街区 token 对应的游戏角色与账号 3 不匹配'
    return None


class _KuroWebSocketWorker(QObject):
    def __init__(self, credential, publish):
        super().__init__()
        self.credential = credential
        self.publish = publish
        self.running = True
        self.socket = None
        self.retry = None
        self.heartbeat = None
        self.session_id = ''
        self.sequence = 0
        self.server_status = ''

    @Slot()
    def start(self):
        self.socket = QWebSocket()
        self.socket.setParent(self)
        self.socket.connected.connect(self.on_connected)
        self.socket.disconnected.connect(self.on_disconnected)
        self.socket.textMessageReceived.connect(self.on_message)
        self.socket.errorOccurred.connect(self.on_error)
        self.retry = QTimer(self)
        self.retry.setSingleShot(True)
        self.retry.setInterval(5000)
        self.retry.timeout.connect(self.connect)
        self.heartbeat = QTimer(self)
        self.heartbeat.setInterval(10000)
        self.heartbeat.timeout.connect(self.send_heartbeat)
        self.connect()

    @Slot()
    def connect(self):
        if not self.running:
            return
        try:
            role_error = verify_kuro_role(self.credential)
        except (requests.ConnectionError, requests.Timeout):
            self.publish(False, None, None, '库街区角色核对请求失败，等待重新连接')
            self.retry.start()
            return
        if role_error is not None:
            self.running = False
            self.publish(False, None, None, role_error)
            self.thread().quit()
            return
        url = QUrl('wss://api.kurobbs.com/ws-map')
        query = QUrlQuery()
        query.addQueryItem('devcode', self.credential.devcode)
        query.addQueryItem('token', self.credential.token)
        query.addQueryItem('source', 'android')
        url.setQuery(query)
        self.publish(False, None, None, '正在连接库街区位置同步')
        self.socket.open(url)

    @Slot()
    def on_connected(self):
        self.session_id = uuid.uuid4().hex
        self.sequence = 0
        self.server_status = ''
        self.retry.setInterval(5000)
        self.publish(True, None, None, '等待库街区坐标')
        self.heartbeat.start()

    @Slot(str)
    def on_message(self, text):
        self.sequence += 1
        code, sample = parse_kuro_message(text, self.session_id, self.sequence, time.time())
        if code is None:
            return
        if code == 200:
            self.publish(True, sample, code, '已收到库街区坐标')
        elif code == 220:
            self.running = False
            self.heartbeat.stop()
            self.publish(False, None, code, '库街区 token 已失效，请重新设置')
            self.socket.close()
        else:
            reason = json.loads(text)[1]
            self.server_status = f'库街区位置不可用（{code}）：{reason}'
            if code in (2301, 2310):
                self.retry.setInterval(30000)
            self.publish(True, None, code, self.server_status)
            if code in (2301, 2310):
                self.socket.close()

    @Slot()
    def send_heartbeat(self):
        if self.running:
            self.socket.sendTextMessage('ping')

    @Slot()
    def on_disconnected(self):
        self.heartbeat.stop()
        if self.running:
            message = self.server_status or '库街区连接已断开'
            self.publish(False, None, None, f'{message}，等待重新连接')
            self.retry.start()

    @Slot()
    def on_error(self, error):
        if self.running:
            self.publish(False, None, None, f'库街区连接失败（{error.name}），等待重新连接')
            self.retry.start()

    @Slot()
    def stop(self):
        self.running = False
        self.retry.stop()
        self.heartbeat.stop()
        self.socket.abort()
        self.thread().quit()


class KuroDirectClient:
    direct = True

    def __init__(self, credential):
        self.lock = threading.RLock()
        self.connected = False
        self.latest = None
        self.code = None
        self.message = '正在连接库街区位置同步'
        self.thread = QThread()
        self.worker = _KuroWebSocketWorker(credential, self._publish)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.start)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.start()

    def _publish(self, connected, sample, code, message):
        with self.lock:
            self.connected = connected
            self.latest = sample
            self.code = code
            self.message = message

    def poll(self, now=None):
        with self.lock:
            return SimpleNamespace(connected=self.connected, latest=self.latest,
                                   code=self.code, message=self.message)

    def stop(self):
        if self.thread is None:
            return
        if self.thread.isRunning():
            QMetaObject.invokeMethod(self.worker, 'stop', Qt.QueuedConnection)
        if not self.thread.wait(12000):
            raise RuntimeError('库街区连接线程未能结束')
        self.thread = None
        self.worker = None
