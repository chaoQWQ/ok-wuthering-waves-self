import ctypes
from ctypes import wintypes
import heapq
import itertools
import threading
import time
import uuid

import win32api
import win32con
import win32gui

from src.utils.ManualRouteRecorder import ACTION_VK_CODES


class RawInputDevice(ctypes.Structure):
    _fields_ = [('usage_page', wintypes.USHORT), ('usage', wintypes.USHORT),
                ('flags', wintypes.DWORD), ('target', wintypes.HWND)]


class RawInputHeader(ctypes.Structure):
    _fields_ = [('kind', wintypes.DWORD), ('size', wintypes.DWORD),
                ('device', wintypes.HANDLE), ('parameter', ctypes.c_size_t)]


class MouseButtons(ctypes.Structure):
    _fields_ = [('flags', wintypes.USHORT), ('data', wintypes.USHORT)]


class MouseButtonUnion(ctypes.Union):
    _fields_ = [('combined', wintypes.ULONG), ('buttons', MouseButtons)]


class RawMouse(ctypes.Structure):
    _fields_ = [('flags', wintypes.USHORT), ('button_union', MouseButtonUnion),
                ('raw_buttons', wintypes.ULONG), ('x', wintypes.LONG), ('y', wintypes.LONG),
                ('extra', wintypes.ULONG)]


class RawKeyboard(ctypes.Structure):
    _fields_ = [('scan_code', wintypes.USHORT), ('flags', wintypes.USHORT),
                ('reserved', wintypes.USHORT), ('virtual_key', wintypes.USHORT),
                ('message', wintypes.UINT), ('extra', wintypes.ULONG)]


KEY_NAMES = {value: name for name, value in ACTION_VK_CODES.items()}
KEY_NAMES.update({0x1B: 'esc', 0x09: 'tab', 0x0D: 'enter', 0x42: 'b', 0x4A: 'j',
                  0x4D: 'm', 0x52: 'r', 0x56: 'v', 0x70: 'f1', 0x71: 'f2'})
BUTTON_FLAGS = ((1, 2, 'left'), (4, 8, 'right'), (16, 32, 'middle'),
                (64, 128, 'x1'), (256, 512, 'x2'))


