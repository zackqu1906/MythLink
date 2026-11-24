"""Keep Raw2 mode-switch gestures alive without creating a system mouse."""
from __future__ import annotations

import time


class TouchpadModeSwitchOutput:
    """Voice-mode sink: observe final single-finger double clicks only.

    The SDK still owns contact classification and pairing. This sink never
    instantiates a native mouse or posts moves, clicks, scrolling or strokes.
    """

    def __init__(self, *, connection, on_ready, on_mode_switch, ignored_click=None, **_):
        self.connection = connection
        self.on_ready = on_ready
        self.on_mode_switch = on_mode_switch
        self.active = False
        self.stopped = False
        self.input_since = time.monotonic()
        self._requested = False
        self.ignored_click = ignored_click

    def start(self):
        if not self.stopped and not self.connection.is_set():
            self.on_ready()

    def activate(self):
        if not self.stopped:
            self.active = True

    def stop(self):
        self.stopped = True
        self.active = False

    def submit(self, event):
        if (not self.active or self.stopped or self.connection.is_set() or self._requested
                or event.kind != 'click' or getattr(event, 'button', None) != 'left'
                or getattr(event, 'count', 0) != 2 or getattr(event, 'is_preview', False)):
            return
        if event.timestamp < self.input_since or not 0 <= time.monotonic() - event.timestamp <= .25:
            return
        if (event.session_id, event.generation, event.click_id) == self.ignored_click:
            return
        # Queue one transition. A repeated packet cannot toggle back while Qt
        # is still replacing this sink with the pointer worker.
        self._requested = True
        self.on_mode_switch(event)
