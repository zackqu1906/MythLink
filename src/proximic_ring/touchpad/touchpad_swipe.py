"""Lock a two-finger contact to vertical scrolling or one horizontal action."""
import math
from collections import deque


class TouchpadSwipe:
    # SDK pointer deltas before user pointer/scroll gain, in screen pixels.
    axis_distance = 6.
    axis_ratio = 1.5
    swipe_distance = 60.

    def __init__(self):
        self.handled_ends = deque(maxlen=256)
        self.reset()

    def reset(self):
        self.episode = None
        self.axis = None
        self.x = self.y = self.pending_y = 0.
        self.fired = False

    def contact(self, event):
        if event.phase == 'BEGIN':
            self.reset()
            self.episode = event.episode_id
        elif event.phase in {'END', 'CANCEL'} and self.episode == event.episode_id:
            if self.fired and event.end_timestamp is not None:
                self.handled_ends.append(event.end_timestamp)
            self.reset()

    def move(self, event):
        """Return (vertical delta, navigation); never repeat before lift-off."""
        if not all(math.isfinite(v) for v in (event.dx, event.dy)):
            self.reset()
            return 0., None
        if self.episode is None or event.episode_id != self.episode:
            return event.dy, None  # Missing BEGIN can scroll but cannot navigate.
        self.x += event.dx
        self.y += event.dy
        self.pending_y += event.dy
        if self.axis is None:
            x, y = abs(self.x), abs(self.y)
            if x >= self.axis_distance and x >= y*self.axis_ratio:
                self.axis = 'horizontal'
            elif y >= self.axis_distance and y >= x*self.axis_ratio:
                self.axis = 'vertical'
            elif max(x, y) >= 2*self.axis_distance:
                self.axis = 'vertical'  # An ambiguous diagonal stays a scroll.
            else:
                return 0., None
        if self.axis == 'vertical':
            delta, self.pending_y = self.pending_y, 0.
            return delta, None
        if not self.fired and abs(self.x) >= self.swipe_distance:
            self.fired = True
            return 0., 'forward' if self.x < 0 else 'back'
        return 0., None
