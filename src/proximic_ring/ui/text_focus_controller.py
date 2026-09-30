"""Asynchronous input-field monitoring, with no continuous focus enforcement."""
from __future__ import annotations

import os
import sys
import threading
import time

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot, Qt

from ..mac_permissions import MacPermissionError
from ..native_access import NativeAccessChannel
from ..text_focus import MESSAGES, foreground_stamp


class TextFocusController(QObject):
    changed = Signal()
    notice = Signal(str)
    _result = Signal(int, str, object, object)

    def __init__(self, ring, *, enabled=None, channel=None, stamp_reader=None):
        super().__init__(ring)
        self.ring = ring
        self._enabled = (sys.platform == "darwin" and os.environ.get("PROXIMIC_STARTUP_PROBE") != "1"
                         if enabled is None else enabled)
        # Discovery never holds up the existing voice/shortcut control channel.
        self._channel = channel or NativeAccessChannel()
        self._stamp_reader = stamp_reader or foreground_stamp
        self._closed = False
        self._active = False
        self._working = False
        self._revision = 0
        self._last_scope = None
        self._inspect_requested = False
        self.pending = threading.Event()
        self.applying = threading.Event()
        self._state = {"status": "checking", "count": 0, "index": 0}
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self.poll)
        self._result.connect(self._receive, Qt.QueuedConnection)
        from .field_selection_controller import FieldSelectionController
        self.picker = FieldSelectionController(self)

    @Property(bool, notify=changed)
    def available(self):
        return self._state["status"] in {"available", "focused"} and self._state.get("count", 0) > 0

    @Property(str, notify=changed)
    def hint(self):
        if self.available and self._state.get("partial"):
            return "部分区域未提供控件"
        return "" if self.available else MESSAGES.get(self._state["status"], MESSAGES["unavailable"])

    def _set_state(self, result):
        if result != self._state:
            self._state = result
            self.changed.emit()

    def capture_stamp(self):
        if not self._enabled or self._closed:
            return None
        try:
            return self._stamp_reader()
        except Exception:
            return None

    def _eligible(self):
        owner = self.ring.owner
        return (self._active and not self._closed and self.ring.mode == "input"
                and not self.ring._selector.blocked.is_set()
                and not getattr(self.ring, "session_blocked", lambda: False)()
                and owner._connected and owner._runtime_active and not owner._disconnect_event.is_set())

    def sync(self):
        active = (self._enabled and self.ring.mode == "input" and self.ring.owner._connected
                  and not self.ring._selector.blocked.is_set()
                and not getattr(self.ring, "session_blocked", lambda: False)())
        if active == self._active or self._closed:
            return
        self._active = active
        self.picker.stop()
        self._revision += 1
        # Closing the window selector back into the same window is not a new
        # window-entry event. Keep its observed scope through this suspension.
        if not active and not self.ring._selector.blocked.is_set():
            self._last_scope = None
        self._inspect_requested = active
        if not self.applying.is_set():
            self.pending.clear()
        self._set_state({"status": "checking" if active else "no_window", "count": 0, "index": 0})
        if active:
            self._timer.start()
            self.poll()
        else:
            self._timer.stop()

    @Slot()
    def poll(self):
        if (not self._eligible() or self._working or self.picker.active.is_set()
                or self.picker._snapshot is not None):
            return
        self._submit("focus_probe")

    def refresh(self):
        if self._eligible():
            self._inspect_requested = True
            self.poll()

    def _submit(self, operation, *, context=None, **params):
        self._working = True
        revision = self._revision
        def work():
            try:
                if (operation == "focus_apply" and (revision != self._revision
                        or not self._eligible() or self.ring.speech_busy() or self.picker.active.is_set())):
                    result = {"status": "cancelled", "count": 0, "index": 0}
                else:
                    scene_apps = self.ring.owner._app_gestures.catalog.scene_bundles()
                    options = {"scene_apps": scene_apps} if scene_apps else {}
                    result = self._channel.call(operation, ignored_pid=os.getpid(), **options, **params)
            except MacPermissionError:
                result = {"status": "permission", "count": 0, "index": 0}
            except Exception:
                result = {"status": "unavailable", "count": 0, "index": 0}
            try:
                self._result.emit(revision, operation, result, context)
            except RuntimeError:
                pass
        threading.Thread(target=work, name="RingTextFocus", daemon=True).start()

    @Slot(int, str, object, object)
    def _receive(self, revision, operation, result, context):
        self._working = False
        if operation == "focus_apply":
            self.applying.clear()
        if self.picker.active.is_set():
            return  # Selection owns AX focus and the availability state now.
        if self._closed or revision != self._revision or not self._eligible():
            self.pending.clear()
            return
        if operation == "focus_probe":
            if result.get("status") != "ready":
                # A transient AX failure in the same window is not a new
                # window-entry event and must not recapture keyboard focus.
                if result.get("status") == "own_app":
                    self._last_scope = None
                self._inspect_requested = True
                self._set_state(result)
                return
            changed = result["scope"] != self._last_scope
            self._last_scope = result["scope"]
            if changed or self._inspect_requested:
                self._inspect_requested = False
                # Observe a busy window change, but don't defer its autofocus.
                action = "restore" if changed and not self.ring.speech_busy() else "inspect"
                if action == "restore":
                    self.pending.set()
                self._set_state({"status": "checking", "count": 0, "index": 0})
                context = (action, result["stamp"], time.monotonic() + 1.3)
                self._submit("focus_plan", context=context, action=action, expected=result["stamp"])
            return
        self._set_state({key: value for key, value in result.items() if key != "plan"})
        if operation == "focus_plan" and result.get("plan"):
            if self.ring.speech_busy() or time.monotonic() >= context[2]:
                self.pending.clear()
                return
            self.applying.set()
            self._submit("focus_apply", context=context, plan=result["plan"])
            return
        self.pending.clear()
        if operation == "focus_apply":
            self.ring.owner._event_log("RING_TEXT_FOCUS", status=result.get("status"),
                                       count=result.get("count", 0), index=result.get("index", 0))

    def close(self):
        self.picker.stop()
        self._closed = True
        self._revision += 1
        self._timer.stop()
        self.pending.clear()
        # Never wait for Accessibility IPC on the GUI/audio thread.
        threading.Thread(target=self._channel.close, name="RingTextFocusClose", daemon=True).start()
