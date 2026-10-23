"""Optional recognition feedback; a bounded mailbox, independent of action routing."""
from __future__ import annotations

import threading
import time
from collections import deque

from PySide6.QtCore import QObject, Property, Qt, Signal, Slot


SETTING_KEY = "ui/gestureTriggerHints"
MAX_PENDING_HINTS = 64


class GestureTriggerController(QObject):
    enabledChanged = Signal()
    triggered = Signal(list)
    hideRequested = Signal()
    _wake = Signal()

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self._enabled = owner._settings.value(SETTING_KEY, False, type=bool)
        self._closed = False
        self._lock = threading.Lock()
        self._pending = deque(maxlen=MAX_PENDING_HINTS)
        self._queued = False
        self._wake.connect(self._deliver, Qt.QueuedConnection)
        owner.connectedChanged.connect(self._connection_changed)

    @Property(bool, notify=enabledChanged)
    def enabled(self):
        return self._enabled

    @enabled.setter
    def enabled(self, value):
        value = bool(value)
        if self._closed or value == self._enabled:
            return
        with self._lock:
            self._enabled = value
            self._pending.clear()
        self.owner._settings.setValue(SETTING_KEY, value)
        if not value:
            self.hideRequested.emit()
        self.enabledChanged.emit()

    def submit(self, name, connection):
        """Worker entry: no QSettings, OS queries, rendering or waiting for Qt.

        A contended notification may be dropped; an input/action never waits
        for optional feedback. One Qt wakeup delivers a bounded batch in order,
        including repeated gestures, without repainting for every queued event.
        """
        if (not self._enabled or self._closed or not name
                or connection is not self.owner._disconnect_event or connection.is_set()):
            return
        if not self._lock.acquire(blocking=False):
            return
        try:
            if not self._enabled or self._closed:
                return
            self._pending.append((name, connection, time.monotonic()))
            wake = not self._queued
            self._queued = True
        finally:
            self._lock.release()
        if wake:
            self._wake.emit()

    @Slot()
    def _deliver(self):
        with self._lock:
            pending = self._pending
            self._pending = deque(maxlen=MAX_PENDING_HINTS)
            self._queued = False
        if not pending or not self._enabled or self._closed:
            return
        now = time.monotonic()
        names = [name for name, connection, created in pending
                 if connection is self.owner._disconnect_event and not connection.is_set()
                 and now - created < 1]
        if names:
            self.triggered.emit(names)

    @Slot()
    def _connection_changed(self):
        if not self.owner.connected:
            with self._lock:
                self._pending.clear()
            self.hideRequested.emit()

    def close(self):
        with self._lock:
            self._closed = True
            self._pending.clear()
        self.hideRequested.emit()
