import hashlib
import multiprocessing
import queue
import shutil
import threading
import time
import traceback
import uuid
from collections import Counter

import cv2

from src.utils.QuestOcrPrivacy import prepare_quest_ocr_frame
from src.workflow.observer import IdleTracker, SceneObserver, protect_observation_frame
from src.workflow.store import WorkflowStateStore
from src.workflow.video import SegmentedVideoWriter
from src.workflow.diagnostics import RecordingDiagnostics, publish_diagnostic


def persist_inputs(store, recording_id, inputs, observations):
    # 操作信息仅在完整区间的场景分类结束后保存。
    ordered = sorted(observations, key=lambda item: item.timestamp)
    index = 0
    accepted = []
    rejected = Counter()
    for timestamp, payload in sorted(inputs, key=lambda item: item[0]):
        while index + 1 < len(ordered) and ordered[index + 1].timestamp < timestamp:
            index += 1
        nearby = ordered[max(0, index - 2):index + 4]
        before = [item for item in nearby if item.timestamp <= timestamp]
        after = [item for item in nearby if item.timestamp >= timestamp]
        if not before or not after:
            rejected['missing_observation'] += 1
            continue
        if after[0].timestamp - before[-1].timestamp > 2:
            rejected['observation_interval'] += 1
            continue
        if any(item.combat or item.gap or item.scene not in ('world', 'dialog', 'menu', 'result')
               for item in (before[-1], after[0])):
            rejected['excluded_scene'] += 1
            continue
        if before[-1].foreground and after[0].foreground:
            accepted.append((timestamp, 'input', payload))
        else:
            rejected['background'] += 1
    store.events_batch(recording_id, accepted)
    return {'received': len(inputs), 'saved': len(accepted), 'excluded': dict(rejected)}


