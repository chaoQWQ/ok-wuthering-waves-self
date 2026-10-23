import hashlib
import multiprocessing
import queue
import shutil
import threading
import time
import traceback
from collections import deque

import cv2

from src.utils.QuestOcrPrivacy import prepare_quest_ocr_frame
from src.workflow.observer import IdleTracker, SceneObserver, protect_observation_frame
from src.workflow.store import WorkflowStateStore
from src.workflow.video import SegmentedVideoWriter


def persist_inputs(store, recording_id, inputs, observations):
    # 操作信息仅在完整区间的场景分类结束后保存。
    ordered = sorted(observations, key=lambda item: item.timestamp)
    index = 0
    for timestamp, payload in sorted(inputs, key=lambda item: item[0]):
        while index + 1 < len(ordered) and ordered[index + 1].timestamp < timestamp:
            index += 1
        nearby = ordered[max(0, index - 2):index + 4]
        before = [item for item in nearby if item.timestamp <= timestamp]
        after = [item for item in nearby if item.timestamp >= timestamp]
        if not before or not after:
            continue
        if after[0].timestamp - before[-1].timestamp > 2:
            continue
        if any(item.combat or item.gap or item.scene not in ('world', 'dialog', 'menu', 'result') for item in nearby):
            continue
        if before[-1].foreground and after[0].foreground:
            store.event(recording_id, timestamp, 'input', payload)


def recording_worker(root, project_root, matching, ocr_params, frames, replies, max_bytes, environment):
    from pathlib import Path
    store = WorkflowStateStore(root)
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

    def finish(status, reason=''):
        nonlocal recording_id, writer, inputs, observations, pending_frames
        if writer is not None:
            writer.close()
        if recording_id is not None:
            if status == 'complete':
                persist_inputs(store, recording_id, inputs, observations)
            store.finish_recording(recording_id, status, reason)
            replies.put(('finished', recording_id))
        recording_id, writer = None, None
        inputs, observations, pending_frames = [], [], []

    try:
        observer = SceneObserver(Path(project_root), matching, ocr_params)
        replies.put(('ready', '等待游戏画面'))
        while True:
            item = frames.get()
            if item is None:
                finish('complete')
                return
            if item[0] == 'gap':
                finish('interrupted', item[1])
                replies.put(('status', item[1]))
                idle = IdleTracker()
                continue
            _, timestamp, frame, foreground, actions, input_active = item
            pending_frames.append((timestamp, frame))
            inputs.extend(actions)
            if len(inputs) > 100_000:
                raise RuntimeError('操作记录达到容量限制，录制已经停止')
            if timestamp < observation_at:
                continue
            observation_at = timestamp + .5
            observation = observer.observe(frame, timestamp, foreground)
            replies.put(('observation', observation.model_dump()))
            if observation.scene in ('login', 'unknown'):
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
                finish('complete')
                idle_state = True
            if idle_state:
                # 保留变化检测；定期截图不生成需要重复等待的流程。
                if timestamp - last_saved_idle >= 30:
                    replies.put(('status', '角色停留中，继续观察任务变化'))
                    last_saved_idle = timestamp
                inputs.clear()
                pending_frames.clear()
                continue
            if recording_id is None:
                recording_id = store.start_recording()
                store.set_environment(recording_id, environment['game_version'], environment['language'])
                writer = SegmentedVideoWriter(store.resolve(recording_id))
                keyframe_count = 0
                replies.put(('status', '正在自动记录游玩过程'))
            if used_bytes > max_bytes or shutil.disk_usage(store.root).free < 512 * 1024 * 1024:
                raise OSError('记录容量或磁盘可用空间达到限制')
            if observation.changed or not observations or timestamp - observations[-1].timestamp >= 2:
                image_path = f'{recording_id}/{observation.frame_id}.png'
                protected = protect_observation_frame(frame, observation)
                success, encoded = cv2.imencode('.png', protected)
                if not success:
                    raise RuntimeError('游戏关键画面编码失败')
                data = encoded.tobytes()
                store.resolve(image_path).write_bytes(data)
                observation.screenshot = image_path
                observation.image_sha256 = hashlib.sha256(data).hexdigest()
                used_bytes += len(data)
                keyframe_count += 1
            store.observe(recording_id, observation)
            observations.append(observation)
            for frame_time, picture in pending_frames:
                writer.write(protect_observation_frame(picture, observation), frame_time)
            pending_frames.clear()
            if keyframe_count >= 48:
                finish('complete')
            if len(observations) % 120 == 0:
                used_bytes = sum(path.stat().st_size for path in store.root.rglob('*') if path.is_file())
    except BaseException:
        finish('error', '录制进程异常终止，证据存在缺口')
        replies.put(('error', traceback.format_exc()))
        raise