class WindowsInputCapture:
    def __init__(self, target_hwnd):
        self.target_hwnd = target_hwnd
        self.enabled = threading.Event()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._samples = []
        self._sequence = itertools.count()
        self._keys = {}
        self._buttons = {}
        self._recording = False
        self._last_activity = 0.0
        self.error = None

    def start(self):
        if self._thread is not None:
            raise RuntimeError('键鼠记录服务已经启动')
        self._thread = threading.Thread(target=self._run, name='GameplayRawInput', daemon=True)
        self._thread.start()
        if not self._ready.wait(10):
            self.close()
            raise TimeoutError('Windows Raw Input 初始化超时')
        self._check_error()

    def _check_error(self):
        if self.error is not None:
            raise RuntimeError('Windows Raw Input 记录异常') from self.error

    def drain(self, timestamp):
        self._check_error()
        result = []
        with self._lock:
            while self._samples and self._samples[0][0] <= timestamp:
                captured_at, _, payload = heapq.heappop(self._samples)
                result.append((captured_at, payload))
        return result

    def active(self, timestamp):
        with self._lock:
            return bool(self._keys or self._buttons or timestamp - self._last_activity < .1)

    def _allowed(self):
        return self.enabled.is_set() and win32gui.GetForegroundWindow() == self.target_hwnd

    def _timestamp(self, message=False):
        ticks = self._user32.GetMessageTime() if message else win32api.GetTickCount()
        elapsed = ctypes.c_int32(ticks - self._start_ticks).value
        return self._start_monotonic + elapsed / 1000

    def _state(self):
        width, height = win32gui.GetClientRect(self.target_hwnd)[2:]
        if width <= 0 or height <= 0:
            raise RuntimeError('无法取得键鼠记录所需的窗口尺寸')
        x, y = win32gui.ScreenToClient(self.target_hwnd, win32api.GetCursorPos())
        return {'format': 2, 'keys': sorted(self._keys), 'buttons': sorted(self._buttons),
                'held_since': dict(self._keys), 'buttons_since': dict(self._buttons),
                'cursor': [x / width, y / height], 'client_size': [width, height],
                'cursor_visible': bool(win32gui.GetCursorInfo()[0] & 1)}

    def _append(self, timestamp, payload):
        with self._lock:
            if len(self._samples) >= 500_000:
                raise RuntimeError('键鼠记录队列达到容量限制')
            heapq.heappush(self._samples, (timestamp, next(self._sequence), payload))
            mouse = payload.get('mouse', {})
            if (payload.get('keys') or payload.get('buttons') or mouse.get('x') or mouse.get('y')
                    or mouse.get('button_flags') or mouse.get('wheel') or mouse.get('horizontal_wheel')
                    or mouse.get('flags', 0) & 1):
                self._last_activity = max(self._last_activity, timestamp)

    def _begin(self):
        timestamp = self._timestamp()
        self._keys = {name: timestamp for code, name in KEY_NAMES.items()
                      if win32api.GetAsyncKeyState(code) & 0x8000}
        self._buttons = {name: timestamp for code, name in ((1, 'left'), (2, 'right'), (4, 'middle'),
                                                           (5, 'x1'), (6, 'x2'))
                         if win32api.GetAsyncKeyState(code) & 0x8000}
        self._recording = True
        self._append(timestamp, {**self._state(), 'baseline': True})

    def _receive(self, handle):
        if not self._allowed():
            self._recording = False
            self._keys.clear()
            self._buttons.clear()
            return
        if not self._recording:
            self._begin()
        size = wintypes.UINT()
        header_size = ctypes.sizeof(RawInputHeader)
        if self._user32.GetRawInputData(handle, 0x10000003, None, ctypes.byref(size), header_size) == 0xFFFFFFFF:
            raise ctypes.WinError(ctypes.get_last_error())
        if size.value < header_size:
            raise ValueError('Windows Raw Input 消息长度不足')
        buffer = ctypes.create_string_buffer(size.value)
        count = self._user32.GetRawInputData(handle, 0x10000003, buffer, ctypes.byref(size), header_size)
        if count == 0xFFFFFFFF:
            raise ctypes.WinError(ctypes.get_last_error())
        if count != size.value:
            raise ValueError('Windows Raw Input 消息读取不完整')
        header = RawInputHeader.from_buffer_copy(buffer)
        timestamp = self._timestamp(message=True)
        if header.kind == 1:
            keyboard = RawKeyboard.from_buffer_copy(buffer, header_size)
            name = KEY_NAMES.get(keyboard.virtual_key)
            if name is None:
                return
            pressed = not keyboard.flags & 1
            if pressed == (name in self._keys):
                return
            if pressed:
                self._keys[name] = timestamp
            else:
                self._keys.pop(name)
            self._append(timestamp, {**self._state(), 'key_event': {'key': name, 'pressed': pressed}})
        elif header.kind == 0:
            mouse = RawMouse.from_buffer_copy(buffer, header_size)
            flags = mouse.button_union.buttons.flags
            for down, up, name in BUTTON_FLAGS:
                if flags & down:
                    self._buttons[name] = timestamp
                if flags & up:
                    self._buttons.pop(name, None)
            wheel = ctypes.c_short(mouse.button_union.buttons.data).value
            motion = {'mode': 'absolute' if mouse.flags & 1 else 'relative',
                      'x': mouse.x, 'y': mouse.y, 'virtual_desktop': bool(mouse.flags & 2),
                      'wheel': wheel if flags & 0x400 else 0,
                      'horizontal_wheel': wheel if flags & 0x800 else 0,
                      'button_flags': flags, 'button_data': mouse.button_union.buttons.data,
                      'raw_buttons': mouse.raw_buttons, 'flags': mouse.flags}
            self._append(timestamp, {**self._state(), 'mouse': motion})

    def _window_proc(self, hwnd, message, parameter, data):
        if message == 0x00FF and not self._stop.is_set():
            try:
                self._receive(data)
            except BaseException as error:
                # 将 Windows 回调中的异常交给录制进程处理。
                self.error = error
                self._stop.set()
        return win32gui.DefWindowProc(hwnd, message, parameter, data)

    def _run(self):
        name = 'GameplayRawInput_' + uuid.uuid4().hex
        instance = win32api.GetModuleHandle(None)
        hwnd = None
        registered = False
        try:
            self._user32 = ctypes.WinDLL('user32', use_last_error=True)
            self._user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RawInputDevice), wintypes.UINT, wintypes.UINT]
            self._user32.RegisterRawInputDevices.restype = wintypes.BOOL
            self._user32.GetRawInputData.argtypes = [wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p,
                                                   ctypes.POINTER(wintypes.UINT), wintypes.UINT]
            self._user32.GetRawInputData.restype = wintypes.UINT
            self._user32.GetMessageTime.argtypes = []
            self._user32.GetMessageTime.restype = wintypes.LONG
            self._start_ticks = win32api.GetTickCount()
            self._start_monotonic = time.monotonic()
            window_class = win32gui.WNDCLASS()
            window_class.hInstance = instance
            window_class.lpszClassName = name
            window_class.lpfnWndProc = self._window_proc
            win32gui.RegisterClass(window_class)
            registered = True
            hwnd = win32gui.CreateWindowEx(0, name, '', 0, 0, 0, 0, 0, -3, 0, instance, None)
            devices = (RawInputDevice * 2)(RawInputDevice(1, 2, 0x100, hwnd), RawInputDevice(1, 6, 0x100, hwnd))
            if not self._user32.RegisterRawInputDevices(devices, 2, ctypes.sizeof(RawInputDevice)):
                raise ctypes.WinError(ctypes.get_last_error())
            self._ready.set()
            while not self._stop.is_set():
                allowed = self._allowed()
                if allowed and not self._recording:
                    self._begin()
                elif not allowed:
                    self._recording = False
                    self._keys.clear()
                    self._buttons.clear()
                if win32gui.PumpWaitingMessages():
                    raise RuntimeError('Windows Raw Input 消息循环意外结束')
                self._stop.wait(.001)
        except BaseException as error:
            self.error = error
        finally:
            if hwnd is not None:
                devices = (RawInputDevice * 2)(RawInputDevice(1, 2, 1, None), RawInputDevice(1, 6, 1, None))
                if not self._user32.RegisterRawInputDevices(devices, 2, ctypes.sizeof(RawInputDevice)):
                    self.error = ctypes.WinError(ctypes.get_last_error())
                win32gui.DestroyWindow(hwnd)
            if registered:
                win32gui.UnregisterClass(name, instance)
            self._ready.set()

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(5)
            if self._thread.is_alive():
                raise RuntimeError('Windows Raw Input 线程未能结束')
        self._check_error()
