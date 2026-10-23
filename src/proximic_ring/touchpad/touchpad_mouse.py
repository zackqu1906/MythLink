"""Bounded system-pointer output, isolated from BLE, inference and the Qt thread."""
from __future__ import annotations

from collections import deque
import threading
import time

from .touchpad_clicks import TouchpadClicks
from .touchpad_diagnostics import note
from .click_diagnostics import ClickTrace, start_click_diagnostics


class TouchpadMouseOutput:
    def __init__(self, *, connection, gain, clicks, invert_y, on_ready, on_end,
                 mouse_factory=None, on_double_click=None, on_gesture=None, on_diagnostic=None,
                 click_context=None, on_capture_click=None):
        self.connection = connection
        self.options = (gain, clicks, invert_y)
        self.on_ready, self.on_end = on_ready, on_end
        self.mouse_factory = mouse_factory
        self.on_double_click = on_double_click
        self.on_diagnostic = on_diagnostic
        self.click_context = click_context or (lambda: (0, 'closed'))
        self.on_capture_click = on_capture_click
        self._last_click_context = None
        self.clicks = TouchpadClicks(on_gesture=on_gesture, on_diagnostic=on_diagnostic) if on_double_click else None
        self.paused = False
        self.mouse = None
        self.stopped = threading.Event()
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.events = deque(maxlen=32)
        self.overflowed = False
        self.active = False
        self._pending_clicks = {}
        start_click_diagnostics()

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
        trace = ClickTrace(event) if event.kind == 'click' else None
        blocked = self._blocked_reason()
        if blocked:
            if trace: trace.finish(blocked)
            return
        with self.lock:
            blocked = self._blocked_reason()
            if blocked:
                if trace: trace.finish(blocked)
                return
            if len(self.events) == self.events.maxlen:
                self.overflowed = True
                evicted = self.events[0][2]
                if evicted:
                    evicted.finish('queue_overflow')
                    self._pending_clicks.pop(evicted.fields['click_id'], None)
            # Snapshot only for clicks: pointer movement keeps its fast path.
            self.events.append((event, self.click_context() if trace else None, trace))
            if trace:
                self._pending_clicks[trace.fields['click_id']] = trace
                trace.record('queued', queue_depth=len(self.events))
        self.wake.set()

    def _blocked_reason(self):
        if self.connection.is_set(): return 'disconnected'
        if self.stopped.is_set(): return 'output_stopped'
        if not self.active: return 'output_inactive'
        if self.paused: return 'output_paused'
        return ''

    def _finish_click(self, trace, reason, **fields):
        if trace is None:
            return
        trace.finish(reason, **fields)
        with self.lock:
            self._pending_clicks.pop(trace.fields['click_id'], None)

    def _recognize_click(self, event, trace, *, pair=True):
        decisions = self.clicks.feed(event, pair=pair)
        if not decisions:
            self._finish_click(trace, self.clicks.last_rejection or 'click_rejected',
                               recognition_age_ms=round((time.monotonic()-event.timestamp)*1000, 3),
                               recognition_limit_ms=round(self.clicks.max_lateness*1000))
        else:
            trace.fields['decision'] = decisions[0].kind
            trace.record('recognized', pair_enabled=pair)
        return decisions

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
                from ..platforms.macos.native_touchpad import NativeTouchpadMouse
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
                for event, submitted_context, trace in events:
                    if trace:
                        trace.dequeued(queue_depth=len(events))
                    if self.paused or now - event.timestamp > .1:
                        note(self.on_diagnostic, 'mouse_paused' if self.paused else 'mouse_event_stale')
                        self._finish_click(trace, 'output_paused' if self.paused else 'mouse_queue_expired',
                                           queue_limit_ms=100)
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
                        context = self.click_context()
                        if context != self._last_click_context:
                            if self.clicks: self.clicks.clear_pair()
                            self._last_click_context = context
                        if submitted_context != context or context[1] in {'loading', 'activating'}:
                            note(self.on_diagnostic, 'click_context_changed')
                            self._finish_click(trace, 'click_context_changed' if submitted_context != context
                                               else 'selector_' + context[1])
                            continue
                        if context[1] == 'capture':
                            # The screenshot canvas owns the tap, even when
                            # normal mouse clicks are disabled. Flush motion
                            # first, then queue a canvas action; never click an
                            # underlying application or pair into handwriting.
                            if batch:
                                self._apply(batch)
                                batch = []
                            if self.clicks:
                                self.clicks.clear_pair()
                                # Keep duplicate/reset protection; capture taps
                                # are immediate singles, never handwriting.
                                if not self._recognize_click(event, trace, pair=False): continue
                            if self.on_capture_click and self.click_context() == context:
                                self.on_capture_click(context)
                                self._finish_click(trace, 'capture_callback_requested', outcome='routed')
                            else:
                                self._finish_click(trace, 'click_context_changed' if self.click_context() != context
                                                   else 'capture_callback_missing')
                            continue
                        pair = context[1] == 'closed'
                        if self.clicks is None:
                            if batch:
                                self._apply(batch)
                                batch = []
                            if self.options[1]:
                                self._send_click(trace)
                            else:
                                self._finish_click(trace, 'clicks_disabled')
                        elif self.options[1]:
                            # Deliver prior motion before the immediate click.
                            if batch:
                                self._apply(batch)
                                batch = []
                            self._deliver_clicks(self._recognize_click(event, trace, pair=pair), context, trace)
                        else:
                            # Recognition hints remain available with mouse
                            # click output disabled; no native action is sent.
                            if self._recognize_click(event, trace, pair=pair):
                                self._finish_click(trace, 'clicks_disabled')
                if batch:
                    self._apply(batch)
        except Exception as exc:
            error = exc
        finally:
            self.stop()
            with self.lock:
                pending = list(self._pending_clicks.values())
                self._pending_clicks.clear()
                self.events.clear()
            for trace in pending:
                diagnostic = getattr(self.mouse, 'last_diagnostic', {}) if error else {}
                requested = trace.fields.get('native_click_requested', False)
                receipt = diagnostic.get('click_receipt', {}) if requested else {}
                # A broken IPC reply cannot prove that a click was not posted.
                unknown = requested and not receipt and diagnostic.get('delivery') != 'not_sent'
                details = dict(clicks_posted=None if unknown else 0,
                               down_posts=None if unknown else 0, up_posts=None if unknown else 0,
                               error_type=type(error).__name__ if error else None)
                details.update({key: value for key, value in receipt.items() if key != 'reason'})
                trace.finish(receipt.get('reason') or diagnostic.get('reason') or
                             ('pointer_worker_error' if error else self._blocked_reason()),
                             outcome='unknown' if unknown else 'failed' if error else 'discarded',
                             **details)
            if self.mouse is not None and hasattr(self.mouse, "close"):
                try:
                    self.mouse.close()  # Worker thread only; stop() remains nonblocking.
                except Exception as exc:
                    error = error or exc
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
        return result

    def _send_click(self, trace):
        blocked = self._blocked_reason()
        if blocked:
            self._finish_click(trace, blocked)
            return
        trace.fields['native_click_requested'] = True
        trace.record('dispatch')
        apply_click = getattr(self.mouse, 'apply_click', None)
        result = (apply_click(dict(trace.fields)) if apply_click is not None
                  else self._apply([dict(kind="click")]))
        result = result if isinstance(result, dict) else {}
        posted = result.get('clicks_posted')
        why = result.get('reason', 'missing_native_receipt')
        details = dict(clicks_posted=None, down_posts=None, up_posts=None)
        details.update({key: value for key, value in result.items() if key != 'reason'})
        self._finish_click(trace, why, outcome='unknown' if posted is None else 'posted' if posted else 'discarded',
                           **details)

    def _deliver_clicks(self, decisions, context, trace):
        for decision in decisions:
            if self.stopped.is_set() or self.connection.is_set():
                self._finish_click(trace, self._blocked_reason())
                return
            if self.click_context() != context:
                self.clicks.clear_pair()
                note(self.on_diagnostic, 'click_context_changed')
                self._finish_click(trace, 'click_context_changed')
                return
            if not self.options[1]:
                self._finish_click(trace, 'clicks_disabled')
                continue
            if decision.kind == "single":
                self._send_click(trace)
            else:
                trace.record('double_target_request')
                resolve = getattr(self.mouse, 'resolve_double_click', None)
                receipt = resolve(dict(trace.fields)) if resolve else {}
                target = receipt.get('target') if resolve else self.mouse.double_click_target()
                details = {key: value for key, value in receipt.items() if key not in {'reason', 'target'}}
                if self.stopped.is_set() or self.connection.is_set():
                    self._finish_click(trace, self._blocked_reason(), **details)
                    return
                if target and self.click_context() == context:
                    self.paused = True
                    self.on_double_click(target)
                    self._finish_click(trace, 'double_click_stroke_requested', outcome='routed', **details)
                else:
                    self._finish_click(trace, 'click_context_changed' if self.click_context() != context
                                       else receipt.get('reason', 'double_target_unavailable'), **details)
