"""Windows system pointer adapter; importing this module never posts events."""
from __future__ import annotations

import ctypes
import math
import os
from ctypes import wintypes


class _Point(ctypes.Structure):
    _fields_ = (("x", wintypes.LONG), ("y", wintypes.LONG))


class _MouseInput(ctypes.Structure):
    _fields_ = (("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", wintypes.WPARAM))


class _InputUnion(ctypes.Union):
    _fields_ = (("mi", _MouseInput),)


class _Input(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = (("type", wintypes.DWORD), ("value", _InputUnion))


class WindowsSystemMouse:
    """Apply ordered Ring movement and clicks through the Windows user32 API."""

    def __init__(self, user32=None):
        if os.name != "nt" and user32 is None:
            raise RuntimeError("WindowsSystemMouse requires Windows")
        self.user32 = user32 or ctypes.WinDLL("user32", use_last_error=True)
        self.enabled = False
        self.gain = 1.0
        self.invert_y = False
        self.clicks_enabled = True
        self._fraction_x = 0.0
        self._fraction_y = 0.0

    def enable(self):
        self._fraction_x = self._fraction_y = 0.0
        self.enabled = True

    def disable(self):
        self.enabled = False
        self._fraction_x = self._fraction_y = 0.0

    def escape_pressed(self):
        return bool(self.user32.GetAsyncKeyState(0x1B) & 0x8000)

    def _position(self):
        point = _Point()
        if not self.user32.GetCursorPos(ctypes.byref(point)):
            raise ctypes.WinError(ctypes.get_last_error())
        return point

    def _click(self):
        inputs = (_Input * 2)(
            _Input(type=0, mi=_MouseInput(dwFlags=0x0002)),
            _Input(type=0, mi=_MouseInput(dwFlags=0x0004)),
        )
        if self.user32.SendInput(2, inputs, ctypes.sizeof(_Input)) != 2:
            raise ctypes.WinError(ctypes.get_last_error())

    def apply(self, events):
        if not self.enabled:
            return
        try:
            if self.escape_pressed():
                raise InterruptedError("Esc 已停止鼠标控制")
            point = self._position()
            for event in events:
                if not self.enabled:
                    break
                if event["kind"] == "move":
                    dx = float(event["dx"]) * self.gain
                    dy = float(event["dy"]) * self.gain * (-1 if self.invert_y else 1)
                    if not math.isfinite(dx) or not math.isfinite(dy):
                        raise ValueError("鼠标位移不是有限数值")
                    x, y = dx + self._fraction_x, dy + self._fraction_y
                    step_x, step_y = round(x), round(y)
                    self._fraction_x, self._fraction_y = x - step_x, y - step_y
                    if step_x or step_y:
                        if not self.user32.SetCursorPos(point.x + step_x, point.y + step_y):
                            raise ctypes.WinError(ctypes.get_last_error())
                        point.x += step_x
                        point.y += step_y
                elif event["kind"] == "click" and self.clicks_enabled:
                    self._click()
        except Exception:
            self.disable()
            raise
