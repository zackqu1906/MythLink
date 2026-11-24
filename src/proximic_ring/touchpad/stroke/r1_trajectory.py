"""Reconstruct an R1 contact from its ordered, sparse SDK motion events."""
from collections import deque


class R1StrokeTrajectory:
    def __init__(self, max_points):
        self.max_points = max_points
        self.reset()

    def reset(self):
        self.stream = None
        self.contact = None
        self.history = deque(maxlen=64)
        self.moves = {}

    @property
    def start_step(self):
        return self.contact.start_step if self.contact is not None else None

    @property
    def touching(self):
        return self.start_step is not None

    @property
    def two_finger(self):
        return self.touching and self.contact.mode == 'TWO_FINGER'

    def _add(self, move):
        if (self.touching and self.start_step <= move.step < self.start_step + self.max_points
                and move.episode_id in (None, self.contact.episode_id)):
            self.moves[move.step] = (move.dx, move.dy)

    def _points(self, end_step):
        x = y = 0.
        points = [(x, y)]
        # SDK Motion omits zero deltas. The confirmed frame interval still
        # defines the 200 Hz path and duration used by contact classification.
        for step in range(self.start_step + 1, end_step):
            dx, dy = self.moves.get(step, (0., 0.))
            x += dx
            y += dy
            points.append((x, y))
        return points

    @property
    def points(self):
        return self._points(max(self.moves, default=self.start_step) + 1) if self.touching else []

    def feed(self, event):
        stream = event.session_id, event.generation
        if stream != self.stream:
            self.reset()
            self.stream = stream
        if event.kind == 'move':
            self.history.append(event)
            self._add(event)
            return None
        if event.phase == 'BEGIN':
            self.contact = event
            self.moves.clear()
            # BEGIN can arrive a few frames after the measured contact edge.
            for move in self.history:
                self._add(move)
            return None
        if not self.touching or event.episode_id != self.contact.episode_id:
            return None
        if event.phase == 'CLASSIFIED':
            self.contact = event
            return None

        start_step, end_step = self.start_step, event.end_step
        result = None
        if (event.phase == 'END' and end_step is not None and 0 < end_step - start_step <= self.max_points
                and event.mode in {'SINGLE', 'TWO_FINGER'}):
            result = start_step, end_step, event.mode, self._points(end_step)
        self.contact = None
        self.moves.clear()
        return result
