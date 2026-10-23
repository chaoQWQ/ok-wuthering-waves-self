import json
import hashlib
import threading
import time
from dataclasses import dataclass

import cv2
import numpy as np
import win32api
import win32con
import win32gui

from src.utils.ManualRouteRecorder import ACTION_VK_CODES
from src.workflow.input import WorkflowStopped
from src.workflow.navigation import RouteFeatureMatcher, geometric_reference


TRAVEL_KEYS = frozenset(('w', 'a', 's', 'd', 'shift', 'space', 'tab', 't'))
KEY_CODES = {**ACTION_VK_CODES, 'tab': win32con.VK_TAB}
MOUSE_FLAGS = {'left': (win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP),
               'right': (win32con.MOUSEEVENTF_RIGHTDOWN, win32con.MOUSEEVENTF_RIGHTUP)}


def check_sample(sample):
    if sample.get('format') != 2:
        raise ValueError('轨迹复现需要包含完整按下和释放事件的录制材料')
    keys, buttons = set(sample['keys']), set(sample['buttons'])
    event_key = sample.get('key_event', {}).get('key')
    if (not keys <= TRAVEL_KEYS or not buttons <= MOUSE_FLAGS.keys()
            or event_key is not None and event_key not in TRAVEL_KEYS):
        raise ValueError('轨迹包含导航和工具操作以外的按键或按钮')
    if 'left' in buttons and 'tab' not in keys:
        raise ValueError('工具轮盘以外的鼠标点击必须生成独立文字交互步骤')
    if sample.get('mouse', {}).get('horizontal_wheel'):
        raise ValueError('轨迹包含未支持的水平滚轮操作')
    if sample.get('mouse', {}).get('mode') == 'absolute' and not sample['cursor_visible']:
        raise ValueError('隐藏光标的绝对定位设备无法提供相对视角轨迹')
    if sample['cursor_visible'] and not all(0 <= position <= 1 for position in sample['cursor']):
        raise ValueError('工具选择光标超出游戏窗口')


@dataclass
class InputSequencePlan:
    started: float
    ended: float
    size: tuple
    samples: list
    checkpoints: list

    @classmethod
    def load(cls, store, workflow, step):
        observations = store.observations(workflow.recording_id)
        indexed = {item.frame_id: item for item in observations}
        before, after = indexed[step.evidence_before], indexed[step.evidence_after]
        interval = [item for item in observations if before.timestamp <= item.timestamp <= after.timestamp]
        if (before.scene != 'world' or after.scene != 'world'
                or any(item.gap or item.combat or not item.foreground
                       or item.scene not in ('world', 'menu') for item in interval)):
            raise ValueError('轨迹步骤必须具有连续的前台非战斗世界与工具轮盘画面')
        events = [(item['timestamp'], json.loads(item['payload']))
                  for item in store.events(workflow.recording_id)
                  if item['kind'] == 'input' and before.timestamp <= item['timestamp'] <= after.timestamp]
        if not events:
            raise ValueError('轨迹步骤缺少原始键鼠事件')
        size = tuple(events[0][1]['client_size'])
        held, buttons = {}, {}
        tab_since = None
        tab_intervals = []
        for timestamp, sample in events:
            check_sample(sample)
            if tuple(sample['client_size']) != size or min(size) <= 0:
                raise ValueError('轨迹录制期间游戏窗口尺寸发生变化')
            current = sample['held_since']
            added = set(current) - held.keys()
            removed = held.keys() - current.keys()
            event = sample.get('key_event', {})
            if added or removed:
                if (len(added) + len(removed) != 1 or event.get('key') not in added | removed
                        or event.get('pressed') != bool(added)):
                    raise ValueError('轨迹存在缺失的按键按下或释放事件')
            elif event:
                raise ValueError('轨迹按键事件与前后持有状态不符')
            if any(value != held[key] for key, value in current.items() if key in held):
                raise ValueError('轨迹按键持有时间不连续')
            if any(abs(current[key] - timestamp) > .005 for key in added):
                raise ValueError('轨迹起点位于已经按住的按键中间')
            if set(current) != set(sample['keys']) or set(sample['buttons_since']) != set(sample['buttons']):
                raise ValueError('轨迹按键和按钮状态不完整')
            if any(value != buttons[key] for key, value in sample['buttons_since'].items() if key in buttons):
                raise ValueError('轨迹鼠标按钮持有时间不连续')
            mouse = sample.get('mouse', {})
            for button, down, up in (('left', 1, 2), ('right', 4, 8)):
                was, now = button in buttons, button in sample['buttons_since']
                flags = mouse.get('button_flags', 0)
                if ((flags & down and (was or not now))
                        or (flags & up and (not was or now))):
                    raise ValueError('轨迹鼠标事件与前后持有状态不符')
                if was != now and not mouse.get('button_flags', 0) & (down if now else up):
                    raise ValueError('轨迹存在缺失的鼠标按钮事件')
                if now and not was and abs(sample['buttons_since'][button] - timestamp) > .005:
                    raise ValueError('轨迹起点位于已经按住的鼠标按钮中间')
            if 'tab' in added:
                tab_since = timestamp
            if 'tab' in removed:
                tab_intervals.append((tab_since, timestamp))
                tab_since = None
            held, buttons = dict(current), dict(sample['buttons_since'])
        if held or buttons:
            raise ValueError('轨迹终点没有包含全部按键和鼠标按钮的释放')
        for item in interval:
            if item.scene == 'menu' and not any(start - .25 <= item.timestamp <= end + .5
                                                for start, end in tab_intervals):
                raise ValueError('轨迹菜单画面没有工具轮盘按键证据')
        checkpoints = [item for item in interval if item.screenshot]
        if (not checkpoints or checkpoints[0].frame_id != before.frame_id
                or checkpoints[-1].frame_id != after.frame_id
                or any(later.timestamp - earlier.timestamp > 2 for earlier, later in zip(checkpoints, checkpoints[1:]))):
            raise ValueError('轨迹缺少连续的图像检查点')
        return cls(before.timestamp, after.timestamp, size, events, checkpoints)


