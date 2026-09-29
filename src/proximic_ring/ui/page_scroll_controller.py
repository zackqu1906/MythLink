"""One in-flight scroll gesture; discovery never blocks voice or the Qt UI."""
from __future__ import annotations

import os
import sys
import threading
import time

from PySide6.QtCore import QObject, Qt, Signal, Slot

from ..mac_permissions import MacPermissionError
from ..native_access import NativeAccessChannel
from ..page_scroll import MESSAGES, animate_scroll
from ..text_focus import foreground_stamp


class PageScrollController(QObject):
    notice = Signal(str)
    _result = Signal(object, object, str, object)

    def __init__(self, ring, *, enabled=None, channel=None, stamp_reader=None):
        super().__init__(ring)
        self.ring = ring
        self._enabled = (sys.platform == "darwin" and os.environ.get("PROXIMIC_STARTUP_PROBE") != "1"
                         if enabled is None else enabled)
        self._channel = channel or NativeAccessChannel()
        self._stamp_reader = stamp_reader or foreground_stamp
        self._closed = False
        self.pending = threading.Event()
        self.applying = threading.Event()
        self._result.connect(self._receive, Qt.QueuedConnection)

    def capture_stamp(self):
        if self._enabled and not self._closed:
            try:
                return self._stamp_reader()
            except Exception:
                pass
        return None

    def _valid(self, request, connection):
        ring, owner = self.ring, self.ring.owner
        with ring._lock:
            current = (ring._mode == "operation" and ring._generation == request.generation
                       and ring._transition is None)
        return (current and not self._closed and owner._runtime_active and owner._connected
                and not ring._selector.blocked.is_set() and not getattr(ring, "session_blocked", lambda: False)()
                and connection is owner._disconnect_event and not connection.is_set()
                and time.monotonic() - request.created < 1.5 and not ring.speech_busy())

    def start(self, request, connection):
        if self.pending.is_set() or not self._valid(request, connection):
            return
        if not self._enabled or request.stamp is None:
            self.notice.emit(MESSAGES["unavailable" if self._enabled else "unsupported"])
            return
        self.pending.set()
        self._submit(request, connection, "scroll_plan", expected=request.stamp,
                     direction=request.name.removeprefix("swipe-"))

    def _submit(self, request, connection, operation, **params):
        def work():
            try:
                if not self._valid(request, connection):
                    result = {"status": "cancelled"}
                elif operation == "scroll_apply":
                    result = animate_scroll(
                        lambda progress: self._channel.call(operation, ignored_pid=os.getpid(),
                                                            progress=progress, **params),
                        lambda: self._valid(request, connection))
                else:
                    result = self._channel.call(operation, ignored_pid=os.getpid(), **params)
            except MacPermissionError:
                result = {"status": "permission"}
            except Exception:
                result = {"status": "unavailable"}
            try:
                self._result.emit(request, connection, operation, result)
            except RuntimeError:
                pass
        threading.Thread(target=work, name="RingPageScroll", daemon=True).start()

    @Slot(object, object, str, object)
    def _receive(self, request, connection, operation, result):
        if operation == "scroll_apply":
            self.applying.clear()
        if not self._valid(request, connection):
            self.pending.clear()
            return
        if operation == "scroll_plan" and result.get("plan"):
            self.applying.set()
            self._submit(request, connection, "scroll_apply", plan=result["plan"])
            return
        self.pending.clear()
        status = result.get("status", "unavailable")
        self.ring.owner._event_log("RING_PAGE_SCROLL", direction=request.name, status=status,
                                   pixels=result.get("pixels", 0))
        if status not in {"posted", "cancelled"}:
            self.notice.emit(MESSAGES.get(status, MESSAGES["unavailable"]))

    def close(self):
        self._closed = True
        threading.Thread(target=self._channel.close, name="RingPageScrollClose", daemon=True).start()
