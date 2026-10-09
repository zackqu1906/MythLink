"""Bounded system-pointer output, isolated from BLE, inference and the Qt thread."""
from __future__ import annotations

from collections import deque
import threading
import time

from .touchpad_clicks import TouchpadClicks
from .touchpad_diagnostics import note


class TouchpadMouseOutput:
    def __init__(self, *, connection, gain, clicks, invert_y, on_ready, on_end,
                 mouse_factory=None, on_double_click=None, on_gesture=None, on_diagnostic=None):
        self.connection = connection
        self.options = (gain, clicks, invert_y)
        self.on_ready, self.on_end = on_ready, on_end
        self.mouse_factory = mouse_factory
        self.on_double_click = on_double_click
        self.on_diagnostic = on_diagnostic
        self.clicks = TouchpadClicks(on_gesture=on_gesture, on_diagnostic=on_diagnostic) if on_double_click else None
        self.paused = False
        self.mouse = None
        self.stopped = threading.Event()
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.events = deque(maxlen=32)
        self.overflowed = False
        self.active = False

    def start(self):
        self.thread = threading.Thread(target=self._run, name="RingTouchpadMouse", daemon=True)
        self.thread.start()

    def activate(self):
        self.active = True
        self.paused = False

    def submit(self, event):
        # Called on BLE: no Quartz, Qt signals, model work or blocking queue puts.
        if event.kind not in {"move", "click"} and not (
                self.clicks and event.kind == "contact" and event.state == "reset"):
            return  # Stroke metadata must not occupy the pointer's bounded queue.
        if self.stopped.is_set() or self.connection.is_set() or not self.active or self.paused:
            return
        with self.lock:
            if len(self.events) == self.events.maxlen:
                self.overflowed = True
            self.events.append(event)
        self.wake.set()

    def stop(self):
        self.stopped.set()
        self.active = False
        if self.mouse is not None:
            self.mouse.disable()
        self.wake.set()

    def _run(self):
        reason, error = "", None
        try:
            factory = self.mouse_factory
            if factory is None:
                from .native_touchpad import NativeTouchpadMouse
                factory = NativeTouchpadMouse
            self.mouse = factory()
            self.mouse.gain, self.mouse.clicks_enabled, self.mouse.invert_y = self.options
            self.mouse.stop_on_escape = False
            if self.stopped.is_set() or self.connection.is_set():
                return
            self.mouse.enable()
            self.on_ready()
            check_health = getattr(self.mouse, "check_health", None)
            while not self.stopped.is_set():
                if self.connection.is_set():
                    reason = "设备已断开"
                    break
                self.wake.wait(.02)
                self.wake.clear()
                with self.lock:
                    events = list(self.events)
                    self.events.clear()
                    overflowed, self.overflowed = self.overflowed, False
                if overflowed and self.clicks:
                    note(self.on_diagnostic, 'mouse_queue_overflow')
                    self.clicks.clear()
                if self.stopped.is_set() or self.connection.is_set():
                    break
                if check_health is not None:
                    check_health()
                now = time.monotonic()
                batch = []
                for event in events:
                    if self.paused or now - event.timestamp > .1:
                        note(self.on_diagnostic, 'mouse_paused' if self.paused else 'mouse_event_stale')
                        continue
                    if event.kind == "contact":
                        self.clicks.feed(event)
                    elif event.kind == "move":
                        # Combine adjacent moves only; never reorder a click.
                        if batch and batch[-1]["kind"] == "move":
                            batch[-1]["dx"] += event.dx
                            batch[-1]["dy"] += event.dy
                        else:
                            batch.append(dict(kind="move", dx=event.dx, dy=event.dy))
                    elif event.kind == "click":
                        if self.clicks is None:
                            batch.append(dict(kind="click"))
                        elif self.options[1]:
                            # Deliver prior motion before the immediate click.
                            if batch:
                                self._apply(batch)
                                batch = []
                            self._deliver_clicks(self.clicks.feed(event))
                        else:
                            # Recognition hints remain available with mouse
                            # click output disabled; no native action is sent.
                            self.clicks.feed(event)
                if batch:
                    self._apply(batch)
        except Exception as exc:
            error = exc
        finally:
            self.stop()
            if self.mouse is not None and hasattr(self.mouse, "close"):
                try:
                    self.mouse.close()  # Worker thread only; stop() remains nonblocking.
                except Exception as exc:
                    error = error or exc
            with self.lock:
                self.events.clear()
            if self.clicks:
                self.clicks.clear()
            self.on_end(reason, error)

    def _apply(self, batch):
        result = self.mouse.apply(batch)
        # Count actual Quartz posts reported by the child, not SDK frames or
        # requests (zero movement and expired IPC requests post nothing).
        if isinstance(result, dict):
            for _ in range(result.get('moves', 0)):
                note(self.on_diagnostic, 'pointer_post')

    def _deliver_clicks(self, decisions):
        for decision in decisions:
            if self.stopped.is_set() or self.connection.is_set():
                return
            if not self.options[1]:
                continue
            if decision.kind == "single":
                self._apply([dict(kind="click")])
            else:
                target = self.mouse.double_click_target()
                if target:
                    self.paused = True
                    self.on_double_click(target)