class NativeSequenceInput:
    def __init__(self, hwnd, check):
        self.hwnd, self.check = hwnd, check
        self.keys, self.buttons = set(), set()
        self.lock = threading.RLock()
        self.error = None

    def apply(self, sample):
        check_sample(sample)
        self.check()
        with self.lock:
            if self.error is not None:
                raise self.error
            if win32gui.GetForegroundWindow() != self.hwnd:
                raise WorkflowStopped('游戏窗口失去前台，停止轨迹输入')
            if tuple(win32gui.GetClientRect(self.hwnd)[2:]) != tuple(sample['client_size']):
                raise WorkflowStopped('游戏窗口尺寸与录制轨迹不同')
            if bool(win32gui.GetCursorInfo()[0] & 1) != sample['cursor_visible']:
                raise WorkflowStopped('当前光标显示状态与录制轨迹不同')
            keys, buttons = set(sample['keys']), set(sample['buttons'])
            for key in sorted(self.keys - keys):
                win32api.keybd_event(KEY_CODES[key], 0, win32con.KEYEVENTF_KEYUP, 0)
                self.keys.remove(key)
            for key in sorted(keys - self.keys):
                self.keys.add(key)
                win32api.keybd_event(KEY_CODES[key], 0, 0, 0)
            mouse = sample.get('mouse', {})
            if sample['cursor_visible']:
                width, height = sample['client_size']
                x, y = sample['cursor']
                point = (min(width - 1, round(x * width)), min(height - 1, round(y * height)))
                win32api.SetCursorPos(win32gui.ClientToScreen(self.hwnd, point))
            elif mouse.get('mode') == 'relative' and (mouse['x'] or mouse['y']):
                win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, mouse['x'], mouse['y'], 0, 0)
            for button in sorted(self.buttons - buttons):
                win32api.mouse_event(MOUSE_FLAGS[button][1], 0, 0, 0, 0)
                self.buttons.remove(button)
            for button in sorted(buttons - self.buttons):
                self.buttons.add(button)
                win32api.mouse_event(MOUSE_FLAGS[button][0], 0, 0, 0, 0)
            if mouse.get('wheel'):
                win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, mouse['wheel'], 0)

    def release(self):
        with self.lock:
            for key in tuple(self.keys):
                win32api.keybd_event(KEY_CODES[key], 0, win32con.KEYEVENTF_KEYUP, 0)
                self.keys.remove(key)
            for button in tuple(self.buttons):
                win32api.mouse_event(MOUSE_FLAGS[button][1], 0, 0, 0, 0)
                self.buttons.remove(button)

    def halt(self, error):
        with self.lock:
            self.error = error
            self.release()


class SequenceWatchdog:
    def __init__(self, sink, is_stopped, deadline):
        self.sink, self.is_stopped, self.deadline = sink, is_stopped, deadline
        self.pulsed_at = time.monotonic()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, name='WorkflowInputGuard', daemon=True)

    def start(self):
        self.thread.start()

    def pulse(self):
        if self.sink.error is not None:
            raise self.sink.error
        now = time.monotonic()
        if now >= self.deadline or now - self.pulsed_at > .2:
            error = WorkflowStopped('轨迹处理未能及时响应，释放轨迹输入')
            self.sink.halt(error)
            raise error
        self.pulsed_at = now

    def _run(self):
        try:
            while not self.stopped.wait(.01):
                if self.is_stopped() or win32gui.GetForegroundWindow() != self.sink.hwnd:
                    raise WorkflowStopped('流程暂停、停止或窗口失去前台，释放轨迹输入')
                now = time.monotonic()
                if now >= self.deadline or now - self.pulsed_at > .2:
                    raise WorkflowStopped('轨迹执行或图像处理超时，释放轨迹输入')
        except BaseException as error:
            self.sink.halt(error)

    def close(self):
        self.stopped.set()
        self.thread.join(1)
        if self.thread.is_alive():
            raise RuntimeError('轨迹输入监护线程未能结束')


