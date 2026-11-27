"""Windows transport for the shared voice and stroke IME protocol."""
from __future__ import annotations

import _winapi
import ctypes
from ctypes import wintypes
from functools import lru_cache
import json
import logging
from multiprocessing.connection import PipeConnection
import sys

from .ime_bridge import IMEBridge, IMEBridgeInUseError, MAX_MESSAGE, PROTOCOL


@lru_cache(maxsize=1)
def _foreground_api():
    user = ctypes.WinDLL("user32")
    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    return user


def foreground_identity():
    user = _foreground_api()
    pid = wintypes.DWORD()
    if user.GetWindowThreadProcessId(user.GetForegroundWindow(), ctypes.byref(pid)):
        return f"win32:{pid.value}", pid.value
    return None


def begin_interactive_stroke():
    """Use interactive QoS while the focused editor belongs to another process."""
    class PowerThrottling(ctypes.Structure):
        _fields_ = [("version", wintypes.ULONG), ("control_mask", wintypes.ULONG),
                    ("state_mask", wintypes.ULONG)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    read, write = kernel.GetProcessInformation, kernel.SetProcessInformation
    read.argtypes = write.argtypes = (wintypes.HANDLE, ctypes.c_int,
                                      ctypes.POINTER(PowerThrottling), wintypes.DWORD)
    process, size = kernel.GetCurrentProcess(), ctypes.sizeof(PowerThrottling)
    power_throttling = 4  # ProcessPowerThrottling
    previous = PowerThrottling(1)
    if not read(process, power_throttling, ctypes.byref(previous), size):
        raise ctypes.WinError(ctypes.get_last_error())

    mask = 1  # PROCESS_POWER_THROTTLING_EXECUTION_SPEED
    if sys.getwindowsversion().build >= 22000:
        # Windows 11 can ignore Raw2's existing timeBeginPeriod request when
        # the main window is covered by the editor.
        mask |= 2  # PROCESS_POWER_THROTTLING_IGNORE_TIMER_RESOLUTION
    interactive = PowerThrottling(previous.version, previous.control_mask | mask,
                                  previous.state_mask & ~mask)
    if not write(process, power_throttling, ctypes.byref(interactive), size):
        raise ctypes.WinError(ctypes.get_last_error())

    def restore():
        if not write(process, power_throttling, ctypes.byref(previous), size):
            raise ctypes.WinError(ctypes.get_last_error())

    return restore


def caret_monitor_origin(caret):
    class MonitorInfo(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("bounds", wintypes.RECT),
                    ("work_area", wintypes.RECT), ("flags", wintypes.DWORD)]

    user = ctypes.WinDLL("user32")
    user.MonitorFromRect.argtypes = (ctypes.POINTER(wintypes.RECT), wintypes.DWORD)
    user.MonitorFromRect.restype = wintypes.HMONITOR
    user.GetMonitorInfoW.argtypes = (wintypes.HMONITOR, ctypes.POINTER(MonitorInfo))
    rect = wintypes.RECT(*caret)
    monitor = user.MonitorFromRect(ctypes.byref(rect), 2)  # nearest monitor
    info = MonitorInfo(ctypes.sizeof(MonitorInfo))
    if monitor and user.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return info.bounds.left, info.bounds.top
    return None


def _create_pipe(first):
    class SecurityAttributes(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("descriptor", ctypes.c_void_p),
                    ("inherit", wintypes.BOOL)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p)
    security.ConvertSidToStringSidW.argtypes = (ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR))
    security.OpenProcessToken.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
    security.GetTokenInformation.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p,
                                              wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.LocalFree.argtypes = (ctypes.c_void_p,)
    kernel.CreateNamedPipeW.argtypes = (
        wintypes.LPCWSTR, *([wintypes.DWORD] * 6), ctypes.POINTER(SecurityAttributes))
    kernel.CreateNamedPipeW.restype = wintypes.HANDLE
    session = wintypes.DWORD()
    if not kernel.ProcessIdToSessionId(kernel.GetCurrentProcessId(), ctypes.byref(session)):
        raise ctypes.WinError(ctypes.get_last_error())
    descriptor = ctypes.c_void_p()
    token = wintypes.HANDLE()
    if not security.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = wintypes.DWORD()
        security.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        user = ctypes.create_string_buffer(size.value)
        if not security.GetTokenInformation(token, 1, user, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = wintypes.LPWSTR()
        if not security.ConvertSidToStringSidW(ctypes.cast(user, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.byref(sid)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                    f"D:P(A;;GA;;;{sid.value})", 1, ctypes.byref(descriptor), None):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            kernel.LocalFree(sid)
    finally:
        kernel.CloseHandle(token)
    try:
        attrs = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
        flags = _winapi.PIPE_ACCESS_DUPLEX | _winapi.FILE_FLAG_OVERLAPPED
        if first:
            flags |= _winapi.FILE_FLAG_FIRST_PIPE_INSTANCE
        handle = kernel.CreateNamedPipeW(
            rf"\\.\pipe\Mythlink.TSF.{session.value}", flags,
            _winapi.PIPE_TYPE_MESSAGE | _winapi.PIPE_READMODE_MESSAGE | 8,
            # The TIP writes whole, hex-encoded messages without blocking the
            # editor. A completed model edit can carry 512K UTF-16 units.
            _winapi.PIPE_UNLIMITED_INSTANCES, 65536, 4 * 1024 * 1024, 0, ctypes.byref(attrs))
        if handle == ctypes.c_void_p(-1).value:
            error = ctypes.get_last_error()
            if first and error == 5:
                raise IMEBridgeInUseError("输入法通道正由另一个 Mythlink 实例使用")
            raise ctypes.WinError(error)
        return handle
    finally:
        kernel.LocalFree(descriptor)


class _Pipe:
    def __init__(self, handle):
        self._pipe = PipeConnection(handle)
        self._timeout = None

    def settimeout(self, timeout):
        self._timeout = timeout

    def makefile(self, _mode):
        return self

    def readline(self, limit):
        if self._timeout is not None and not self._pipe.poll(self._timeout):
            return b""
        fields = self._pipe.recv_bytes(limit).decode("utf-8").split("\t")
        message = {"type": fields[0]}
        for field in fields[1:]:
            key, value = field.split("=", 1)
            kind, value = value[0], value[1:]
            if kind == "s":
                message[key] = bytes.fromhex(value).decode("utf-8")
            elif kind == "n":
                message[key] = int(value)
            elif kind == "b":
                message[key] = value == "1"
            elif kind == "a":
                message[key] = [int(part) for part in value.split(",")]
        if message["type"] == "hello":
            if message.get("protocol") != PROTOCOL:
                return b""
            message["token"] = ""  # The Windows pipe authenticates at open.
        return json.dumps(message, ensure_ascii=False).encode() + b"\n"

    def sendall(self, frame):
        message = json.loads(frame)
        fields = [message.pop("type")]
        for key, value in message.items():
            if isinstance(value, bool):
                fields.append(f"{key}=b{int(value)}")
            elif isinstance(value, int):
                fields.append(f"{key}=n{value}")
            elif isinstance(value, str):
                fields.append(f"{key}=s{value.encode().hex()}")
        payload = "\t".join(fields).encode()
        if len(payload) > MAX_MESSAGE:
            raise ValueError("输入法消息过长")
        self._pipe.send_bytes(payload)

    def shutdown(self, _direction):
        self.close()

    def close(self):
        self._pipe.close()


class WindowsIMEBridge(IMEBridge):
    def start(self):
        self._token = ""
        self._listener = _create_pipe(first=True)
        self._spawn(self._accept_pipe, "tsf-listener")

    def _accept_pipe(self):
        while True:
            with self._condition:
                handle = self._listener
                if self._closed:
                    return
            if handle is None:
                try:
                    handle = _create_pipe(first=False)
                except OSError as exc:
                    # A temporary instance/resource limit must not tear down
                    # an already accepted editor connection.
                    logging.getLogger(__name__).warning("TSF listener retry: %s", exc)
                    with self._condition:
                        if not self._closed:
                            self._condition.wait(.25)
                    continue
                with self._condition:
                    if self._closed:
                        _winapi.CloseHandle(handle)
                        return
                    self._listener = handle
            try:
                pending = _winapi.ConnectNamedPipe(handle, overlapped=True)
                while _winapi.WaitForSingleObject(pending.event, 200) == 258:
                    if self._closed:
                        pending.cancel()
                        pending.GetOverlappedResult(True)
                        return
                _, error = pending.GetOverlappedResult(True)
                if error:
                    raise ctypes.WinError(error)
                with self._condition:
                    if self._closed:
                        return
                    # Transfer ownership before creating the next listener;
                    # failure to allocate it must not leak this connection.
                    self._listener = None
                    self._spawn(self._session, "tsf-client", _Pipe(handle))
            except OSError as exc:
                if self._closed:
                    return
                if getattr(exc, "winerror", None) in (109, 232, 233):
                    _winapi.DisconnectNamedPipe(handle)
                    continue
                logging.getLogger(__name__).warning("TSF listener reset: %s", exc)
                with self._condition:
                    if self._listener == handle:
                        self._listener = None
                        _winapi.CloseHandle(handle)

    def close(self):
        with self._condition:
            self._closed = True
            listener, self._listener = self._listener, None
        if listener is not None:
            _winapi.CloseHandle(listener)
        super().close()
