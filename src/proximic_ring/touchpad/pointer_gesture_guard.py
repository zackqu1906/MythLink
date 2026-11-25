"""Arbitrate firmware gestures against one ring pointer output's SDK events."""
from collections import deque
import math

from .touchpad_drag import TouchpadDrag


class PointerGestureGuard:
    release_delay = .250

    def __init__(self):
        self.contact = None
        self.quiet_until = 0.
        self.motion = deque()
        self.epoch = object()

    def gesture_epoch(self, now):
        # A new epoch prevents a deferred tap/pinch or half a pair from
        # crossing a pointer interaction, even after its release delay expires.
        return self.epoch if self.contact is None and now >= self.quiet_until else None

    def reset(self):
        self.contact = None
        self.quiet_until = 0.
        self.motion.clear()

    def _pointer_moved(self, event, stamp):
        if self.contact is not None or not (event.dx or event.dy):
            return False
        if not (math.isfinite(event.dx) and math.isfinite(event.dy)):
            return False
        if getattr(event, 'mode', '') == 'TWO_FINGER':
            return True
        # Without a contact edge, measure net displacement before user gain.
        while self.motion and self.motion[0][0] < stamp - TouchpadDrag.movement_window:
            self.motion.popleft()
        self.motion.append((stamp, event.dx, event.dy))
        return math.hypot(sum(dx for _, dx, _ in self.motion),
                          sum(dy for _, _, dy in self.motion)) >= TouchpadDrag.start_distance

    def observe(self, event, stamp, now):
        if event.kind == 'pointer_contact':
            phase, contact = event.phase, event.episode_id
        elif event.kind == 'contact':
            if event.state == 'reset':
                self.reset()
                return
            phase, contact = ('BEGIN' if event.state == 'down' else 'END'), 'legacy'
        else:
            phase, contact = None, None

        releasing = phase in {'END', 'CANCEL'}
        if releasing:
            if self.contact is not None and contact != self.contact:
                return
            self.contact = None  # Even a late release must end a stationary hold.
            self.motion.clear()
        max_age = .2 if getattr(event, 'session_id', None) is not None else .1
        if releasing:
            max_age = self.release_delay
        if not 0 <= now - stamp <= max_age:
            return
        if event.kind == 'move' and not self._pointer_moved(event, stamp):
            return
        if self.gesture_epoch(now) is not None:
            self.epoch = object()
        if phase in {'BEGIN', 'CLASSIFIED'}:
            self.contact = contact
            self.motion.clear()
        self.quiet_until = max(self.quiet_until, stamp + self.release_delay)