class InputSequencePlayer:
    def __init__(self, task, store, workflow):
        self.task, self.store, self.workflow = task, store, workflow

    def run(self, step, deadline):
        plan = InputSequencePlan.load(self.store, self.workflow, step)
        task = self.task
        sink = NativeSequenceInput(task.hwnd.hwnd, task._coordinator.check)
        task._coordinator.sequence_input = sink
        watchdog = None
        try:
            if any(win32api.GetAsyncKeyState(KEY_CODES[key]) & 0x8000 for key in TRAVEL_KEYS) or any(
                    win32api.GetAsyncKeyState(button) & 0x8000 for button in (1, 2)):
                raise WorkflowStopped('用户仍然按住键盘或鼠标，无法开始轨迹输入')
            references = []
            features = RouteFeatureMatcher()
            for item in plan.checkpoints:
                data = self.store.resolve(item.screenshot).read_bytes()
                if hashlib.sha256(data).hexdigest() != item.image_sha256:
                    raise WorkflowStopped('轨迹截图与通过检查的证据不符')
                frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
                if frame is None:
                    raise WorkflowStopped('轨迹图像无法解码')
                references.append((item, features.describe(frame)))
            current = task.executor.next_frame(time_out=.1)
            if current is None:
                raise WorkflowStopped('无法取得轨迹起始画面')
            self._require_alignment(features.align('', references[0][1], features.describe(current)))
            started = time.monotonic()
            if started + plan.ended - plan.started > deadline:
                raise WorkflowStopped('轨迹时长超过步骤剩余执行时间')
            watchdog = SequenceWatchdog(sink, lambda: task.executor.exit_event.is_set() or task.executor.paused
                                        or task.paused or not task.enabled, deadline)
            watchdog.start()
            schedule = [(timestamp, 1, sample) for timestamp, sample in plan.samples]
            schedule.extend((item.timestamp, 0, frame) for item, frame in references[1:])
            for timestamp, kind, payload in sorted(schedule, key=lambda item: (item[0], item[1])):
                scheduled = started + timestamp - plan.started
                while time.monotonic() < scheduled:
                    watchdog.pulse()
                    if time.monotonic() >= deadline:
                        raise WorkflowStopped('轨迹复现超过步骤执行时间')
                    task._coordinator.check()
                    time.sleep(min(.005, max(0, scheduled - time.monotonic())))
                watchdog.pulse()
                if time.monotonic() - scheduled > .2:
                    raise WorkflowStopped('图像或输入处理未能及时完成，停止轨迹复现')
                if time.monotonic() >= deadline:
                    raise WorkflowStopped('轨迹复现超过步骤执行时间')
                if kind:
                    sink.apply(payload)
                else:
                    current = task.executor.next_frame(time_out=.1)
                    if current is None:
                        raise WorkflowStopped('轨迹复现途中无法取得画面')
                    self._require_alignment(features.align('', payload, features.describe(current)))
                    watchdog.pulse()
                    if time.monotonic() - scheduled > .2:
                        raise WorkflowStopped('图像核查耗时超出轨迹时序允许范围')
            sink.release()
            watchdog.close()
            watchdog = None
            observation = task.observe_workflow()
            task._coordinator.observe(observation)
            if not step.completion.matches(observation):
                raise WorkflowStopped('轨迹输入已经结束，实际任务状态未满足完成条件')
        finally:
            try:
                sink.release()
            finally:
                try:
                    if watchdog is not None:
                        watchdog.close()
                finally:
                    task._coordinator.sequence_input = None

    @staticmethod
    def _check_reference(reference, current):
        if reference is None or current is None:
            raise WorkflowStopped('轨迹复现缺少可检查的图像')
        match = geometric_reference(reference, current)
        InputSequencePlayer._require_alignment(match)

    @staticmethod
    def _require_alignment(match):
        if (match is None or abs(match.center_shift_x) > .03 or abs(match.center_shift_y) > .03
                or not .9 <= match.area_ratio <= 1.1 or match.coverage < .15):
            raise WorkflowStopped('当前位置或视角与轨迹图像不匹配，停止当前步骤')
