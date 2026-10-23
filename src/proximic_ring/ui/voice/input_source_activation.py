"""Check/select the system input source in a cancellable helper process.

No AX/text-field detection, keyboard injection or GUI-thread wait is performed.
The helper may briefly own focus to refresh a newly selected input context.
The UI waits for native BEGIN before starting gesture-controlled ASR.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from PySide6.QtCore import QObject, QProcess, QTimer, Signal
from PySide6.QtGui import QGuiApplication

from ...voice.ime.input_method_install import payload_directory


INPUT_ACTIVATION_TIMEOUT_MS = 1000


def agent_input_window(origin):
    """Capture only the keyed Agent panel in this process, never the main UI."""
    if origin[1] != os.getpid() or QGuiApplication.platformName() != 'cocoa':
        return None
    window = QGuiApplication.focusWindow()
    if (window is not None and window.isVisible() and window.isActive()
            and window.objectName().startswith('agent-')):
        return window
    return None


def restore_agent_input_window(window, helper_pid):
    """Return from this helper to this still-valid panel without app-wide raise."""
    from PySide6.QtCore import Qt
    from shiboken6 import isValid
    from AppKit import NSWorkspace, NSRunningApplication, NSApplicationActivateIgnoringOtherApps
    import ctypes
    import objc
    front = NSWorkspace.sharedWorkspace().frontmostApplication()
    if (helper_pid <= 0 or front is None or int(front.processIdentifier()) != helper_pid
            or not isValid(window) or not window.isVisible()
            or window.flags() & Qt.WindowDoesNotAcceptFocus
            or QGuiApplication.focusWindow() not in (None, window)):
        return False
    native = objc.objc_object(c_void_p=ctypes.c_void_p(int(window.winId()))).window()
    native.orderFrontRegardless()
    native.makeKeyWindow()
    window.requestActivate()
    application = NSRunningApplication.currentApplication()
    if not application.isActive():
        application.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
    return bool(native.isKeyWindow())


def warm_input_method():
    """Launch our accessory IME in the background without hiding its palettes."""
    # `open` can pass the smoke-test environment to the long-lived installed
    # IME. That process would keep reconnecting to a deleted temporary socket
    # even after a normal host starts. Isolated hosts must not launch it.
    if os.environ.get("PROXIMIC_IME_SOCKET", "").strip() or "--self-check-package" in sys.argv:
        return
    bundle = Path.home() / "Library/Input Methods/ProxiMicInput.app"
    if (bundle / "Contents/MacOS/ProxiMicInput").is_file():
        QProcess.startDetached("/usr/bin/open", ["-g", str(bundle)])


class InputSourceActivation(QObject):
    event = Signal(object)

    def __init__(self, parent=None, *, command=None, timeout_ms=INPUT_ACTIVATION_TIMEOUT_MS):
        super().__init__(parent)
        self._command = command
        self._timeout_ms = timeout_ms
        self._process = QProcess(self)
        self._process.readyReadStandardOutput.connect(self._read)
        self._process.finished.connect(self._finished)
        self._process.errorOccurred.connect(self._process_error)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self._fail("输入法切换超时，请稍后重试"))
        self._buffer = b""
        self._stage = "new"
        self._released = False
        self._local_window = None

    def start(self, origin):
        self._start(origin, "ensure-selected")

    def recover(self, origin):
        self._start(origin, "refresh-selected")

    def owns_foreground(self):
        """Allow only this live helper during its intentional focus interval."""
        pid = int(self._process.processId())
        if self._stage != "probing" or pid <= 0:
            return False
        try:
            from ...platforms.macos.mac_workspace import frontmost_application
            front = frontmost_application()
            return front is not None and int(front.processIdentifier()) == pid
        except Exception:
            return False

    def _start(self, origin, operation):
        if self._stage != "new":
            return
        self._stage = "probing"
        self._local_window = agent_input_window(origin)
        if self._local_window is not None:
            QGuiApplication.instance().focusWindowChanged.connect(self._local_focus_changed)
        command = self._command or [str(payload_directory() / "InputMethodAdmin")]
        self._timer.start(self._timeout_ms)
        args = [*command[1:], operation, str(origin[1]), origin[0]]
        if self._local_window is not None:
            args.append("--host-restores-focus")
        self._process.start(command[0], args)

    def _local_focus_changed(self, window):
        if self._stage == 'probing' and window is not None and window is not self._local_window:
            self._fail("已切换窗口，本句已取消")

    def _clear_local_window(self):
        if self._local_window is not None:
            QGuiApplication.instance().focusWindowChanged.disconnect(self._local_focus_changed)
            self._local_window = None

    def cancel(self):
        if self._released:
            return
        self._released = True
        self._stage = "cancelled"
        self._timer.stop()
        self._clear_local_window()
        if self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.kill()  # asynchronous; never wait for the child
        else:
            self.deleteLater()

    def _fail(self, error):
        if self._stage in {"cancelled", "failed", "done"}:
            return
        self.cancel()
        self._stage = "failed"
        self.event.emit({"event": "error", "error": error})

    def _read(self):
        self._buffer += bytes(self._process.readAllStandardOutput())
        if len(self._buffer) > 8192:
            self._fail("输入法切换工具返回异常数据")
            return
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            if self._stage in {"cancelled", "failed", "done"}:
                continue
            try:
                data = json.loads(line)
                kind = data["event"]
                if not isinstance(kind, str):
                    raise ValueError()
            except (ValueError, KeyError, TypeError):
                self._fail("输入法切换工具返回异常数据")
                return
            if kind == "error":
                self._fail(str(data.get("error") or "无法切换输入法"))
                return
            if kind == "restore_focus" and self._stage == "probing":
                try:
                    restored = (self._local_window is not None and
                        restore_agent_input_window(self._local_window, int(self._process.processId())))
                except (ImportError, RuntimeError, AttributeError):
                    restored = False
                if not restored:
                    self._fail("Agent 输入窗口已变化，本句已取消")
                continue
            if kind != "selected" or self._stage != "probing":
                self._fail("输入法切换工具状态异常")
                return
            self._stage = "done"
            self._timer.stop()
            self._clear_local_window()
            self.event.emit(data)

    def _finished(self, code, status):
        self._read()
        if self._stage not in {"done", "cancelled", "failed"}:
            self._fail("输入法切换工具提前退出，请更新组件或重试")
        if self._released:
            self.deleteLater()

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._fail("无法启动输入法切换工具，请构建或更新完整应用")
