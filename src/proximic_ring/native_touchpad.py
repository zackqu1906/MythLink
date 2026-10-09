"""Pointer delivery in a fresh permission process, with an immediate stop gate."""
from __future__ import annotations

import os
import logging
import select
import threading
import time

from .native_access import NativeAccessChannel
from .mac_activity import MacActivity
from ring_python_sdk.touchpad.macos import MacSystemMouse


class NativeTouchpadMouse:
    """Used on the existing pointer thread; no UI/BLE thread waits on IPC."""
    def __init__(self, *, channel_factory=NativeAccessChannel):
        self._cancel_read, self._cancel_write = os.pipe()
        self._channel = channel_factory(pass_fds=(self._cancel_read,))
        self._stopped = threading.Event()
        self._closed = False
        self._next_health_check = 0.
        self.enabled = False
        self.gain = 1.
        self.clicks_enabled = True
        self.invert_y = False
        self.stop_on_escape = False

    def enable(self):
        if self._stopped.is_set():
            return
        status = self._channel.call("touchpad_enable", cancel_fd=self._cancel_read, gain=self.gain,
                                    clicks=self.clicks_enabled, invert_y=self.invert_y)
        if isinstance(status, dict):
            logging.getLogger(__name__).info('[APP_ACTIVITY] mouse_worker=%s protected=%s',
                status.get('pid'), status.get('app_nap_protected'))
        self.enabled = not self._stopped.is_set()
        self._next_health_check = time.monotonic() + 1.

    def check_health(self):
        # Read cached child status even while idle or paused for stroke input.
        # The actual OS permission query runs on the child's monitor thread.
        now = time.monotonic()
        if self.enabled and not self._stopped.is_set() and now >= self._next_health_check:
            self._channel.call("touchpad_health")
            self._next_health_check = time.monotonic() + 1.

    def apply(self, events):
        if self.enabled and not self._stopped.is_set():
            return self._channel.call("touchpad_apply", events=events, created=time.monotonic())

    def double_click_target(self):
        if self.enabled and not self._stopped.is_set():
            return self._channel.call("touchpad_double_click_target", created=time.monotonic())
        return None

    def disable(self):
        self.enabled = False
        if not self._stopped.is_set():
            self._stopped.set()
            try:
                # A single byte wakes the child's gate even during a batch.
                # Unlike another IPC request, this never waits behind Quartz.
                os.write(self._cancel_write, b"\0")
            except OSError:
                pass

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.disable()
        try:
            self._channel.close()
        finally:
            os.close(self._cancel_read)
            os.close(self._cancel_write)


class WorkerTouchpadMouse(MacSystemMouse):
    """Keep the SDK's movement, gain and click logic, checking the shared gate."""
    def __init__(self, cancel_fd, **kwargs):
        self._cancel_fd = cancel_fd
        self._activity = MacActivity('Mythlink system pointer output', latency_critical=True)
        super().__init__(**kwargs)
        self.stop_on_escape = False

    def enable(self):
        super().enable()
        if self.enabled:
            self._activity.start()

    def disable(self):
        super().disable()
        self._activity.close()

    @property
    def app_nap_protected(self):
        return self._activity.active

    def double_click_target(self):
        """Use the current app, including focus changes made by the first click."""
        from .mac_workspace import frontmost_application
        self.check_health()
        if not self.enabled or not self.clicks_enabled:
            return None
        app = frontmost_application()
        if app is not None and app.bundleIdentifier() and self.enabled:
            return {"bundle": str(app.bundleIdentifier()), "pid": int(app.processIdentifier())}
        return None

    @property
    def enabled(self):
        # Readability means stop or parent exit. Do not consume the stop byte.
        return self._enabled and not select.select([self._cancel_fd], [], [], 0)[0]

    @enabled.setter
    def enabled(self, value):
        self._enabled = bool(value)
