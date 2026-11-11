"""Two-finger motion interface on top of Raw2 pointer events.

The Raw2 model already reports both axes for two-finger contact; the product
only maps the vertical part to the system wheel. This module exposes the full
motion without posting any OS input:

* ``TwoFingerScroll``: every two-finger frame, with dx and dy (pointer px).
* ``TwoFingerSwipe``: one event per two-finger gesture when it ends, with its
  total displacement and one of eight directions.

Feed it the ``on_pointer_move`` events of ``RingSession.touchpad_on(protocol='raw2')``
and call ``tick()`` periodically (e.g. every 20-50 ms) so a gesture can end
when the fingers stop. All callbacks run synchronously on the caller's thread.

Coordinates follow the pointer: +dx is right, +dy is down (screen space).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
import time
from typing import Callable

from .events import Raw2PointerMove, Raw2Reset


class Direction(str, Enum):
    UP = 'up'
    UP_RIGHT = 'up_right'
    RIGHT = 'right'
    DOWN_RIGHT = 'down_right'
    DOWN = 'down'
    DOWN_LEFT = 'down_left'
    LEFT = 'left'
    UP_LEFT = 'up_left'


# Counter-clockwise from +x (right), in 45-degree sectors.
_SECTORS = (Direction.RIGHT, Direction.UP_RIGHT, Direction.UP, Direction.UP_LEFT,
            Direction.LEFT, Direction.DOWN_LEFT, Direction.DOWN, Direction.DOWN_RIGHT)


def classify_direction(dx: float, dy: float) -> Direction:
    """Nearest of eight directions; each covers ±22.5° around its axis."""
    if not (math.isfinite(dx) and math.isfinite(dy)) or (dx == 0 and dy == 0):
        raise ValueError('direction needs a finite, non-zero displacement')
    angle = math.degrees(math.atan2(-dy, dx)) % 360  # screen y grows downward
    return _SECTORS[int((angle + 22.5) // 45) % 8]


@dataclass(frozen=True)
class TwoFingerScroll:
    dx: float
    dy: float
    timestamp: float
    session_id: str = ''
    generation: int = 0
    kind: str = 'two_finger_scroll'


@dataclass(frozen=True)
class TwoFingerSwipe:
    direction: Direction
    dx: float
    dy: float
    distance: float
    angle_deg: float          # 0 = right, 90 = up, counter-clockwise
    duration_s: float
    frames: int
    started: float
    ended: float
    session_id: str = ''
    generation: int = 0
    kind: str = 'two_finger_swipe'


class TwoFingerGestures:
    """Group two-finger frames into gestures and classify their direction.

    min_distance: net displacement (px) below which a gesture is discarded.
    end_gap_s: the gesture ends after this long without two-finger frames.
    max_interrupt_s: SINGLE/UNKNOWN frames shorter than this inside a
        gesture do not split it (the model briefly flickers between classes).
    """

    def __init__(self, *, on_scroll: Callable[[TwoFingerScroll], None] | None = None,
                 on_swipe: Callable[[TwoFingerSwipe], None] | None = None,
                 min_distance: float = 15., end_gap_s: float = .15, max_interrupt_s: float = .08,
                 clock: Callable[[], float] = time.monotonic):
        if min_distance < 0 or end_gap_s <= 0 or max_interrupt_s < 0:
            raise ValueError('invalid two-finger thresholds')
        self.on_scroll, self.on_swipe = on_scroll, on_swipe
        self.min_distance, self.end_gap_s, self.max_interrupt_s = min_distance, end_gap_s, max_interrupt_s
        self.clock = clock
        self._stream = None
        self._clear()

    def _clear(self):
        self._active = False
        self._dx = self._dy = 0.
        self._frames = 0
        self._started = self._last_two = 0.
        self._interrupt_since = None

    def reset(self):
        """Drop any gesture in progress without emitting it."""
        self._clear()

    def feed(self, event) -> TwoFingerSwipe | None:
        """Consume one pointer event; returns a swipe if this event ended one."""
        if isinstance(event, Raw2Reset):
            self._stream = (event.session_id, event.generation)
            self.reset()
            return None
        if not isinstance(event, Raw2PointerMove):
            return None
        stream = (event.session_id, event.generation)
        if stream != self._stream:
            # A new session/generation invalidates the previous gesture.
            self._stream = stream
            self.reset()
        now = self.clock()
        mode = str(getattr(event.mode, 'value', event.mode)).upper()
        if mode != 'TWO_FINGER':
            if self._active:
                if self._interrupt_since is None:
                    self._interrupt_since = now
                elif now - self._interrupt_since > self.max_interrupt_s:
                    return self._finish(now)
            return None
        self._interrupt_since = None
        if not self._active:
            self._active, self._started = True, now
        self._dx += event.dx
        self._dy += event.dy
        self._frames += 1
        self._last_two = now
        if self.on_scroll is not None:
            self.on_scroll(TwoFingerScroll(event.dx, event.dy, event.timestamp, *stream))
        return None

    def tick(self) -> TwoFingerSwipe | None:
        """End a gesture whose fingers have stopped; call periodically."""
        now = self.clock()
        if self._active and now - self._last_two > self.end_gap_s:
            return self._finish(now)
        return None

    def flush(self) -> TwoFingerSwipe | None:
        """End the current gesture immediately (e.g. when the stream stops)."""
        return self._finish(self.clock()) if self._active else None

    def _finish(self, now):
        dx, dy, frames, started, ended = self._dx, self._dy, self._frames, self._started, self._last_two
        stream = self._stream or ('', 0)
        self._clear()
        distance = math.hypot(dx, dy)
        if distance < self.min_distance or distance == 0:
            return None
        swipe = TwoFingerSwipe(classify_direction(dx, dy), dx, dy, distance,
                               math.degrees(math.atan2(-dy, dx)) % 360, ended - started,
                               frames, started, ended, *stream)
        if self.on_swipe is not None:
            self.on_swipe(swipe)
        return swipe