class WindowsGameplaySource:
    def __init__(self, hwnd_window):
        from ok.device.capture import WindowsGraphicsCaptureMethod
        self.window = hwnd_window
        self.capture = WindowsGraphicsCaptureMethod(hwnd_window)

    def frame(self):
        return self.capture.get_frame()

    def foreground(self):
        import win32gui
        return win32gui.GetForegroundWindow() == self.window.hwnd

    def sample_input(self):
        import win32api
        import win32gui
        from src.utils.ManualRouteRecorder import read_action_keys
        if not self.foreground():
            return None
        rect = win32gui.GetClientRect(self.window.hwnd)
        if rect[2] <= 0 or rect[3] <= 0:
            return None
        x, y = win32gui.ScreenToClient(self.window.hwnd, win32api.GetCursorPos())
        return {
            'keys': list(read_action_keys()),
            'buttons': [name for code, name in ((1, 'left'), (2, 'right'), (4, 'middle'))
                        if win32api.GetAsyncKeyState(code) & 0x8000],
            'cursor': [round(x / rect[2], 4), round(y / rect[3], 4)],
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
        self._input_lock = threading.Lock()
        self._inputs = deque()
        self._input_active = False

    def start(self, source, max_gib=20):
        if self.process is not None:
            raise RuntimeError('录制服务已经启动')
        self.source = source
        self.error = None
        self.stop_event.clear()
        context = multiprocessing.get_context('spawn')
        self.frames = context.Queue(maxsize=40)
        self.replies = context.Queue(maxsize=128)
        self.process = context.Process(target=recording_worker,
                                       args=(str(self.store.root), str(self.project_root), self.matching,
                                             self.ocr_params, self.frames, self.replies, int(max_gib * 1024 ** 3), self.environment),
                                       name='GameplayRecording', daemon=True)
        self.process.start()
        self.capture_thread = threading.Thread(target=self._capture, name='GameplayCapture', daemon=True)
        self.input_thread = threading.Thread(target=self._sample_input, name='GameplayInputObservation', daemon=True)
        self.capture_thread.start()
        self.input_thread.start()
        self.status = '正在初始化识别服务'

    def _sample_input(self):
        previous = None
        try:
            while not self.stop_event.is_set() and not self.exit_event.is_set():
                current = None if self.latest and self.latest.get('combat') else self.source.sample_input()
                signature = (tuple(current['keys']), tuple(current['buttons'])) if current is not None else None
                with self._input_lock:
                    self._input_active = current is not None and bool(current['keys'] or current['buttons'])
                    if signature != previous:
                        if current is not None:
                            self._inputs.append((time.monotonic(), current))
                        previous = signature
                self.stop_event.wait(.01)
        except BaseException as error:
            self.error = error
            self.stop_event.set()

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
            while not self.stop_event.is_set() and not self.exit_event.is_set():
                started = time.monotonic()
                self.poll()
                if self.error is not None:
                    raise self.error
                if not self.process.is_alive():
                    raise RuntimeError('录制进程已经终止')
                picture = self.source.frame()
                if picture is None:
                    if not missing:
                        self.frames.put(('gap', '无法取得游戏画面，当前记录已中断'), timeout=1)
                    missing = True
                    self.stop_event.wait(.5)
                    continue
                missing = False
                self.captured_frames += 1
                height, width = picture.shape[:2]
                if height > 1080:
                    picture = cv2.resize(picture, (round(width * 1080 / height), 1080), interpolation=cv2.INTER_AREA)
                with self._input_lock:
                    actions = list(self._inputs)
                    self._inputs.clear()
                    input_active = self._input_active
                self.frames.put(('frame', time.monotonic(), picture, self.source.foreground(), actions, input_active), timeout=1)
                self.stop_event.wait(max(0, .05 - (time.monotonic() - started)))
        except BaseException as error:
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
            elif kind in ('ready', 'status'):
                self.status = payload
            elif kind == 'error':
                self.error = RuntimeError(payload)
                self.status = '录制进程异常，已经停止'

    def stop(self):
        self.stop_event.set()
        if self.capture_thread is not None:
            self.capture_thread.join(timeout=6)
            self.input_thread.join(timeout=2)
            if self.capture_thread.is_alive() or self.input_thread.is_alive():
                raise RuntimeError('录制采集线程未能结束')
        if self.process is not None:
            if self.process.is_alive():
                self.frames.put(None, timeout=5)
                self.process.join(timeout=15)
            self.poll()
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(timeout=5)
                self.store.recover()
                raise RuntimeError('录制进程未能正常结束，当前记录标记为中断')
            self.frames.cancel_join_thread()
            self.replies.cancel_join_thread()
            self.frames.close()
            self.replies.close()
        self.process = None
        self.capture_thread = None
        if self.error is not None:
            raise self.error
        self.status = '未启用'
