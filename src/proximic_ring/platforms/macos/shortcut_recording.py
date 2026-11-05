"""Temporarily suspend symbolic hotkeys while the foreground UI records a chord.

Carbon's mode stack changes no saved shortcuts. macOS also releases its effect
when this process loses foreground status or exits. All calls stay on the UI
thread, as required by CarbonEvents.h.
"""
from __future__ import annotations

import ctypes
import threading


class SymbolicHotKeyGuard:
    DISABLED = 1  # kHIHotKeyModeAllDisabled

    def __init__(self, *, carbon=None, trusted=None, active=None):
        self._carbon = carbon
        self._trusted = trusted
        self._active = active
        self._token = None

    def _load(self):
        if self._carbon is None:
            carbon = ctypes.CDLL('/System/Library/Frameworks/Carbon.framework/Carbon')
            carbon.PushSymbolicHotKeyMode.argtypes = [ctypes.c_uint32]
            carbon.PushSymbolicHotKeyMode.restype = ctypes.c_void_p
            carbon.PopSymbolicHotKeyMode.argtypes = [ctypes.c_void_p]
            carbon.PopSymbolicHotKeyMode.restype = None
            carbon.GetSymbolicHotKeyMode.argtypes = []
            carbon.GetSymbolicHotKeyMode.restype = ctypes.c_uint32
            self._carbon = carbon
        if self._trusted is None:
            from ApplicationServices import AXIsProcessTrusted
            self._trusted = AXIsProcessTrusted
        if self._active is None:
            from AppKit import NSApplication
            self._active = lambda: NSApplication.sharedApplication().isActive()

    def start(self):
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError('快捷键录入必须在界面线程启动')
        if self._token is not None:
            return
        self._load()
        if not self._active():
            raise RuntimeError('请先返回 Mythlink 窗口，再点击快捷键录入框。')
        # Carbon can return a token even when TCC denies the requested mode.
        if not self._trusted():
            raise RuntimeError('录制系统快捷键需要 Mythlink 的辅助功能权限；也可选择“手动输入”，填写 Cmd+Space 等组合键。')
        token = self._carbon.PushSymbolicHotKeyMode(self.DISABLED)
        if not token:
            raise RuntimeError('无法暂停系统快捷键，请选择“手动输入”填写组合键。')
        self._token = token
        try:
            if self._carbon.GetSymbolicHotKeyMode() != self.DISABLED:
                raise RuntimeError('系统未确认快捷键录制保护，请选择“手动输入”填写组合键。')
        except BaseException:
            self.stop()
            raise

    def stop(self):
        if self._token is None:
            return
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError('快捷键录入必须在界面线程结束')
        self._carbon.PopSymbolicHotKeyMode(self._token)
        self._token = None