def recording_worker(root, project_root, matching, ocr_params, frames, frame_consumed, replies, max_bytes,
                     environment, target_hwnd, session_id):
    from pathlib import Path
    from src.workflow.input_capture import WindowsInputCapture
    store = WorkflowStateStore(root)
    diagnostics = RecordingDiagnostics(root, session_id, 'worker',
                                       publish=lambda entry: replies.put(('diagnostic', entry)))
    diagnostics.write('worker_start', '录制处理进程开始初始化', hwnd=target_hwnd, ocr_params=ocr_params)
    recording_id = None
    writer = None
    inputs, observations = [], []
    pending_frames = []
    idle = IdleTracker()
    observation_at = 0.0
    idle_state = False
    keyframe_count = 0
    last_saved_idle = 0.0
    used_bytes = sum(path.stat().st_size for path in store.root.rglob('*') if path.is_file())
    capture = WindowsInputCapture(target_hwnd)
    boundary = None
    boundary_input = None
    boundary_frame = None
    previous_scene = None
    last_slow_log = 0.0

    def save_keyframe(observation, picture):
        nonlocal used_bytes, keyframe_count
        image_path = f'{recording_id}/{observation.frame_id}.png'
        success, encoded = cv2.imencode('.png', protect_observation_frame(picture, observation))
        if not success:
            raise RuntimeError('游戏关键画面编码失败')
        data = encoded.tobytes()
        store.resolve(image_path).write_bytes(data)
        observation.screenshot = image_path
        observation.image_sha256 = hashlib.sha256(data).hexdigest()
        used_bytes += len(data)
        keyframe_count += 1

    def finish(status, reason='', trigger=''):
        nonlocal recording_id, writer, inputs, observations, pending_frames, boundary, boundary_input
        if writer is not None:
            writer.close()
        if recording_id is not None:
            if status == 'complete':
                input_statistics = persist_inputs(store, recording_id, inputs, observations)
                boundary = observations[-1] if observations else None
                boundary_input = inputs[-1][1] if inputs else boundary_input
            else:
                input_statistics = {'received': len(inputs), 'saved': 0,
                                    'excluded': {'interrupted_recording': len(inputs)}}
                boundary, boundary_input = None, None
            intervals = [after.timestamp - before.timestamp for before, after in zip(observations, observations[1:])]
            statistics = {
                'recording_id': recording_id, 'status': status, 'trigger': trigger, 'reason': reason,
                'observations': len(observations), 'keyframes': keyframe_count,
                'scenes': dict(Counter(item.scene for item in observations)),
                'unknown_or_gap': sum(item.gap for item in observations),
                'maximum_observation_interval': max(intervals, default=0),
                'intervals_over_two_seconds': sum(value > 2 for value in intervals),
                'inputs': input_statistics,
                'video_frames': writer.frames_written if writer is not None else 0,
            }
            diagnostics.summary(store.resolve(recording_id), **statistics)
            diagnostics.write('segment_finished', '游玩片段保存结束', **statistics)
            store.finish_recording(recording_id, status, reason)
            replies.put(('finished', recording_id))
        recording_id, writer = None, None
        inputs, observations, pending_frames = [], [], []

    try:
        initialization_started = time.monotonic()
        observer = SceneObserver(Path(project_root), matching, ocr_params)
        capture.start()
        diagnostics.write('worker_ready', '识别服务及键鼠记录已经就绪',
                          initialization_seconds=time.monotonic() - initialization_started)
        replies.put(('ready', '等待游戏画面'))
        while True:
            item = frames.get()
            frame_consumed.set()
            if item is None:
                capture.enabled.clear()
                if recording_id is not None and pending_frames:
                    final_time, final_frame = pending_frames[-1]
                    final = observer.observe(final_frame, final_time, foreground)
                    if final.scene in ('login', 'unknown'):
                        final.gap = True
                    else:
                        save_keyframe(final, final_frame)
                        for frame_time, picture in pending_frames:
                            writer.write(protect_observation_frame(picture, final), frame_time)
                    store.observe(recording_id, final)
                    observations.append(final)
                finish('complete', trigger='stop_requested')
                return
            if item[0] == 'gap':
                capture.enabled.clear()
                diagnostics.write('capture_gap', '处理进程收到画面采集中断通知', level='warning',
                                  recording_id=recording_id, reason=item[1],
                                  capture_details=item[2] if len(item) > 2 else None)
                finish('interrupted', item[1], trigger='capture_gap')
                boundary_frame = None
                replies.put(('status', item[1]))
                idle = IdleTracker()
                continue
            _, timestamp, frame, foreground = item
            pending_frames.append((timestamp, frame))
            inputs.extend(capture.drain(timestamp))
            input_active = capture.active(timestamp)
            if len(inputs) > 500_000:
                raise RuntimeError('操作记录达到容量限制，录制已经停止')
            if timestamp < observation_at:
                continue
            observation_at = timestamp + .5
            observation_started = time.monotonic()
            observation = observer.observe(frame, timestamp, foreground)
            observation_seconds = time.monotonic() - observation_started
            queue_age = observation_started - timestamp
            if (observation_seconds >= 1 or queue_age >= 1) and observation_started - last_slow_log >= 10:
                diagnostics.write('processing_slow', '画面识别耗时或处理等待时间较长', level='warning',
                                  recording_id=recording_id, frame_id=observation.frame_id,
                                  observation_seconds=observation_seconds, queue_age_seconds=queue_age,
                                  pending_video_frames=len(pending_frames), buffered_inputs=len(inputs))
                last_slow_log = observation_started
            scene_state = observation.scene, observation.combat, foreground
            if scene_state != previous_scene:
                diagnostics.write('scene_changed', '观察到场景或前台状态变化', recording_id=recording_id,
                                  frame_id=observation.frame_id, capture_timestamp=timestamp,
                                  scene=observation.scene, combat=observation.combat, foreground=foreground)
                previous_scene = scene_state
            if foreground and not observation.combat and observation.scene in ('world', 'dialog', 'menu', 'result'):
                capture.enabled.set()
            else:
                capture.enabled.clear()
            replies.put(('observation', observation.model_dump()))
            if observation.scene in ('login', 'unknown'):
                boundary, boundary_input, boundary_frame = None, None, None
                pending_frames.clear()
                if recording_id is not None:
                    store.event(recording_id, timestamp, 'excluded_scene', {'scene': observation.scene})
                    observation.gap = True
                    store.observe(recording_id, observation)
                    observations.append(observation)
                continue
            if observation.scene == 'loading':
                if recording_id is not None:
                    store.observe(recording_id, observation)
                    observations.append(observation)
                    for frame_time, picture in pending_frames:
                        writer.write(prepare_quest_ocr_frame(picture), frame_time)
                pending_frames.clear()
                continue
            is_idle = idle.observe(observation, input_active)
            if idle_state and not is_idle:
                idle_state = False
            if is_idle and not idle_state:
                if recording_id is not None:
                    store.event(recording_id, timestamp, 'idle_started', {'since': idle.since})
                finish('complete', trigger='idle')
                idle_state = True
            if idle_state:
                # 保留变化检测；定期截图不生成需要重复等待的流程。
                if timestamp - last_saved_idle >= 30:
                    replies.put(('status', '角色停留中，继续观察任务变化'))
                    last_saved_idle = timestamp
                boundary = observation
                boundary_frame = frame.copy()
                if inputs:
                    boundary_input = inputs[-1][1]
                inputs.clear()
                pending_frames.clear()
                continue
            if recording_id is None:
                recording_id = store.start_recording()
                store.set_environment(recording_id, environment['game_version'], environment['language'])
                writer = SegmentedVideoWriter(store.resolve(recording_id))
                keyframe_count = 0
                diagnostics.summary(store.resolve(recording_id), recording_id=recording_id, status='recording')
                diagnostics.write('segment_started', '开始保存游玩片段', recording_id=recording_id,
                                  capture_timestamp=timestamp, frame_size=[frame.shape[1], frame.shape[0]])
                replies.put(('started', recording_id))
                if boundary is not None and timestamp - boundary.timestamp <= 2 and not boundary.gap:
                    previous = boundary.model_copy(update={'frame_id': uuid.uuid4().hex})
                    if boundary_frame is not None:
                        save_keyframe(previous, boundary_frame)
                    store.observe(recording_id, previous)
                    observations.append(previous)
                    if boundary_input is not None:
                        initial = {key: value for key, value in boundary_input.items()
                                   if key not in ('mouse', 'key_event')}
                        initial['baseline'] = True
                        inputs.insert(0, (previous.timestamp, initial))
                    boundary = None
                boundary_frame = None
                replies.put(('status', '正在自动记录游玩过程'))
            if used_bytes > max_bytes or shutil.disk_usage(store.root).free < 512 * 1024 * 1024:
                raise OSError('记录容量或磁盘可用空间达到限制')
            if observation.changed or not observations or timestamp - observations[-1].timestamp >= 2:
                save_keyframe(observation, frame)
            store.observe(recording_id, observation)
            observations.append(observation)
            for frame_time, picture in pending_frames:
                writer.write(protect_observation_frame(picture, observation), frame_time)
            pending_frames.clear()
            if keyframe_count >= 48:
                finish('complete', trigger='keyframe_limit')
            if len(observations) % 120 == 0:
                used_bytes = sum(path.stat().st_size for path in store.root.rglob('*') if path.is_file())
    except BaseException:
        diagnostics.write('worker_error', '录制处理进程异常终止', level='error',
                          recording_id=recording_id, traceback=traceback.format_exc())
        finish('error', '录制进程异常终止，证据存在缺口', trigger='worker_error')
        replies.put(('error', traceback.format_exc()))
        raise
    finally:
        capture.close()


