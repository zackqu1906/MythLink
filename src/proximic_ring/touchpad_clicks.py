"""Emit the first SDK click immediately, then pair a second as a double click."""
from __future__ import annotations

from dataclasses import dataclass
import time
from .touchpad_diagnostics import note


@dataclass(frozen=True)
class ClickDecision:
    kind: str
    first: object
    second: object | None = None


class TouchpadClicks:
    """Owned by one output worker; no timers or OS events on the BLE thread.

    The first click emits a single immediately. A second within 400 ms emits
    a double; it never retracts or replays the first single. Each pair ends
    after its double, so a third click starts a new pair. There is no timer
    or deferred delivery. Call ``clear`` at output/connection boundaries.
    """

    interval = .400
    max_lateness = .150

    def __init__(self, *, on_gesture=None, on_diagnostic=None):
        self._first_click = None
        self._last_click = None
        self._on_gesture = on_gesture
        self._on_diagnostic = on_diagnostic

    def _notify(self, name):
        if self._on_gesture is not None:
            try:
                self._on_gesture(name)
            except Exception:
                pass  # Optional recognition feedback cannot interrupt input.

    def clear(self):
        self._first_click = None
        self._last_click = None

    def feed(self, event, *, now=None):
        now = time.monotonic() if now is None else now
        if event.kind == "contact" and event.state == "reset":
            # The processor emits step=0 when the whole token/model stream
            # resets. A nonzero step only retires an expired movement frame;
            # it cannot invalidate a click the SDK has already confirmed.
            if event.step == 0:
                self.clear()
            return []
        if event.kind != "click":
            return []
        # A delayed/duplicate sensor callback cannot start a fresh gesture.
        last = self._last_click
        if (not 0 <= now - event.timestamp <= self.max_lateness
                or (last is not None and (event.step <= last.step or event.timestamp < last.timestamp))):
            note(self._on_diagnostic, 'click_stale_or_duplicate')
            return []
        self._last_click = event
        first = self._first_click
        if first is not None and event.timestamp - first.timestamp <= self.interval + 1e-9:
            self._first_click = None
            self._notify("double-click")
            return [ClickDecision("double", first, event)]
        self._first_click = event
        self._notify("click")
        return [ClickDecision("single", event)]
