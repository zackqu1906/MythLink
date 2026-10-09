"""Bounded stroke collection off the BLE and Qt threads."""
from __future__ import annotations

from collections import deque
import threading
import time

from .stroke_input import StrokeCollector
from .touchpad_clicks import TouchpadClicks
from .touchpad_diagnostics import note


class TouchpadStrokeOutput:
    def __init__(self, *, connection, on_ready, on_end, on_stroke, on_tap, on_trace=None,
                 on_double_click=None, on_gesture=None, on_diagnostic=None):
        self.connection = connection
        self.on_ready, self.on_end = on_ready, on_end
        self.on_stroke, self.on_tap = on_stroke, on_tap
        self.on_trace = on_trace
        self.on_double_click = on_double_click
        self.on_diagnostic = on_diagnostic
        self.clicks = TouchpadClicks(on_gesture=on_gesture, on_diagnostic=on_diagnostic) if on_double_click else None
        self._trace_time = 0.
        self._trace_visible = False
        self.stopped = threading.Event()
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.events = deque(maxlen=512)
        self.overflowed = False
        self.active = False
        self.collector = StrokeCollector()
        self.pending_strokes = {}
        self.click_verdicts = {}
        self.resolved_steps = deque(maxlen=256)

    def _clear_pending(self):
        self.pending_strokes.clear()
        self.click_verdicts.clear()
        self.resolved_steps.clear()

    def _resolve_contacts(self):
        for end_step, (points, created) in list(self.pending_strokes.items()):
            if self.stopped.is_set() or self.connection.is_set():
                self._clear_pending()
                return
            if end_step in self.click_verdicts:
                is_click, _ = self.click_verdicts.pop(end_step)
                del self.pending_strokes[end_step]
                self.resolved_steps.append(end_step)
                if is_click:
                    note(self.on_diagnostic, 'stroke_contact_is_click')
                    if self.clicks is None: self.on_tap()
                elif points is not None:
                    self._publish_stroke(points)
                else:
                    note(self.on_diagnostic, 'stroke_invalid_geometry')
            elif time.monotonic() - created > .5:
                note(self.on_diagnostic, 'stroke_verdict_timeout')
                # Missing a decision is incomplete data, never permission to write.
                del self.pending_strokes[end_step]
        for end_step, (_, created) in list(self.click_verdicts.items()):
            if time.monotonic() - created > .5:
                del self.click_verdicts[end_step]

    def start(self):
        self.thread = threading.Thread(target=self._run, name="RingTouchpadStrokes", daemon=True)
        self.thread.start()

    def activate(self):
        self.active = True

    def submit(self, event):
        if self.stopped.is_set() or self.connection.is_set() or not self.active:
            return
        with self.lock:
            if len(self.events) == self.events.maxlen:
                self.overflowed = True
            self.events.append(event)
        self.wake.set()

    def stop(self):
        self.stopped.set()
        self.active = False
        self.wake.set()

    def _notify_trace(self, points, finished=False):
        if self.on_trace is None:
            return
        # Display-only copy. Never smooth or change the recognizer's trajectory.
        stride = max(1, (len(points)+199)//200)
        view = list(points[::stride])
        if points and view[-1] != points[-1]: view.append(points[-1])
        try:
            self.on_trace(view, finished)
        except Exception:
            pass  # A display callback must not interfere with stroke/tap delivery.
        self._trace_visible = bool(points)
        self._trace_time = time.monotonic()

    def _publish_stroke(self, points):
        note(self.on_diagnostic, 'stroke_delivered')
        self._notify_trace(points, True)
        self.on_stroke(points)

    def _run(self):
        reason, error = "", None
        try:
            self.on_ready()
            while not self.stopped.is_set():
                if self.connection.is_set():
                    reason = "设备已断开"
                    break
                self.wake.wait(.02)
                self.wake.clear()
                with self.lock:
                    events = list(self.events)
                    self.events.clear()
                    overflowed = self.overflowed
                    self.overflowed = False
                if overflowed:
                    note(self.on_diagnostic, 'stroke_queue_overflow')
                    self.collector.reset()
                    self._notify_trace([])
                    self._clear_pending()
                    if self.clicks: self.clicks.clear()
                # Confirmed edges carry their original (earlier) frame index.
                # Order this batch for the stroke consumer only; pointer and
                # click delivery in the SDK keep their original ordering.
                events.sort(key=lambda event: (event.step, {'contact': 0, 'move': 1, 'click': 2, 'click_verdict': 3}.get(event.kind, 4)))
                for event in events:
                    if self.stopped.is_set() or self.connection.is_set():
                        break
                    if time.monotonic() - event.timestamp > .15:
                        note(self.on_diagnostic, 'stroke_event_stale')
                        self.collector.reset()
                        self._notify_trace([])
                        self._clear_pending()
                        continue
                    if self.clicks:
                        for decision in self.clicks.feed(event):
                            if decision.kind == "double":
                                self.active = False
                                self.collector.reset()
                                self._clear_pending()
                                self.on_double_click()
                    if not self.active:
                        break
                    if event.kind in {"move", "contact"}:
                        if event.kind == 'contact' and event.state == 'reset':
                            self._notify_trace([])
                            self._clear_pending()
                        start_step = self.collector.start_step
                        last_step = self.collector.last_step
                        end_step = (event.step if event.kind == 'contact' and event.state == 'up'
                                    else self.collector.end_step)
                        points = self.collector.feed(event)
                        if (start_step is not None and self.collector.start_step is None
                                and event.kind == 'move' and last_step is not None and event.step != last_step + 1):
                            note(self.on_diagnostic, 'stroke_movement_gap')
                        if start_step is not None and end_step is not None and self.collector.start_step is None:
                            self.pending_strokes[end_step] = (points, time.monotonic())
                    elif event.kind in {"click", "click_verdict"}:
                        if event.step not in self.resolved_steps:
                            self.click_verdicts[event.step] = (
                                True if event.kind == 'click' else event.is_click, time.monotonic())
                self._resolve_contacts()
                if self.collector.touching and len(self.collector.points) > 1:
                    if time.monotonic()-self._trace_time >= .033:
                        self._notify_trace(self.collector.points)
                elif self._trace_visible and not self.pending_strokes:
                    # Completed ink fades in the UI; don't immediately erase it.
                    if self._trace_time and time.monotonic()-self._trace_time > .4:
                        self._notify_trace([])
        except Exception as exc:
            error = exc
        finally:
            self.stop()
            self._clear_pending()
            if self.clicks: self.clicks.clear()
            self.on_end(reason, error)
