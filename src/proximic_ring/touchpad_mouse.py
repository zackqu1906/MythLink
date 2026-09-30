"""Bounded system-pointer output, isolated from BLE, inference and the Qt thread."""
from __future__ import annotations

from collections import deque
import threading
import time


class TouchpadMouseOutput:
    def __init__(self, *, connection, gain, clicks, invert_y, on_ready, on_end,
                 mouse_factory=None):
        self.connection = connection
        self.options = (gain, clicks, invert_y)
        self.on_ready, self.on_end = on_ready, on_end
        self.mouse_factory = mouse_factory
        self.mouse = None
        self.stopped = threading.Event()
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.events = deque(maxlen=32)
        self.active = False
        self.deadline = None

    def start(self):
        self.thread = threading.Thread(target=self._run, name="RingTouchpadMouse", daemon=True)
        self.thread.start()

    def activate(self, seconds):
        self.deadline = time.monotonic() + seconds if seconds else None
        self.active = True

    def submit(self, event):
        # Called on BLE: no Quartz, Qt signals, model work or blocking queue puts.
        if self.stopped.is_set() or self.connection.is_set() or not self.active:
            return
        with self.lock:
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
                from ring_python_sdk.touchpad.macos import MacSystemMouse
                factory = MacSystemMouse
            self.mouse = factory()
            self.mouse.gain, self.mouse.clicks_enabled, self.mouse.invert_y = self.options
            if self.stopped.is_set() or self.connection.is_set():
                return
            self.mouse.enable()
            self.on_ready()
            while not self.stopped.is_set():
                if self.connection.is_set():
                    reason = "设备已断开"
                    break
                if self.mouse.escape_pressed():
                    reason = "已通过 Esc 停止"
                    break
                if self.deadline is not None and time.monotonic() >= self.deadline:
                    reason = "已自动停止"
                    break
                self.wake.wait(.02)
                self.wake.clear()
                with self.lock:
                    events = list(self.events)
                    self.events.clear()
                if self.stopped.is_set() or self.connection.is_set():
                    break
                now = time.monotonic()
                batch = []
                for event in events:
                    if now - event.timestamp > .1:
                        continue
                    if event.kind == "move":
                        # Combine adjacent moves only; never reorder a click.
                        if batch and batch[-1]["kind"] == "move":
                            batch[-1]["dx"] += event.dx
                            batch[-1]["dy"] += event.dy
                        else:
                            batch.append(dict(kind="move", dx=event.dx, dy=event.dy))
                    elif event.kind == "click":
                        batch.append(dict(kind="click"))
                if batch:
                    self.mouse.apply(batch)
        except InterruptedError:
            reason = "已通过 Esc 停止"
        except Exception as exc:
            error = exc
        finally:
            self.stop()
            with self.lock:
                self.events.clear()
            self.on_end(reason, error)
