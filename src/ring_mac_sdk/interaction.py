"""Deterministic port of 579694e InteractionEngine and ClickPairing.

Packet ingress timestamps are metadata; frame times drive gesture intervals.
Call only from one owner. No OS input, display scaling or wall-clock time.
"""
import math
import time
from collections import deque
from dataclasses import dataclass

from .types import ContactRange

MS = 1_000_000
PERIOD = 5 * MS
INF = 2**63-1


@dataclass(frozen=True)
class ContactTimes:
    down: int
    up: int
    down_frame: int
    up_frame: int


class Pairing:
    def __init__(self, sink, ids):
        self.sink, self.ids = sink, ids
        self.clear()

    def clear(self):
        self.first = self.second = None
        self.safe = -INF
        self.entered = self.ready = self.id = 0

    def single(self, now):
        first = self.first
        self.first = self.second = None
        if first is not None:
            self.sink("click", now, id=self.id, button="LEFT", contacts=(first,))

    def down(self, frame_time, now):
        if self.first and self.second is None and now > self.first.up+250*MS:
            self.single(now)
        if self.first and self.second is None and self.first.up_frame < frame_time <= self.first.up_frame+250*MS and now <= self.first.up+250*MS:
            self.second, self.ready = frame_time, now

    def rejected(self, down, now):
        if self.second is not None and self.second == down:
            self.single(now)

    def candidate(self, button, contact, now):
        if now > contact.up+200*MS:
            self.rejected(contact.down_frame, now)
            return
        if self.first and self.second is not None and self.second == contact.down_frame:
            if button == "LEFT":
                self.sink("click", now, id=self.id, button="LEFT", contacts=(self.first, contact))
                self.first = self.second = None
                return
            self.single(now)
        elif self.first:
            self.single(now)
        current_id = self.ids()
        if button == "RIGHT":
            self.sink("click", now, id=current_id, button=button, contacts=(contact,))
            return
        self.first, self.entered, self.id, self.second = contact, now, current_id, None
        if now >= contact.up+250*MS:
            self.single(now)
        else:
            self.sink("preview", now, id=self.id, contact=contact)

    def progress(self, frame_time, now):
        if self.second is not None and self.safe <= self.second+200*MS < frame_time:
            self.ready = now
        self.safe = frame_time
        self.tick(now)

    def tick(self, now):
        if self.first and now >= self.next_wake():
            self.single(now)

    def next_wake(self):
        if not self.first:
            return INF
        if self.second is None:
            return min(self.entered+1000*MS, self.first.up+250*MS)
        return min(self.entered+1000*MS, self.ready) if self.safe > self.second+200*MS else self.entered+1000*MS


@dataclass(frozen=True)
class Recognition:
    frame: int
    source: int
    ready: int
    x: float
    y: float
    touch: float
    finger: float


@dataclass
class Episode:
    id: int
    start: int
    down: int
    end: int = INF
    two: bool | None = None
    seen: int = 0
    votes: int = 0


def sigmoid(x):
    return 1/(1+math.exp(-x)) if x >= 0 else math.exp(x)/(1+math.exp(x))


def delta(x, y):
    if not math.isfinite(x) or not math.isfinite(y):
        raise ValueError("Nonfinite velocity")
    speeds = (0, .05, .10, .20, .325, .50, .75, 1.10, 1.55, 2.15, 3, 4)
    ratios = (292.5, 292.5, 303.75, 330, 371.25, 423.75, 491.25, 615, 768.75, 1001.25, 1312.5, 1665)
    speed, ratio = math.hypot(x, y), ratios[-1]
    for i in range(1, len(speeds)):
        if speed <= speeds[i]:
            ratio = ratios[i-1]+(speed-speeds[i-1])/(speeds[i]-speeds[i-1])*(ratios[i]-ratios[i-1])
            break
    dx, dy = x*ratio*.005, y*ratio*.005
    return (0., 0.) if math.hypot(dx, dy) < .1 else (dx, dy)


