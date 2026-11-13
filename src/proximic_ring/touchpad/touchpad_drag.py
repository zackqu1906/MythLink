"""Single-contact dwell-to-drag using SDK deltas before user pointer gain."""
from collections import deque
import math
import time
import uuid


class TouchpadDrag:
    # SDK pixels, independent of pointer speed, axis inversion and screen DPI.
    # Initial single-user calibration: stationary 600 ms radius/path < 4.8 px;
    # stationary 100 ms drift < 4.6 px in the offline SDK evaluation.
    radius = 6.
    path_limit = 8.
    movement_window = .1
    start_distance = 6.

    def __init__(self, release, ready, *, record=None):
        self.release, self.ready = release, ready
        self.record = record
        self.active = None
        self.held = False
        self.state = 'idle'
        self.handled = deque(maxlen=256)
        self.cancelled = deque(maxlen=256)

    @staticmethod
    def _contact_key(event):
        return event.session_id, event.generation, event.episode_id

    def _consume_end(self, event):
        if event.end_timestamp is not None:
            self.handled.append((event.session_id, event.generation, event.end_timestamp))

    def _record(self, stage, event=None, **details):
        if self.record is None:
            return
        try:
            now = time.monotonic()
            fields = dict(drag_id=self.drag_id, stage=stage,
                sdk_session=self.active.session_id, sdk_generation=self.active.generation,
                episode_id=self.active.episode_id, mode=self.mode,
                contact_timestamp=self.active.timestamp,
                contact_start_timestamp=self.active.start_timestamp,
                drag_start_timestamp=self.started_timestamp,
                last_motion_timestamp=self.last_motion,
                held_ms=round((now-self.started_at)*1000, 3), **details)
            if event is not None:
                fields.update(event_kind=event.kind, event_phase=getattr(event, 'phase', None),
                    event_mode=event.mode, event_episode_id=event.episode_id,
                    event_session=event.session_id, event_generation=event.generation,
                    event_timestamp=event.timestamp,
                    event_end_timestamp=getattr(event, 'end_timestamp', None),
                    event_age_ms=round((now-event.timestamp)*1000, 3),
                    dx=getattr(event, 'dx', None), dy=getattr(event, 'dy', None))
            # Invalid motion must still leave a JSON record explaining cleanup.
            self.record({key: None if isinstance(value, float) and not math.isfinite(value) else value
                         for key, value in fields.items()})
        except Exception:
            pass  # Diagnostics must never prevent a mouse-up.

    def cancel(self, reason='cancelled', event=None, **details):
        if self.held:
            self._record('end', event, reason=reason, release_requested=True, **details)
        if self.active is not None and self.held:
            self.cancelled.append(self._contact_key(self.active))
        self.active = None
        self.state = 'idle'
        if self.held:
            self.held = False
            self.release()

    def consumed(self, event):
        return event.button == 'left' and (
            event.session_id, event.generation, event.source_timestamp) in self.handled

    def contact(self, event, *, enabled, dwell_ms):
        key = self._contact_key(event)
        if event.phase == 'BEGIN':
            if self.active is not None and self._contact_key(self.active) == key:
                return
            self.cancel('contact_replaced', event)
            if enabled:
                self.active = event
                self.mode = event.mode
                # Start at the confirmed BEGIN: no unobserved pre-BEGIN motion
                # can count toward the dwell. Settings apply to the next contact.
                self.deadline = event.timestamp + dwell_ms / 1000
                self.x = self.y = self.path = 0.
                self.recent = deque()
                self.last_motion = event.timestamp
                self.state = 'pending'
        if event.phase in {'END', 'CANCEL'} and key in self.cancelled:
            self._consume_end(event)
        if self.active is None or self._contact_key(self.active) != key:
            return
        self.mode = event.mode
        if event.phase in {'END', 'CANCEL'}:
            if self.held:
                self._consume_end(event)
            self.cancel('sdk_end' if event.phase == 'END' else 'sdk_cancel', event)
        elif not enabled or event.mode == 'TWO_FINGER':
            self.cancel('drag_disabled' if not enabled else 'two_finger', event)

    def tick(self, now):
        # A stationary SDK contact emits no Motion. The worker calls this even
        # with an empty queue; readiness itself never presses a mouse button.
        if (self.active is not None and self.mode == 'SINGLE' and
                self.state == 'pending' and now >= self.deadline):
            self.state = 'ready'
            self.ready()

    def move(self, event):
        """Return (kind, dx, dy, begin_drag), or buffer sub-threshold jitter."""
        if not all(math.isfinite(v) for v in (event.dx, event.dy, event.timestamp)):
            self.cancel('invalid_motion', event)
            raise ValueError('Nonfinite dwell motion')
        if self.active is None:
            return 'move', event.dx, event.dy, False
        if (self._contact_key(event) != self._contact_key(self.active) or
                event.mode == 'TWO_FINGER' or event.timestamp < self.last_motion):
            reason = ('contact_mismatch' if self._contact_key(event) != self._contact_key(self.active)
                      else 'two_finger' if event.mode == 'TWO_FINGER' else 'motion_out_of_order')
            self.cancel(reason, event)
            return 'move', event.dx, event.dy, False
        self.last_motion = event.timestamp
        if self.held:
            return 'drag', event.dx, event.dy, False
        if self.state == 'moving':
            return 'move', event.dx, event.dy, False
        # Evaluate samples by source time even if a wall-clock readiness hint
        # already fired. Late pre-deadline movement can still disqualify dwell.
        if event.timestamp < self.deadline or self.mode != 'SINGLE':
            self.x += event.dx
            self.y += event.dy
            self.path += math.hypot(event.dx, event.dy)
            if math.hypot(self.x, self.y) > self.radius or self.path > self.path_limit:
                self.state = 'moving'  # Never re-arm until a new BEGIN.
                return 'move', self.x, self.y, False
            return None
        self.tick(event.timestamp)
        self.recent.append((event.timestamp, event.dx, event.dy))
        while self.recent and self.recent[0][0] <= event.timestamp-self.movement_window:
            self.recent.popleft()
        dx = sum(sample[1] for sample in self.recent)
        dy = sum(sample[2] for sample in self.recent)
        if math.hypot(dx, dy) <= self.start_distance:
            return None
        self.state, self.held = 'dragging', True
        self.drag_id = uuid.uuid4().hex[:12]
        self.started_at, self.started_timestamp = time.monotonic(), event.timestamp
        self._record('start', event, reason='dwell_motion')
        # The output worker inserts a single-count down immediately before this
        # buffered displacement, preserving ordering with earlier mouse moves.
        return 'drag', dx, dy, True
