"""Public 1.4.0 token/trajectory/click adapter. No desktop event injection.

Ported from supplied AAR b.f, e.m/n/d/h/g/i contracts. Timing uses monotonic
seconds; model step is fixed at 200 Hz. Cross-Android numeric parity is pending.
"""
from collections import deque
import math
import struct
import numpy as np

from .trajectory import CD_GAIN, CD_SPEED, TrajectoryBuffer, TrajectoryCursor

START = bytes.fromhex('21 00 c8 00 c8 00 d0 07 10 0a 01')
STOP = b'\x21\x01'
WRITE = '6e400002-b5a3-f393-e0a9-e50e24dcca9e'
NOTIFY = '6e400003-b5a3-f393-e0a9-e50e24dcca9e'


def parse_tokens(packet):
    if packet[:2] != b'\x21\x05': return None
    if len(packet)<10: raise ValueError('token 数据头不完整')
    seq,count,uptime,version=struct.unpack_from('<HBIB',packet,2)
    if not 1<=count<=20 or version!=1 or len(packet)!=10+count*12:
        raise ValueError('不支持的 token 数据格式')
    values=np.asarray(struct.unpack_from('<'+'e'*(count*6),packet,10),np.float32).reshape(count,6)
    if not np.isfinite(values).all(): raise ValueError('token 含无效数值')
    return seq,uptime,values


class ClickDetector:
    """Original contact and single-click rules; used once per input stream."""
    def __init__(self):
        self.reset()

    def reset(self):
        self.window = deque(maxlen=15)
        self.raw = {}
        self.pending = deque()
        self.start = self.transition = self.last_prob = self.last_move = None
        self.contact_edges = deque(maxlen=64)
        self.contact_verdicts = deque(maxlen=64)

    def probability(self, index, probability):
        if self.last_prob is not None and index != self.last_prob + 1:
            self.reset()
        self.last_prob = index
        self.window.append((index, probability))
        if len(self.window) < 15:
            return []
        sorted_probabilities = sorted(value for _, value in self.window)
        low, high = sorted_probabilities[2], sorted_probabilities[12]
        if high - low <= .5 + 1e-12:
            return []
        hi = [i for i, value in self.window if value >= high][-3:]
        lo = [i for i, value in self.window if value <= low][-3:]
        previous, current = (lo, hi) if self.start is None else (hi, lo)
        if previous[-1] >= current[0] or current[-1] != index:
            return []
        if self.transition is not None and current[0] <= self.transition:
            return []
        self.transition = index
        # Contact metadata observes the original decision; it adds no threshold.
        self.contact_edges.append((self.start is None, current[0], index))
        if self.start is None:
            self.start = current[0]
        else:
            self.pending.append((self.start, current[0]))
            self.start = None
        return self.ready()

    def movement(self, index, velocity):
        self.raw[index] = velocity
        self.last_move = index
        for step in list(self.raw):
            if step < index - 800:
                del self.raw[step]
        return self.ready()

    def ready(self):
        result = []
        while (self.pending and self.last_move is not None
               and self.last_move >= self.pending[0][1] - 1):
            start, end = self.pending.popleft()
            self.contact_verdicts.append((start, end, False))
            if not 0 < end - start < 40 or any(k not in self.raw for k in range(start, end)):
                continue
            dx = sum(self.raw[k][0] for k in range(start, end)) * .005
            dy = sum(self.raw[k][1] for k in range(start, end)) * .005
            if math.hypot(dx, dy) <= .02:
                result.append(dict(kind='click', step=end))
                self.contact_verdicts[-1] = (start, end, True)
        return result


class Postprocessor:
    """One sample buffer and click detector; two optional read schedules."""
    def __init__(self):
        self.pointer = None
        self.reset()

    @property
    def n(self):
        return self.frames.n

    def reset(self):
        self.frames = TrajectoryBuffer()
        self.trajectory = TrajectoryCursor(paced=False)
        if self.pointer is not None:
            self.pointer = TrajectoryCursor(paced=True)
        self.prob = {}
        self.click = ClickDetector()
        self.events = []
        self.contact = 0.

    def enable_pointer_moves(self):
        """Enable the original mouse schedule before the first model frame."""
        self.pointer = TrajectoryCursor(paced=True)

    def _oldest_consumed(self):
        return (min(self.trajectory.last, self.pointer.last)
                if self.pointer is not None else self.trajectory.last)

    def add(self, output, arrival, frame_time):
        output = np.asarray(output)
        if output.shape != (5, 3) or not np.isfinite(output).all():
            raise ValueError('模型输出无效')
        self.frames.add(output, arrival, frame_time, self._oldest_consumed())
        if self.n <= 200:
            return
        for j, (_, _, logit) in enumerate(output):
            step = self.n + j - 2
            if step > 200:
                probability = 1 / (1 + math.exp(-max(-80, min(80, float(logit)))))
                self.prob.setdefault(step, []).append(probability)
        for step in sorted(k for k in self.prob if k <= self.n - 2):
            samples = self.prob.pop(step)
            self.contact = sum(samples) / len(samples)
            clicks = self.click.probability(step, self.contact)
            for down, edge_step, confirmed_step in self.click.contact_edges:
                self.events.append(dict(kind='contact', state='down' if down else 'up',
                                        step=edge_step, confirmed_step=confirmed_step))
            self.click.contact_edges.clear()
            self.events.extend(clicks)

    def drain(self, now):
        expired, movements = self.trajectory.drain(self.frames, now)
        for step in expired:
            self.click.reset()  # Never classify a click across lost recognition data.
            self.events.append(dict(kind='contact', state='reset', step=step, confirmed_step=step))
        for step, vx, vy, dx, dy in movements:
            self.events.append(dict(kind='move', step=step, dx=dx, dy=dy, contact=self.contact))
            self.events.extend(self.click.movement(step, (vx, vy)))
        self.frames.release_through(self._oldest_consumed())
        return self.take_events()

    def drain_pointer(self, now):
        if self.pointer is None:
            return []
        # Mouse expiry must never reset or feed the shared click detector.
        _, movements = self.pointer.drain(self.frames, now)
        self.frames.release_through(self._oldest_consumed())
        return [dict(kind='move', step=step, dx=dx, dy=dy, contact=self.contact)
                for step, _, _, dx, dy in movements]

    def take_events(self):
        for start, end, is_click in self.click.contact_verdicts:
            self.events.append(dict(kind='click_verdict', start_step=start, step=end, is_click=is_click))
        self.click.contact_verdicts.clear()
        events, self.events = self.events, []
        return events
