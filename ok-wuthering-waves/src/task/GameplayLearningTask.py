import threading
import queue
import time
from pathlib import Path

from ok import TriggerTask
from ok.core.events import communicate

from src.workflow.compiler import AgyVisionProvider, WorkflowCompiler
from src.workflow.recorder import GameplayRecorderService, WindowsGameplaySource
from src.workflow.store import WorkflowStateStore
from src.workflow.validator import WorkflowValidator
from src.utils.ManualRouteRecorder import parse_hotkey_vk_codes


class GameplayLearningTask(TriggerTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = 'Gameplay Learning'
        self.description = 'Record normal gameplay and learn quest and event workflows with local AGY'
        self.default_config = {
            '_enabled': False,
            'Recording Toggle Hotkey': '<ctrl>+<shift>+<f8>',
            'Recording folder': 'working_images/gameplay',
            'Storage limit (GiB)': 20,
            'Automatic analysis': True,
            'Game version': '',
            'AGY companion': '',
            'Node executable': '',
        }
        self.config_description = {
            'Recording Toggle Hotkey': 'Global hotkey to start or stop recording. Clear to disable.',
            'Recording folder': 'Local recordings, screenshots, learned workflows and account progress.',
            'Automatic analysis': 'Use the installed AGY to analyze completed recordings automatically.',
            'Game version': 'Optional game version override. Empty uses the game executable version.',
            'AGY companion': 'Optional path to agy-companion.mjs. Empty discovers the installed plugin.',
            'Node executable': 'Optional Node.js 18 or newer executable path.',
        }
        self.config_type = {'Recording Toggle Hotkey': {'type': 'hotkey'}}
        self.trigger_interval = 1
        self.recorder = None
        self.store = None
        self.failure = None
        self.analysis_status = '等待完整记录'
        self._shutdown = threading.Event()
        self._supervisor = None
        self._analysis = None
        self._analyzing = False
        self.game_version = ''
        self._session_lock = threading.RLock()
        self._recording_session_id = None
        self._stopping_sessions = queue.SimpleQueue()

    def validate_config(self, key, value):
        if key == 'Recording Toggle Hotkey' and value.strip():
            codes = parse_hotkey_vk_codes(value)
            if codes is None or not any(code not in (0x10, 0x11, 0x12) for code in codes):
                return 'Use letters, numbers, F1–F24, Space, Tab or Enter with optional Ctrl, Alt or Shift.'

    def post_init(self):
        super().post_init()
        self.get_store()
        if self.enabled:
            self.enable()
        if self._supervisor is None:
            self._supervisor = threading.Thread(target=self._supervise, name='GameplayLearning', daemon=True)
            self._analysis = threading.Thread(target=self._analyze, name='GameplayAnalysis', daemon=True)
            self._supervisor.start()
            self._analysis.start()

    def enable(self):
        with self._session_lock:
            if self._recording_session_id is None:
                self._recording_session_id = self.get_store().start_session()
            self._enabled = True
            self.config['_enabled'] = True
        communicate.task.emit(self)

    def disable(self):
        with self._session_lock:
            super().disable()
            if self._recording_session_id is not None:
                self._stopping_sessions.put(self._recording_session_id)
                self._recording_session_id = None

    def _finish_stopped_sessions(self):
        while not self._stopping_sessions.empty():
            identifier = self._stopping_sessions.get_nowait()
            try:
                if self.recorder is not None and self.recorder.recording_session_id == identifier:
                    recorder, self.recorder = self.recorder, None
                    recorder.stop()
            finally:
                self.store.finish_session(identifier)

    def get_store(self):
        with self._session_lock:
            directory = Path(self.config.get('Recording folder')).resolve()
            if self.store is None or directory != self.store.root:
                if (self.recorder is not None or self._analyzing or self._recording_session_id is not None
                        or not self._stopping_sessions.empty()):
                    raise RuntimeError('运行期间不能修改记录目录')
                self.store = WorkflowStateStore(directory)
                self.store.recover()
            return self.store

    def _version(self, window):
        override = self.config.get('Game version').strip()
        if override:
            return override
        import psutil
        import win32api
        import win32process
        _, pid = win32process.GetWindowThreadProcessId(window.hwnd)
        executable = psutil.Process(pid).exe()
        version = win32api.GetFileVersionInfo(executable, '\\')
        parts = (version['FileVersionMS'] >> 16, version['FileVersionMS'] & 65535,
                 version['FileVersionLS'] >> 16, version['FileVersionLS'] & 65535)
        return '.'.join(map(str, parts))

    def _supervise(self):
        try:
            while not self._shutdown.is_set() and not self.executor.exit_event.is_set():
                self._finish_stopped_sessions()
                with self._session_lock:
                    active_session = self._recording_session_id
                replay_running = type(self.executor.current_task).__name__ == 'MultiAccountWorkflowTask'
                if not self.enabled or active_session is None or replay_running:
                    if self.recorder is not None:
                        if replay_running:
                            self.store.event(self.recorder.recording_session_id, time.monotonic(), 'capture_gap',
                                             {'reason': '录制期间开始执行其他内容'})
                        self.recorder.stop()
                        self.recorder = None
                else:
                    self.get_store()
                    window = self.executor.device_manager.hwnd_window
                    if window is not None and window.exists and window.hwnd:
                        if self.recorder is None:
                            project = Path(__file__).resolve().parents[2]
                            configuration = self.executor.config
                            self.game_version = self._version(window)
                            self.recorder = GameplayRecorderService(
                                self.store, project, configuration['template_matching'],
                                dict(configuration['ocr'].get('default', configuration['ocr']).get('params', {})),
                                self.executor.exit_event, {'game_version': self.game_version, 'language': str(self.executor.locale)})
                            self.recorder.start(WindowsGameplaySource(window), self.config.get('Storage limit (GiB)'),
                                                parent_id=active_session)
                        self.recorder.poll()
                        if self.recorder.error is not None:
                            raise self.recorder.error
                        self.info_set('Recording status', self.recorder.status)
                    elif self.recorder is not None:
                        self.store.event(self.recorder.recording_session_id, time.monotonic(), 'capture_gap',
                                         {'reason': '录制期间游戏窗口消失'})
                        self.recorder.stop()
                        self.recorder = None
                        self.info_set('Recording status', '等待游戏窗口')
                self._shutdown.wait(.5)
        except BaseException as error:
            self.failure = error
            self.info_set('Recording status', str(error))
            self.log_error('自动记录已经停止', error, notify=True)
            self.disable()
        finally:
            if self.recorder is not None:
                try:
                    self.recorder.stop()
                finally:
                    self.recorder = None
            self._finish_stopped_sessions()
            if self._recording_session_id is not None:
                self.store.event(self._recording_session_id, time.monotonic(), 'capture_gap',
                                 {'reason': '程序退出时录制尚未结束'})
                self.store.finish_session(self._recording_session_id)
                self._recording_session_id = None

    def _analyze(self):
        try:
            while not self._shutdown.is_set() and not self.executor.exit_event.is_set():
                if self.failure is not None:
                    return
                with self._session_lock:
                    store = self.store
                    analyze = store is not None and self.config.get('Automatic analysis')
                    self._analyzing = bool(analyze)
                if analyze:
                    for recording_id in store.pending_recordings():
                        if (self._shutdown.is_set() or self.executor.exit_event.is_set()
                                or not self.config.get('Automatic analysis')):
                            break
                        self.analysis_status = 'AGY 正在分析完整录制流程'
                        provider = AgyVisionProvider(
                            store.root / 'agy', self._shutdown,
                            self.config.get('Node executable'), self.config.get('AGY companion'))
                        workflows = WorkflowCompiler(store, provider).compile(
                            recording_id, **store.environment(recording_id))
                        self.analysis_status = f'分析完成，生成 {len(workflows)} 项流程'
                        self.info_set('Analysis status', self.analysis_status)
                    for identifier in store.pending_sessions():
                        if (self._shutdown.is_set() or self.executor.exit_event.is_set()
                                or not self.config.get('Automatic analysis')):
                            break
                        revisions, reason = WorkflowValidator(store).session_report(identifier)
                        store.save_session_plan(identifier, revisions, reason)
                        self.analysis_status = reason or f'完整录制分析完成，包含 {len(revisions)} 项连续内容'
                        self.info_set('Analysis status', self.analysis_status)
                self._analyzing = False
                self._shutdown.wait(1)
        except BaseException as error:
            self.analysis_status = str(error)
            self.info_set('Analysis status', str(error))
            self.log_error('AGY 自动分析已经停止', error, notify=True)
        finally:
            self._analyzing = False

    def run(self):
        if self.failure is not None:
            raise self.failure
        self.info_set('Analysis status', self.analysis_status)
        return False

    def on_destroy(self):
        self._shutdown.set()
        if self._supervisor is not None:
            self._supervisor.join(timeout=25)
        if self._analysis is not None:
            self._analysis.join(timeout=50)
        if self._supervisor is None:
            self._finish_stopped_sessions()
            if self._recording_session_id is not None:
                self.store.event(self._recording_session_id, time.monotonic(), 'capture_gap',
                                 {'reason': '程序退出时录制尚未结束'})
                self.store.finish_session(self._recording_session_id)
                self._recording_session_id = None
        super().on_destroy()
