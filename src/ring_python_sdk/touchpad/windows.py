"""Win32 pointer output; loading or enabling the adapter never posts input."""
from __future__ import annotations

import ctypes
import math
import os
import time


class _Point(ctypes.Structure):
    _fields_ = [('x', ctypes.c_int32), ('y', ctypes.c_int32)]


class _MouseInput(ctypes.Structure):
    _fields_ = [('dx', ctypes.c_int32), ('dy', ctypes.c_int32),
                ('mouseData', ctypes.c_uint32), ('dwFlags', ctypes.c_uint32),
                ('time', ctypes.c_uint32), ('dwExtraInfo', ctypes.c_size_t)]


class _KeyboardInput(ctypes.Structure):
    _fields_ = [('wVk', ctypes.c_uint16), ('wScan', ctypes.c_uint16),
                ('dwFlags', ctypes.c_uint32), ('time', ctypes.c_uint32),
                ('dwExtraInfo', ctypes.c_size_t)]


class _HardwareInput(ctypes.Structure):
    _fields_ = [('uMsg', ctypes.c_uint32), ('wParamL', ctypes.c_uint16), ('wParamH', ctypes.c_uint16)]


class _InputUnion(ctypes.Union):
    _fields_ = [('mi', _MouseInput), ('ki', _KeyboardInput), ('hi', _HardwareInput)]


class _Input(ctypes.Structure):
    _anonymous_ = ('union',)
    _fields_ = [('type', ctypes.c_uint32), ('union', _InputUnion)]


class WindowsSystemMouse:
    def __init__(self, user32=None, *, stroke_target=None):
        if user32 is None:
            if os.name != 'nt':
                raise RuntimeError('WindowsSystemMouse requires Windows')
            user32 = ctypes.WinDLL('user32', use_last_error=True)
            user32.GetCursorPos.argtypes = [ctypes.POINTER(_Point)]
            user32.GetCursorPos.restype = ctypes.c_int
            user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
            user32.SetCursorPos.restype = ctypes.c_int
            user32.GetSystemMetrics.argtypes = [ctypes.c_int]
            user32.GetSystemMetrics.restype = ctypes.c_int
            user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
            user32.GetAsyncKeyState.restype = ctypes.c_int16
            user32.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(_Input), ctypes.c_int]
            user32.SendInput.restype = ctypes.c_uint
        self.user32 = user32
        self.stroke_target = stroke_target
        self.enabled = False
        self.gain = 1.
        self.invert_y = False
        self.clicks_enabled = True
        self.stop_on_escape = True
        self._fraction_x = self._fraction_y = 0.

    def enable(self):
        self._fraction_x = self._fraction_y = 0.
        self.enabled = True

    def disable(self):
        self.enabled = False
        self._fraction_x = self._fraction_y = 0.

    def close(self):
        self.disable()

    def check_health(self):
        if self.enabled and self.stop_on_escape and self.user32.GetAsyncKeyState(0x1B) & 0x8000:
            self.disable()
            raise InterruptedError('Esc 已停止触摸板')

    def _error(self, operation):
        error = ctypes.get_last_error() if os.name == 'nt' else 0
        return OSError(error, operation + ' 失败，请检查当前桌面及目标应用的输入权限。')

    def _position(self):
        point = _Point()
        if not self.user32.GetCursorPos(ctypes.byref(point)):
            raise self._error('GetCursorPos')
        return point

    def _click(self):
        if not self.enabled or not self.clicks_enabled:
            return
        inputs = (_Input * 2)(
            _Input(type=0, mi=_MouseInput(dwFlags=0x0002)),
            _Input(type=0, mi=_MouseInput(dwFlags=0x0004)))
        if self.user32.SendInput(2, inputs, ctypes.sizeof(_Input)) != 2:
            raise self._error('SendInput')

    def double_click_target(self):
        self.check_health()
        return self.stroke_target() if self.enabled and self.stroke_target else None

    def complete_double_click(self):
        # The shared click detector already posted the first click. Outside
        # the local trial editor, send the second normally to Windows.
        self.apply([dict(kind='click')])

    def handle(self, event):
        if not 0 <= time.monotonic()-event.timestamp <= .1:
            return
        if event.kind == 'move':
            self.apply([dict(kind='move', dx=event.dx, dy=event.dy)])
        elif event.kind == 'click':
            self.apply([dict(kind='click')])

    def apply(self, events):
        moves = 0
        if not self.enabled:
            return dict(moves=moves)
        try:
            self.check_health()
            point = self._position()
            left, top, width, height = [self.user32.GetSystemMetrics(i) for i in (76, 77, 78, 79)]
            if width <= 0 or height <= 0:
                raise RuntimeError('无法读取 Windows 桌面显示范围')
            for event in events:
                if not self.enabled:
                    break
                self.check_health()
                if event['kind'] == 'move':
                    dx = float(event['dx'])*self.gain
                    dy = float(event['dy'])*self.gain*(-1 if self.invert_y else 1)
                    if not math.isfinite(dx) or not math.isfinite(dy):
                        raise ValueError('鼠标位移不是有限数值')
                    x, y = dx+self._fraction_x, dy+self._fraction_y
                    step_x, step_y = round(x), round(y)
                    self._fraction_x, self._fraction_y = x-step_x, y-step_y
                    if step_x or step_y:
                        x = max(left, min(left+width-1, point.x+step_x))
                        y = max(top, min(top+height-1, point.y+step_y))
                        if not self.enabled:
                            break
                        if (x, y) != (point.x, point.y):
                            if not self.user32.SetCursorPos(x, y):
                                raise self._error('SetCursorPos')
                            moves += 1
                            # Windows can clamp again (ClipCursor/display gaps).
                            point = self._position()
                elif event['kind'] == 'click' and self.clicks_enabled:
                    self._click()
        except Exception:
            self.disable()
            raise
        return dict(moves=moves)
