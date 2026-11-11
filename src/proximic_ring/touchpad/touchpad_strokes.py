"""Bounded stroke collection off the BLE and Qt threads."""
from __future__ import annotations

from collections import deque
import threading
import time
import uuid

from .stroke.stroke_input import StrokeCollector
from .touchpad_clicks import TouchpadClicks
from .touchpad_diagnostics import note
from .click_diagnostics import ClickTrace, start_click_diagnostics
from .stroke.stroke_kinematics import measure_stroke
from .stroke.stroke_hold_selection import hold_selection


class TouchpadStrokeOutput:
    def __init__(self, *, connection, on_ready, on_end, on_stroke, on_tap, on_trace=None,
                 on_double_click=None, on_gesture=None, on_diagnostic=None, on_tap_contact=None,
                 on_stroke_details=None, on_selection=None, on_selection_state=None,
                 contact_classifier=None, on_contact_intent=None, two_finger_classifier=None):
        self.connection = connection
        self.on_ready, self.on_end = on_ready, on_end
        self.on_stroke, self.on_tap = on_stroke, on_tap
        self.on_trace = on_trace
        self.on_tap_contact = on_tap_contact
        self.on_stroke_details = on_stroke_details
        self.on_selection = on_selection
        self.on_selection_state = on_selection_state
        self.contact_classifier = contact_classifier
        # Optional: Raw2 pointer frames tag two-finger contacts, which this
        # classifier turns into menu selections instead of ink or taps.
        self.two_finger_classifier = two_finger_classifier
        self.pointer_events = deque(maxlen=512)
        self.two_finger_steps = deque(maxlen=4096)
        self._two_finger_live = False
        self.on_contact_intent = on_contact_intent
        self._selection_time = 0.
        self._selection_state = None
        self._selection_two = False
        self.on_double_click = on_double_click
        self.on_diagnostic = on_diagnostic
        self.clicks = TouchpadClicks(on_gesture=on_gesture, on_diagnostic=on_diagnostic) if on_double_click and contact_classifier is None else None
        # The opt-in contact policy owns single taps (candidate selection); it
        # only borrows SDK click pairing to recognize a double click.
        self.double_clicks = (TouchpadClicks(on_gesture=on_gesture, on_diagnostic=on_diagnostic)
                              if on_double_click and contact_classifier is not None else None)
        self._trace_time = 0.
        self._trace_visible = False
        self.stopped = threading.Event()
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.events = deque(maxlen=512)
        self._reset_epoch = self._batch_epoch = 0
        self.overflowed = False
        self.active = False
        self.collector = StrokeCollector()
        self.pending_strokes = {}
        self.pending_contact_details = {}
        self.click_verdicts = {}
        self.resolved_steps = deque(maxlen=256)
        start_click_diagnostics()
        self._stream_id = uuid.uuid4().hex
        self._contact_epoch = 0
        # Optional foreground gate. A new monotonic cutoff starts a fresh
        # input session; None pauses it. Ordinary input has no gate.
        self.input_since = None
        self.input_epoch = None

    def _clear_pending(self):
        self._contact_epoch += 1
        self.two_finger_steps.clear()
        self._two_finger_live = self._selection_two = False
        self.pending_strokes.clear()
        self.pending_contact_details.clear()
        self.click_verdicts.clear()
        self.resolved_steps.clear()
        self._notify_selection_state([])

    def _two_finger(self, start_step, end_step=None):
        """Did Raw2 classify any frame of this contact as two-finger?"""
        if self.two_finger_classifier is None or start_step is None:
            return False
        return any(step >= start_step and (end_step is None or step < end_step)
                   for step in self.two_finger_steps)

    def _notify_selection_state(self, points, two_finger=False):
        if self.on_selection_state is None:
            return
        state = (self.two_finger_classifier(points) if two_finger and points
                 else (self.contact_classifier or hold_selection)(points))
        if state != self._selection_state and self._batch_epoch == self._reset_epoch:
            self._selection_state = state
            self.on_selection_state(state)
        self._selection_time = time.monotonic()

    def _resolve_contacts(self):
        for end_step, (points, created) in list(self.pending_strokes.items()):
            if self.stopped.is_set() or self.connection.is_set() or self._batch_epoch != self._reset_epoch:
                self._clear_pending()
                return
            if end_step in self.click_verdicts:
                is_click, _ = self.click_verdicts.pop(end_step)
                del self.pending_strokes[end_step]
                contact = self.pending_contact_details.pop(end_step, {})
                self.resolved_steps.append(end_step)
                contact_size = len(contact.get('points', []))
                if self.on_selection is not None and self._two_finger(contact.get('start_step'), end_step):
                    # A two-finger contact never writes or taps; it selects.
                    decision = self.two_finger_classifier(contact.get('points') or [], finished=True)
                    value = dict(contact, end_step=end_step, sdk_is_click=is_click, intent_decision=decision)
                    if self.on_contact_intent is not None:
                        self.on_contact_intent({k: v for k, v in value.items() if k != 'points'})
                    self._notify_trace([], True)
                    self.on_selection(dict(value, decision=decision))
                    continue
                if self.contact_classifier is not None and self.clicks is None:
                    # Opt-in application policy consumes every complete contact,
                    # including SDK non-clicks. Shared SDK/mouse rules stay intact.
                    complete = (contact.get('start_step') is not None and contact_size > 0
                                and contact_size == end_step-contact['start_step']
                                and contact_size <= self.collector.max_points
                                and contact.get('complete', False))
                    decision = (self.contact_classifier(contact['points'], finished=True) if complete else
                                dict(phase='idle', progress=0, direction=None,
                                     action='reject', reason='incomplete-contact'))
                    value = dict(contact, end_step=end_step, sdk_is_click=is_click,
                                 intent_decision=decision)
                    if self.on_contact_intent is not None:
                        self._emit_current(self.on_contact_intent, {k: v for k, v in value.items() if k != 'points'})
                    action = decision['action']
                    if action == 'stroke' and points is not None:
                        self._publish_stroke(points, contact)
                    elif action == 'tap':
                        if self.on_tap_contact is not None:
                            self._notify_trace(contact['points'], True)
                            self._emit_current(self.on_tap_contact, value)
                            self._trace_visible = False
                        else:
                            self._notify_trace([], True)
                            self._emit_current(self.on_tap)
                    elif action in {'select', 'page', 'punctuation', 'cancel-selection'} and self.on_selection is not None:
                        self._notify_trace([], True)
                        self._emit_current(self.on_selection, dict(value, decision=decision))
                    elif self.on_tap_contact is not None:
                        # A rejected contact remains visible and gets a reason;
                        # it cannot silently commit or become handwriting.
                        value['intent_decision'] = dict(decision, action='reject')
                        self._notify_trace(contact.get('points', []), True)
                        self._emit_current(self.on_tap_contact, value)
                        self._trace_visible = False
                    continue
                if (self.on_selection is not None and contact.get('start_step') is not None
                        and contact_size == end_step - contact['start_step']
                        and contact_size <= self.collector.max_points):
                    selection = hold_selection(contact.get('points', []), finished=True)
                    if 'action' in selection:
                        self._notify_trace([], True)
                        self._emit_current(self.on_selection, dict(contact, decision=selection, end_step=end_step))
                        continue
                if is_click:
                    note(self.on_diagnostic, 'stroke_contact_is_click')
                    if self.clicks is None and self.on_tap_contact is not None:
                        # The opt-in trial receives the actual collected geometry
                        # for confirmation arbitration. SDK verdict stays intact;
                        # this contact is never silently published as handwriting.
                        tap_points = contact.pop('points', points or [])
                        self._notify_trace(tap_points, True)
                        self._emit_current(self.on_tap_contact, dict(contact, points=tap_points, end_step=end_step,
                                                                     geometry_valid=points is not None))
                        # The opt-in UI owns clearing accepted ink and retaining
                        # rejected ink; don't erase it on the idle fade below.
                        self._trace_visible = False
                        continue
                    # A tap may have produced a live preview while awaiting the
                    # SDK verdict. Clear it before delivering the tap action;
                    # only a negative click verdict publishes finished ink.
                    self._notify_trace([], True)
                    if self.clicks is None: self._emit_current(self.on_tap)
                elif points is not None:
                    self._publish_stroke(points, contact)
                else:
                    note(self.on_diagnostic, 'stroke_invalid_geometry')
            elif time.monotonic() - created > .5:
                note(self.on_diagnostic, 'stroke_verdict_timeout')
                # Missing a decision is incomplete data, never permission to write.
                del self.pending_strokes[end_step]
                self.pending_contact_details.pop(end_step, None)
        for end_step, (_, created) in list(self.click_verdicts.items()):
            if time.monotonic() - created > .5:
                del self.click_verdicts[end_step]

    def start(self):
        self.thread = threading.Thread(target=self._run, name="RingTouchpadStrokes", daemon=True)
        self.thread.start()

    def activate(self):
        self.active = True

    def submit_pointer(self, event):
        """Raw2 pointer frames: only their two-finger mode tag is used."""
        if (self.two_finger_classifier is None or self.stopped.is_set() or not self.active
                or str(getattr(getattr(event, 'mode', ''), 'value', getattr(event, 'mode', ''))).upper() != 'TWO_FINGER'):
            return
        with self.lock:
            self.pointer_events.append(event.step)
        self.wake.set()

    def submit(self, event):
        trace = ClickTrace(event, mode='stroke') if event.kind == 'click' else None
        if self.stopped.is_set() or self.connection.is_set() or not self.active:
            if trace: trace.finish(self._click_stop_reason())
            return
        reset = event.kind == 'contact' and event.state == 'reset' and getattr(event, 'session_id', None) is not None
        if self.input_since is not None and not reset:
            since = self.input_since()
            if since is None or event.timestamp < since:
                return
        with self.lock:
            if self.stopped.is_set() or self.connection.is_set() or not self.active:
                if trace: trace.finish(self._click_stop_reason())
                return
            if reset:
                # Raw2 may restart frame numbering or interrupt the same generation.
                # Invalidate both the pending queue and the worker's detached batch.
                self._reset_epoch += 1
                for _, pending in self.events:
                    if pending: pending.finish('session_reset')
                self.events.clear()
                self.pointer_events.clear()
                self.wake.set()
                return
            if len(self.events) == self.events.maxlen:
                self.overflowed = True
                evicted = self.events[0][1]
                if evicted: evicted.finish('stroke_queue_overflow')
            self.events.append((event, trace))
            if trace: trace.record('queued', queue_depth=len(self.events))
        self.wake.set()

    def _click_stop_reason(self):
        if self.connection.is_set(): return 'disconnected'
        if self.stopped.is_set(): return 'output_stopped'
        return 'stroke_output_inactive'

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
            self._emit_current(self.on_trace, view, finished)
        except Exception:
            pass  # A display callback must not interfere with stroke/tap delivery.
        self._trace_visible = bool(points)
        self._trace_time = time.monotonic()

    def _emit_current(self, callback, *args):
        if self._batch_epoch == self._reset_epoch:
            callback(*args)

    def _publish_stroke(self, points, contact=None):
        note(self.on_diagnostic, 'stroke_delivered')
        self._notify_trace(points, True)
        if self.on_stroke_details is not None:
            metrics = measure_stroke(points, (contact or {}).get('duration_ms'))
            self._emit_current(self.on_stroke_details, dict(points=points, kinematics=metrics))
        else:
            self._emit_current(self.on_stroke, points)

    def _run(self):
        reason, error = "", None
        events = []
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
                    two_finger_steps = list(self.pointer_events)
                    self.pointer_events.clear()
                    reset = self._batch_epoch != self._reset_epoch
                    self._batch_epoch = self._reset_epoch
                    overflowed = self.overflowed
                    self.overflowed = False
                if overflowed:
                    note(self.on_diagnostic, 'stroke_queue_overflow')
                if overflowed or reset:
                    self.collector.reset()
                    self._notify_trace([])
                    self._clear_pending()
                    if self.clicks: self.clicks.clear()
                    if self.double_clicks: self.double_clicks.clear()
                if self.input_since is not None:
                    since = self.input_since()
                    if since != self.input_epoch:
                        self.input_epoch = since
                        self.collector.reset()
                        self._notify_trace([])
                        self._clear_pending()
                        if self.clicks: self.clicks.clear()
                        if self.double_clicks: self.double_clicks.clear()
                    if since is None:
                        for _, trace in events:
                            if trace: trace.finish('stroke_not_foreground')
                        events = []
                        continue
                    fresh = []
                    for event, trace in events:
                        if event.timestamp >= since:
                            fresh.append((event, trace))
                        elif trace:
                            trace.finish('stroke_focus_changed')
                    events = fresh
                self.two_finger_steps.extend(two_finger_steps)
                # Confirmed edges carry their original (earlier) frame index.
                # Order this batch for the stroke consumer only; pointer and
                # click delivery in the SDK keep their original ordering.
                events.sort(key=lambda row: (row[0].step, {'contact': 0, 'move': 1, 'click': 2, 'click_verdict': 3}.get(row[0].kind, 4)))
                for event, trace in events:
                    if self.stopped.is_set() or self.connection.is_set() or self._batch_epoch != self._reset_epoch:
                        break
                    if trace: trace.dequeued(queue_depth=len(events))
                    if self.input_since is not None and self.input_since() != self.input_epoch:
                        break
                    if time.monotonic() - event.timestamp > .15:
                        note(self.on_diagnostic, 'stroke_event_stale')
                        if trace: trace.finish('stroke_queue_expired', queue_limit_ms=150)
                        self.collector.reset()
                        self._notify_trace([])
                        self._clear_pending()
                        continue
                    clicks = self.clicks or self.double_clicks
                    if clicks:
                        decisions = clicks.feed(event)
                        if trace and not decisions:
                            trace.finish(clicks.last_rejection or 'click_rejected')
                        for decision in decisions:
                            if trace:
                                trace.fields['decision'] = decision.kind
                                trace.record('recognized')
                            if decision.kind == "double":
                                self.collector.reset()
                                self._clear_pending()
                                if self._batch_epoch != self._reset_epoch:
                                    break
                                self.active = False
                                self.on_double_click()
                                if trace: trace.finish('double_click_exit_stroke_requested', outcome='routed')
                            elif trace:
                                trace.finish('stroke_mode_no_mouse_click', outcome='routed')
                    elif trace:
                        trace.finish('stroke_mode_no_mouse_click', outcome='routed')
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
                        contact_points = []
                        complete = False
                        if (self.on_tap_contact is not None or self.on_selection is not None or self.contact_classifier is not None) and start_step is not None and end_step is not None:
                            # Preserve even tiny/short contacts that the handwriting
                            # collector legitimately rejects. Trim at the original
                            # release frame, just as the collector does.
                            moves = [m for m in self.collector.moves if start_step <= m.step < end_step]
                            if (event.kind == 'move' and start_step <= event.step < end_step
                                    and (not moves or event.step > moves[-1].step)):
                                moves.append(event)
                            if moves:
                                complete = (len(moves) == end_step-start_step
                                            and all(m.step == start_step+i for i, m in enumerate(moves)))
                                contact_points = [(0., 0.)]
                                x = y = 0.
                                for move in moves[1:]:
                                    x += float(move.dx)
                                    y += float(move.dy)
                                    contact_points.append((x, y))
                        points = self.collector.feed(event)
                        if (start_step is not None and self.collector.start_step is None
                                and event.kind == 'move' and last_step is not None and event.step != last_step + 1):
                            note(self.on_diagnostic, 'stroke_movement_gap')
                        if start_step is not None and end_step is not None and self.collector.start_step is None:
                            self.pending_strokes[end_step] = (points, time.monotonic())
                            self.pending_contact_details[end_step] = dict(
                                start_step=start_step,
                                duration_ms=(end_step-start_step)*5 if start_step is not None else None,
                                points=contact_points, complete=complete,
                                stream_id=f'{self._stream_id}:{self._contact_epoch}')
                    elif event.kind in {"click", "click_verdict"}:
                        if event.step not in self.resolved_steps:
                            self.click_verdicts[event.step] = (
                                True if event.kind == 'click' else event.is_click, time.monotonic())
                for _, trace in events:
                    if trace and not trace.finished:
                        trace.finish('session_reset' if self._batch_epoch != self._reset_epoch else self._click_stop_reason())
                events = []
                if self._batch_epoch != self._reset_epoch:
                    continue
                if self.input_since is not None and self.input_since() != self.input_epoch:
                    continue
                self._resolve_contacts()
                two_finger = self.collector.touching and self._two_finger(self.collector.start_step)
                if two_finger and not self._two_finger_live:
                    self._notify_trace([])  # Two fingers draw the selection path, not ink.
                self._two_finger_live = two_finger
                if self.on_selection_state is not None:
                    if (self.collector.touching and self.collector.moves
                            and time.monotonic() - self.collector.moves[-1].timestamp > .15):
                        # A stopped stream is not evidence of a stationary finger.
                        self.collector.reset()
                        self._notify_trace([])
                        two_finger = self._two_finger_live = False
                    if not self.collector.touching:
                        # Keep a released two-finger path on screen until it resolves.
                        if not (self._selection_two and self.pending_strokes):
                            self._selection_two = False
                            self._notify_selection_state([])
                    elif time.monotonic() - self._selection_time >= .033 or two_finger != self._selection_two:
                        self._selection_two = two_finger
                        self._notify_selection_state(self.collector.points, two_finger)
                if two_finger:
                    pass  # The selection state above carries this contact's path.
                elif self.collector.touching and len(self.collector.points) > 1:
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
            with self.lock:
                pending = list(self.events)
                self.events.clear()
            for _, trace in [*events, *pending]:
                if trace and not trace.finished:
                    trace.finish('stroke_worker_error' if error else self._click_stop_reason(),
                                 error_type=type(error).__name__ if error else None)
            self._clear_pending()
            if self.clicks: self.clicks.clear()
            if self.double_clicks: self.double_clicks.clear()
            self.on_end(reason, error)