class WindowsGameplaySource:
    def __init__(self, hwnd_window):
        from ok.device.capture import WindowsGraphicsCaptureMethod
        self.window = hwnd_window
        self.diagnostic_sink = None
        self.pending_callback_errors = []
        self.callback_lock = threading.Lock()
        source = self

        class RecordingGraphicsCapture(WindowsGraphicsCaptureMethod):
            def frame_arrived_callback(self, *args):
                try:
                    return super().frame_arrived_callback(*args)
                except BaseException:
                    source.callback_error(traceback.format_exc())
                    raise

        self.capture = RecordingGraphicsCapture(hwnd_window)

    def set_diagnostics(self, diagnostics):
        with self.callback_lock:
            self.diagnostic_sink = diagnostics
            for error in self.pending_callback_errors:
                diagnostics.write('backend_callback_error', 'WGC 图像回调异常', level='error', traceback=error)
            self.pending_callback_errors.clear()

    def callback_error(self, error):
        with self.callback_lock:
            if self.diagnostic_sink is None:
                self.pending_callback_errors.append(error)
            else:
                self.diagnostic_sink.write('backend_callback_error', 'WGC 图像回调异常',
                                           level='error', traceback=error)

    def frame(self):
        return self.capture.get_frame()

    def foreground(self):
        import win32gui
        return win32gui.GetForegroundWindow() == self.window.hwnd

    def diagnostics(self):
        import win32gui
        hwnd = int(self.window.hwnd)
        exists = bool(win32gui.IsWindow(hwnd))
        capture = self.capture
        return {
            'hwnd': hwnd, 'window_exists': exists,
            'window_visible': bool(win32gui.IsWindowVisible(hwnd)) if exists else False,
            'window_minimized': bool(win32gui.IsIconic(hwnd)) if exists else False,
            'foreground_hwnd': int(win32gui.GetForegroundWindow()),
            'client_size': list(win32gui.GetClientRect(hwnd)[2:]) if exists else None,
            'capture_hwnd': int(capture.capture_hwnd), 'frame_pool_active': capture.frame_pool is not None,
            'backend_frame_age_seconds': time.time() - capture.last_frame_time,
            'frame_requested': capture.frame_requested.is_set(),
            'capture_cancel_generation': capture._frame_cancel_generation,
        }

    def close(self):
        self.capture.close()


