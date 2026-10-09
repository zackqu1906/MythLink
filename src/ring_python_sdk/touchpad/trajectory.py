"""Shared velocity samples and gain, with independent movement read cursors.

The model predicts five overlapping frames per input token. A paced reader can
see more predictions for a frame than a reader that has already consumed it.
Keep one sample buffer until both readers have passed a frame; cache the common
calculation only while its sample list is unchanged.
"""
import math

import numpy as np


CD_SPEED = [0, .05, .1, .2, .325, .5, .75, 1.1, 1.55, 2.15, 3, 4]
CD_GAIN = [390, 390, 405, 440, 495, 565, 655, 820, 1025, 1335, 1750, 2220]


class TrajectoryBuffer:
    def __init__(self):
        self.n = 0
        self.samples = {}
        self.times = {}
        self._calculated = {}

    def add(self, output, arrival, frame_time, oldest_consumed):
        self.n += 1
        self.times[self.n] = (frame_time, arrival)
        if self.n > 200:
            for j, (vx, vy, _) in enumerate(output):
                step = self.n + j - 2
                if step > 200 and step > oldest_consumed:
                    self.samples.setdefault(step, []).append((float(vx), float(vy)))
                    self._calculated.pop(step, None)
        for step in list(self.times):
            if step < self.n - 66 and step not in self.samples:
                del self.times[step]

    def available_after(self, step):
        return sorted(k for k in self.samples.keys() & self.times.keys() if k > step)

    def movement(self, step):
        if step not in self._calculated:
            velocity = np.mean(self.samples[step], axis=0)
            vx, vy = float(velocity[0]), float(velocity[1])
            x, y = vx * 1.595, vy
            cd = float(np.interp(math.hypot(x, y), CD_SPEED, CD_GAIN))
            gain = cd * .7 * .005
            dx, dy = x * gain * .75, y * gain * 1.10
            if math.hypot(dx, dy) < .1:
                dx = dy = 0.
            self._calculated[step] = (vx, vy, dx, dy)
        return self._calculated[step]

    def release_through(self, step):
        # Reclaim only samples consumed (or expired) on every active cursor.
        for k in list(self.samples):
            if k <= step:
                del self.samples[k]
                self.times.pop(k, None)
                self._calculated.pop(k, None)


class TrajectoryCursor:
    """Paced mouse: one due frame. Recognition: catch up at most 20 frames.

    Both retain their established timing and expiry rules. No click detector,
    model state or copy of the sample buffer lives in a cursor.
    """
    def __init__(self, *, paced):
        self.paced = paced
        self.last = 0
        self.next_step = self.next_time = self.last_time = None
        self.catchup = False

    def drain(self, frames, now):
        expired, movements = [], []
        for step in frames.available_after(self.last):
            if now - frames.times[step][0] <= .1:
                break
            expired.append(step)
            self.last = max(self.last, step)
            if self.next_step is not None and step >= self.next_step:
                self.next_step = step + 1

        available = frames.available_after(self.last)
        if self.next_time is None:
            if not available:
                return expired, movements
            self.next_step = available[0]
            if self.paced:
                # Original mouse cadence: start now, not at the oldest sensor
                # timestamp, so a new packet is spread across successive polls.
                self.next_time = max(now, (self.last_time or now - .005) + .005)
            else:
                # yyf: keep every due trajectory frame through slow polling.
                self.next_time = min(now, frames.times[self.next_step][0])

        for _ in range(1 if self.paced else 20):
            if now < self.next_time:
                break
            step = self.next_step
            if step <= self.last or step not in frames.samples or step not in frames.times:
                self.next_time = self.next_step = None
                break
            movements.append((step, *frames.movement(step)))
            self.last = step
            self.last_time = now
            self.next_step = step + 1
            available = frames.available_after(self.last)
            if not available:
                self.next_step = self.next_time = None
                self.catchup = False
                break
            age = now - frames.times[available[0]][1]
            if self.catchup and age < .015:
                self.catchup = False
            elif not self.catchup and age > .025:
                self.catchup = True
            interval = .00475 if self.catchup else .005
            self.next_time += interval
            if self.paced and self.next_time <= now:
                self.next_time += (int((now - self.next_time) / interval) + 1) * interval
        return expired, movements