class Engine:
    def __init__(self, sink):
        self.sink = sink
        self.episode_ids = self.click_ids = 0
        self.clicks = Pairing(sink, self._next_click)
        self.clear()

    def _next_click(self):
        self.click_ids += 1
        return self.click_ids

    def clear(self):
        self.recognition = deque()
        self.fixed, self.fingers = {}, {}
        self.history = deque(maxlen=15)
        self.active = None
        self.published = self.last_touch = self.confirmed = -1
        self.edges = {True: None, False: None}
        self.dropped = 0
        self.clicks.clear()

    def publish(self, g, arrival, values, available):
        if len(values) != 8 or not all(math.isfinite(v) for v in values):
            raise ValueError("Expected eight finite Raw2 outputs")
        batch, previous = [], self.published
        for i in range(2):
            frame = g-1+i
            if frame < 0 or (previous >= 0 and frame != previous+1):
                raise ValueError("Frame discontinuity")
            x, y, touch, finger = values[4*i:4*i+4]
            batch.append(Recognition(frame, arrival, available, x, y, sigmoid(touch), sigmoid(finger)))
            previous = frame
        if len(self.recognition)+2 > 600:
            raise ValueError("Recognition capacity exceeded")
        self.recognition.extend(batch)
        self.published = previous

    def round(self, clock=time.monotonic_ns):
        self.clicks.tick(clock())
        spent = 0
        for _ in range(2):
            if not self.recognition or spent >= MS:
                break
            start = clock()
            self.recognize(self.recognition.popleft(), start)
            spent += max(0, clock()-start)
            self.clicks.tick(clock())

    def next_wake(self, now):
        return now if self.recognition else self.clicks.next_wake()

    @staticmethod
    def mode(e):
        return "UNKNOWN" if e.two is None else "TWO_FINGER" if e.two else "SINGLE"

    def contact(self, e, phase, confirming, now):
        end = self.fixed[e.end] if phase in ("END", "CANCEL") else None
        self.sink("contact", now, id=e.id, phase=phase, mode=self.mode(e), source=confirming.source,
                  range=ContactRange(e.start, e.down, e.end if end else None, end.source if end else None))

    def edge(self, down):
        if len(self.history) != 15:
            return -1
        values = sorted(r.touch for r in self.history)
        high, low = values[12], values[2]
        if high-low <= .5 or abs(high-low-.5) <= 1e-12:
            return -1
        hi = [r.frame for r in self.history if r.touch >= high][-3:]
        lo = [r.frame for r in self.history if r.touch <= low][-3:]
        arrival, departure = (hi, lo) if down else (lo, hi)
        return arrival[0] if departure[-1] < arrival[0] and arrival[-1] == self.last_touch and arrival[0] > self.confirmed else -1

    def accept_edge(self, down, frame):
        previous = self.edges[down]
        if previous is not None and frame-previous < 15:
            return False
        self.edges[down] = frame
        return True

    def decide(self, e, closed):
        count = max(0, min(e.start+10, self.last_touch+1, e.end)-e.start)
        for i in range(count):
            if not e.seen & (1 << i):
                e.seen |= 1 << i
                if self.fingers[e.start+i] >= .5:
                    e.votes |= 1 << i
        if e.two is not None:
            return
        votes = (e.votes & ((1 << count)-1)).bit_count()
        if closed or votes >= 2 or count == 10:
            e.two = votes >= 2

    def finish(self, e, now):
        end = self.fixed.get(e.end)
        part = [self.fixed[f] for f in range(e.start, e.end) if f in self.fixed]
        if end is None or e.end <= e.start or (e.end-e.start)*PERIOD >= 200*MS or len(part) != e.end-e.start or math.hypot(sum(r.x*.005 for r in part), sum(r.y*.005 for r in part)) > .020:
            self.clicks.rejected(e.start*PERIOD, now)
            return
        count = max(0, min(10, e.end-e.start))
        two = (e.votes & ((1 << count)-1)).bit_count() >= 2
        self.clicks.candidate("RIGHT" if two else "LEFT", ContactTimes(e.down, end.source, e.start*PERIOD, e.end*PERIOD), now)

    def recognize(self, r, now):
        if self.last_touch >= 0 and r.frame != self.last_touch+1:
            raise ValueError("Touch frame gap")
        self.last_touch = r.frame
        self.fixed[r.frame], self.fingers[r.frame] = r, r.finger
        self.history.append(r)
        down, up = self.edge(True), self.edge(False)
        cancelled = new_down = ended = None
        if down >= 0 and self.accept_edge(True, r.frame):
            self.confirmed = r.frame
            if self.active:
                self.active.end = down
                self.decide(self.active, True)
                self.contact(self.active, "CANCEL", r, now)
                cancelled = self.active.start*PERIOD
            start = self.fixed[down]
            self.episode_ids += 1
            self.active = Episode(self.episode_ids, down, start.source)
            self.contact(self.active, "BEGIN", r, now)
            new_down = start.frame*PERIOD
        elif up >= 0 and self.accept_edge(False, r.frame):
            self.confirmed = r.frame
            if self.active:
                ended, self.active = self.active, None
                ended.end = up
                self.decide(ended, True)
                self.contact(ended, "END", r, now)
        if self.active:
            unknown = self.active.two is None
            self.decide(self.active, False)
            if unknown and self.active.two is not None:
                self.contact(self.active, "CLASSIFIED", r, now)
        if cancelled is not None:
            self.clicks.rejected(cancelled, now)
        if new_down is not None:
            self.clicks.down(new_down, now)
        if ended:
            self.finish(ended, now)
        self.fingers = {k: v for k, v in self.fingers.items() if k >= self.last_touch-50}
        self.fixed = {k: v for k, v in self.fixed.items() if k >= self.last_touch-800}
        safe = self.fixed.get(self.last_touch-14)
        if safe:
            self.clicks.progress(safe.frame*PERIOD, now)
        if now > r.source+200*MS:
            self.dropped += 1
            return
        dx, dy = delta(r.x, r.y)
        if dx or dy:
            self.sink("motion", now, frame=r.frame, source=r.source, dx=dx, dy=dy,
                      mode=self.mode(self.active) if self.active else "SINGLE",
                      id=self.active.id if self.active else None)