class GameplayRecorderService:
    def __init__(self, store, project_root, matching, ocr_params, exit_event, environment):
        self.store = store
        self.project_root = project_root
        self.matching = matching
        self.ocr_params = ocr_params
        self.exit_event = exit_event
        self.environment = environment
        self.stop_event = threading.Event()
        self.capture_thread = None
        self.process = None
        self.status = '未启用'
        self.error = None
        self.latest = None
        self.captured_frames = 0
        self.observation_count = 0
        self.recording_id = None

    def start(self, source, max_gib=20):
        if self.process is not None:
            raise RuntimeError('录制服务已经启动')
        self.source = source
        self.error = None
        self.latest = None
        self.captured_frames = 0
        self.observation_count = 0
        self.recording_id = None
        self.stop_event.clear()
        self.session_id = uuid.uuid4().hex
        self.diagnostics = RecordingDiagnostics(self.store.root, self.session_id, 'capture')
        source.set_diagnostics(self.diagnostics)
        self.diagnostics.write('session_start', '录制采集服务启动', window=source.diagnostics(),
                               max_gib=max_gib)
        context = multiprocessing.get_context('spawn')
        self.frames = context.Queue(maxsize=2)
        self.frame_consumed = context.Event()
        self.frame_consumed.set()
        self.replies = context.Queue(maxsize=128)
        self.process = context.Process(target=recording_worker,
                                       args=(str(self.store.root), str(self.project_root), self.matching,
                                             self.ocr_params, self.frames, self.frame_consumed, self.replies,
                                             int(max_gib * 1024 ** 3),
                                             self.environment, int(source.window.hwnd), self.session_id),
                                       name='GameplayRecording', daemon=True)
        self.process.start()
        self.capture_thread = threading.Thread(target=self._capture, name='GameplayCapture', daemon=True)
        self.capture_thread.start()
        self.status = '正在初始化识别服务'

    def _capture(self):
        try:
            # 等待识别模型初始化完成，防止启动时采集队列超过容量。
            while not self.stop_event.is_set() and not self.exit_event.is_set():
                self.poll()
                if self.error is not None:
                    raise self.error
                if self.status == '等待游戏画面':
                    break
                self.stop_event.wait(.1)
            missing = False
            missing_started = None
            last_frame = None
            last_state = None
            last_heartbeat = time.monotonic()
            waiting_since = time.monotonic()
            while not self.stop_event.is_set() and not self.exit_event.is_set():
                started = time.monotonic()
                self.poll()
                if self.error is not None:
                    raise self.error
                if not self.process.is_alive():
                    raise RuntimeError('录制进程已经终止')
                # 处理进程取出前一张截图后才继续采集，保留实际采集时间。
                if not self.frame_consumed.wait(.1):
                    if time.monotonic() - waiting_since >= 30:
                        raise TimeoutError('游戏画面的识别与保存连续 30 秒没有响应，录制已经停止')
                    continue
                request_started = time.monotonic()
                picture = self.source.frame()
                received = time.monotonic()
                if picture is None:
                    if not missing:
                        missing_started = request_started
                        details = {
                            'window': self.source.diagnostics(), 'frame_request_seconds': received - request_started,
                            'last_success_age_seconds': received - last_frame if last_frame is not None else None,
                            'captured_frames': self.captured_frames, 'observation_count': self.observation_count,
                        }
                        self.diagnostics.write('capture_missing', '未取得新画面，当前片段中断', level='warning',
                                               recording_id=self.recording_id, **details)
                        self.frame_consumed.clear()
                        self.frames.put(('gap', '无法取得游戏画面，当前记录已中断', details), timeout=1)
                        waiting_since = time.monotonic()
                    missing = True
                    self.stop_event.wait(.5)
                    continue
                if missing:
                    self.diagnostics.write('capture_recovered', '画面采集已经恢复', recording_id=self.recording_id,
                                           missing_seconds=received - missing_started, window=self.source.diagnostics())
                missing = False
                last_frame = received
                self.captured_frames += 1
                height, width = picture.shape[:2]
                foreground = self.source.foreground()
                state = foreground, width, height
                if state != last_state:
                    self.diagnostics.write('capture_state', '采集窗口状态或画面尺寸变化',
                                           recording_id=self.recording_id, frame_size=[width, height],
                                           window=self.source.diagnostics())
                    last_state = state
                if received - last_heartbeat >= 30:
                    self.diagnostics.write('capture_heartbeat', '录制采集仍在运行', recording_id=self.recording_id,
                                           captured_frames=self.captured_frames, observations=self.observation_count,
                                           frame_request_seconds=received - request_started,
                                           window=self.source.diagnostics())
                    last_heartbeat = received
                if height > 1080:
                    picture = cv2.resize(picture, (round(width * 1080 / height), 1080), interpolation=cv2.INTER_AREA)
                self.frame_consumed.clear()
                self.frames.put(('frame', received, picture, foreground), timeout=1)
                waiting_since = time.monotonic()
                self.stop_event.wait(max(0, .05 - (time.monotonic() - started)))
        except BaseException as error:
            self.diagnostics.write('capture_error', '录制采集线程异常终止', level='error',
                                   recording_id=self.recording_id, captured_frames=self.captured_frames,
                                   traceback=traceback.format_exc())
            self.error = error
            self.status = f'录制停止：{error}'
            self.stop_event.set()
        finally:
            self.source.close()

    def poll(self):
        if self.process is None:
            return
        while True:
            try:
                kind, payload = self.replies.get_nowait()
            except queue.Empty:
                break
            if kind == 'observation':
                self.latest = payload
                self.observation_count += 1
            elif kind == 'diagnostic':
                publish_diagnostic(payload)
            elif kind == 'started':
                self.recording_id = payload
            elif kind == 'finished':
                if self.recording_id == payload:
                    self.recording_id = None
            elif kind in ('ready', 'status'):
                self.status = payload
            elif kind == 'error':
                self.error = RuntimeError(payload)
                self.status = '录制进程异常，已经停止'

    def stop(self):
        if self.process is not None:
            self.diagnostics.write('stop_requested', '请求结束录制采集', recording_id=self.recording_id,
                                   captured_frames=self.captured_frames, observations=self.observation_count)
        self.stop_event.set()
        if self.capture_thread is not None:
            self.capture_thread.join(timeout=6)
            if self.capture_thread.is_alive():
                self.diagnostics.write('capture_stop_timeout', '录制采集线程未能正常结束', level='error',
                                       recording_id=self.recording_id)
                raise RuntimeError('录制采集线程未能结束')
        if self.process is not None:
            if self.process.is_alive():
                if self.error is not None:
                    self.frames.put(('gap', f'采集异常：{self.error}'), timeout=5)
                self.frames.put(None, timeout=5)
                self.process.join(timeout=15)
            self.poll()
            if self.process.is_alive():
                self.diagnostics.write('worker_stop_timeout', '录制处理进程未能正常结束', level='error',
                                       recording_id=self.recording_id)
                self.process.terminate()
                self.process.join(timeout=5)
                self.store.recover()
                raise RuntimeError('录制进程未能正常结束，当前记录标记为中断')
            self.frames.cancel_join_thread()
            self.replies.cancel_join_thread()
            self.frames.close()
            self.replies.close()
            self.diagnostics.write('session_stopped', '录制采集服务已经结束',
                                   worker_exitcode=self.process.exitcode, captured_frames=self.captured_frames,
                                   observations=self.observation_count, error=str(self.error) if self.error else None)
        self.process = None
        self.capture_thread = None
        if self.error is not None:
            raise self.error
        self.status = '未启用'
