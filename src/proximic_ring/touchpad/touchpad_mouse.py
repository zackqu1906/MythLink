"""Bounded system-pointer output, isolated from BLE, inference and the Qt thread."""
from __future__ import annotations

from collections import deque
import threading
import time
import sys

from .touchpad_clicks import TouchpadClicks
from .touchpad_diagnostics import note
from .click_diagnostics import ClickTrace, start_click_diagnostics


class TouchpadMouseOutput:
    def __init__(self, *, connection, gain, clicks, invert_y, on_ready, on_end,
                 mouse_factory=None, on_double_click=None, on_gesture=None, on_diagnostic=None,
                 click_context=None, on_capture_click=None, scroll=True, double_click=True,
                 right_click=True, scroll_gain=1., raw2=False):
        self.connection = connection
        self.options = (gain, clicks, invert_y)
        self.on_ready, self.on_end = on_ready, on_end
        self.mouse_factory = mouse_factory
        self.on_double_click = on_double_click
        self.on_gesture = on_gesture
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
        self._features = (bool(clicks), bool(double_click), bool(right_click), bool(scroll), float(scroll_gain))
        self.raw2 = raw2
        self._stream_key = None
        self._seen_clicks = deque(maxlen=256)
        self._last_scroll_hint = None
        start_click_diagnostics()

    def start(self):
        self.thread = threading.Thread(target=self._run, name="RingTouchpadMouse", daemon=True)
        self.thread.start()

    def activate(self):
        self.active = True
        self.paused = False

    def configure(self, *, clicks, double_click, right_click, scroll, scroll_gain):
        # One immutable snapshot; the pointer worker reads it before dispatch.
        self._features = (bool(clicks), bool(double_click), bool(right_click), bool(scroll), float(scroll_gain))
        self.options = (self.options[0], bool(clicks), self.options[2])

    def _reset_stream(self, event):
        with self.lock:
            self._stream_key = (event.session_id, event.generation)
            self._seen_clicks.clear()
            for _, _, trace in self.events:
                if trace:
                    trace.finish('session_reset')
                    self._pending_clicks.pop(trace.fields['click_id'], None)
            self.events.clear()
        if self.clicks:
            self.clicks.clear()

    def submit(self, event):
        # Called on BLE: no Quartz, Qt signals, model work or blocking queue puts.
        if event.kind == 'contact' and event.state == 'reset' and getattr(event, 'session_id', None) is not None:
            self._reset_stream(event)
            return
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
                if getattr(event, 'session_id', None) is not None:
                    # A lost Raw2 event must not turn a partial gesture into input.
                    self.stopped.set()
                    self.active = False
                    if trace: trace.finish('queue_overflow')
                    self.wake.set()
                    return
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
                if sys.platform == 'win32':
                    from ring_python_sdk.touchpad.windows import WindowsSystemMouse
                    factory = WindowsSystemMouse
                else:
                    from ..platforms.macos.native_touchpad import NativeTouchpadMouse
                    factory = NativeTouchpadMouse
            self.mouse = factory()
            self.mouse.gain, self.mouse.clicks_enabled, self.mouse.invert_y = self.options
            # Raw2's independent button switches are checked at dispatch.
            # The native output must allow right/double clicks even if singles are off.
            if self.raw2:
                self.mouse.clicks_enabled = True
            self.mouse.stop_on_escape = sys.platform == 'win32' and hasattr(self.mouse, 'check_health')
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
                    if getattr(event, 'session_id', None) is not None:
                        if trace and batch:
                            self._apply(batch)
                            batch = []
                        value = self._raw2_event(event, submitted_context, trace)
                        if value:
                            if batch and batch[-1]['kind'] == value['kind'] and batch[-1].get('stream') == value['stream']:
                                for axis in ('dx', 'dy'):
                                    if axis in value:
                                        batch[-1][axis] += value[axis]
                                batch[-1]['expires_at'] = min(batch[-1]['expires_at'], value['expires_at'])
                            else:
                                batch.append(value)
                        continue
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
        # Recheck after coalescing: reset or a live toggle can occur while the
        # worker is processing its local batch. Legacy events have no stream.
        batch = [e for e in batch if 'stream' not in e or
                 (tuple(e['stream']) == self._stream_key and
                  (e['kind'] != 'scroll' or self._features[3]))]
        if not batch or self._blocked_reason():
            return {}
        result = self.mouse.apply(batch)
        # Count actual Quartz posts reported by the child, not SDK frames or
        # requests (zero movement and expired IPC requests post nothing).
        if isinstance(result, dict):
            for _ in range(result.get('moves', 0)):
                note(self.on_diagnostic, 'pointer_post')
            for _ in range(result.get('scrolls', 0)):
                note(self.on_diagnostic, 'scroll_post')
        return result

    def _raw2_event(self, event, submitted_context, trace):
        key = (event.session_id, event.generation)
        if self._stream_key is None:
            self._stream_key = key
        blocked = self._blocked_reason()
        if blocked or key != self._stream_key:
            self._finish_click(trace, blocked or 'session_reset')
            return
        limit = .25 if trace else .2
        now = time.monotonic()
        age = now - event.timestamp
        if not 0 <= age <= limit:
            self._finish_click(trace, 'mouse_queue_expired', queue_limit_ms=limit*1000)
            return
        singles, doubles, right, scroll, gain = self._features
        if trace:
            click_id = (key, event.click_id)
            if click_id in self._seen_clicks:
                self._finish_click(trace, 'duplicate_click')
                return
            self._seen_clicks.append(click_id)
            context = self.click_context()
            if context != submitted_context or context[1] in {'loading', 'activating'}:
                self._finish_click(trace, 'click_context_changed')
                return
            if context[1] == 'capture':
                if event.button == 'left' and self.on_capture_click:
                    self.on_capture_click(context)
                    self._finish_click(trace, 'capture_callback_requested', outcome='routed')
                else:
                    self._finish_click(trace, 'capture_owns_input')
                return
            enabled = (singles if event.count == 1 else doubles) if event.button == 'left' else right
            if event.button not in {'left', 'right'} or event.count not in {1, 2}:
                self._finish_click(trace, 'invalid_click')
            elif not enabled:
                self._finish_click(trace, 'clicks_disabled')
            else:
                self._notify_gesture(event.button + ('-double-click' if event.count == 2 else '-click'))
                self._send_click(trace, event)
        elif event.mode == 'TWO_FINGER':
            if scroll:
                # Movement arrives at sensor rate. Keep recognition feedback
                # to one card per second per stream, independent of OS receipts.
                last = self._last_scroll_hint
                if event.dy * gain and (last is None or last[0] != key or now - last[1] >= 1.):
                    self._last_scroll_hint = (key, now)
                    self._notify_gesture('two-finger-scroll')
                return dict(kind='scroll', dy=-event.dy*gain,
                            stream=list(key), expires_at=event.timestamp+.2)
        else:
            return dict(kind='move', dx=event.dx, dy=event.dy,
                        stream=list(key), expires_at=event.timestamp+.2)

    def _notify_gesture(self, name):
        if self.on_gesture is not None:
            try:
                self.on_gesture(name)
            except Exception:
                pass  # Optional feedback cannot interrupt pointer output.

    def _send_click(self, trace, event=None):
        blocked = self._blocked_reason()
        if event is not None and (event.session_id, event.generation) != self._stream_key:
            blocked = 'session_reset'
        if blocked:
            self._finish_click(trace, blocked)
            return
        trace.fields['native_click_requested'] = True
        trace.record('dispatch')
        apply_click = getattr(self.mouse, 'apply_click', None)
        params = (dict(button=event.button, count=event.count, click_id=f'{event.session_id}:{event.generation}:{event.click_id}',
                       expires_at=event.timestamp+.25) if event is not None else {})
        result = (apply_click(dict(trace.fields), **params) if apply_click is not None
                  else self._apply([dict(kind="click", **params)]))
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
                    if sys.platform == 'win32' and not target and self.click_context() == context:
                        self._send_click(trace)
                        continue
                    self._finish_click(trace, 'click_context_changed' if self.click_context() != context
                                       else receipt.get('reason', 'double_target_unavailable'), **details)
