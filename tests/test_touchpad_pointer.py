"""Original mouse cadence alongside unchanged current click/stroke decisions."""
import hashlib
import json
import math
import struct

import numpy as np
import pytest

from ring_python_sdk.touchpad.core import Postprocessor
from ring_python_sdk.touchpad import TouchpadProcessor


def movement_trace(interval):
    post = Postprocessor()
    post.enable_pointer_moves()
    rows, n = [], 0
    for ms in range(3000):
        now = 1 + ms / 1000
        if ms % 50 == 0:
            for j in range(10):
                n += 1
                output = np.array([[.4 + math.sin((n+k)/20)*.2,
                                    math.cos((n+k)/33)*.1, 8.] for k in range(-2, 3)])
                post.add(output, now, now-(9-j)*.005)
        if ms % interval == 0:
            post.drain(now)
            for event in post.drain_pointer(now):
                if event['kind'] == 'move':
                    rows.append([ms, event['step'], round(event['dx'], 9), round(event['dy'], 9)])
    return rows


def test_mouse_frames_are_spread_across_notifications():
    rows = movement_trace(1)
    stable = [row for row in rows if row[0] >= 2000]
    assert len(stable) == 200
    gaps = [b[0]-a[0] for a, b in zip(stable, stable[1:])]
    assert min(gaps) >= 4 and max(gaps) <= 6


@pytest.mark.parametrize('interval, expected', [
    (1, '1b2e8b548eec3c6f368d879fb6408c14a2eab0eb95aa6535e755d85ed52dc99f'),
    (8, '86fa819761571c259322cff9fe3c1a61d2080e608375dcf5a1af124a59ff023f'),
    (16, '56234200e822f7e82c27894aa5e997c1b3563774ed91d0ca1f31e369b3469741'),
])
def test_movement_matches_recorded_original_version(interval, expected):
    # Golden traces generated from 27c773d before the yyf integration. Includes
    # varying velocities, frame indices, timing and original slow-poll expiry.
    encoded = json.dumps(movement_trace(interval), separators=(',', ':')).encode()
    assert hashlib.sha256(encoded).hexdigest() == expected


class Backbone:
    def __init__(self): self.n = self.calls = 0
    def reset(self): self.n = 0
    def step(self, token):
        self.n += 1
        self.calls += 1
        rows = []
        for k in range(self.n-2, self.n+3):
            down = 300 <= k < 320 or 350 <= k < 420
            rows.append([.4 if 350 <= k < 420 else 0., 0., 8. if down else -8.])
        return np.array(rows)


def packet(seq):
    return b'\x21\x05' + struct.pack('<HBIB', seq, 10, seq*50, 1) + bytes(120)


@pytest.mark.parametrize('interval', [1, 8, 16])
def test_pointer_cannot_change_current_click_stroke_events_or_run_inference_twice(interval):
    current = TouchpadProcessor(backbone=Backbone())
    split = TouchpadProcessor(backbone=Backbone())
    split.enable_pointer_moves()
    all_events, pointer = [], []
    maximum_buffered = 0
    for ms in range(3000):
        now = 1 + ms / 1000
        if ms % 50 == 0:
            for processor in (current, split):
                processor.feed(packet(ms//50), arrival=now, now=now)
        if ms % interval == 0:
            events = current.poll(now)
            assert split.poll(now) == events
            all_events.extend(events)
            pointer.extend(split.poll_pointer(now))
            maximum_buffered = max(maximum_buffered, len(split.post.frames.samples))
    assert current.backbone.calls == split.backbone.calls == 600
    assert any(e.kind == 'click' for e in all_events)
    assert any(e.kind == 'click_verdict' and not e.is_click for e in all_events)
    assert pointer and all(e.kind == 'move' for e in pointer)
    assert maximum_buffered <= 32  # The slower cursor cannot retain an ever-growing history.
    split.reset()
    assert split.poll_pointer(5.) == []
    assert split.post.n == 0 and split.post.pointer.last == 0


@pytest.mark.parametrize('interval, expected', [
    (1, 'ab19cc4e7033f76994ac454a974e88517922d107582d628e6f5f854d5914ec60'),
    (8, '0d16bd75292003f177f00fd19b5831a768039efc900fce43a828024eb881c1aa'),
    (16, '31dd1681d90a9482c141476b6cf82343e488ecc2e789d103125740f6b9c0a112'),
])
def test_recognition_matches_recorded_yyf_events(interval, expected):
    # Captured before this refactor: includes every timestamp, movement,
    # contact edge, click and negative stroke verdict, not just event counts.
    processor = TouchpadProcessor(backbone=Backbone())
    processor.enable_pointer_moves()
    rows = []
    for ms in range(3000):
        now = 1 + ms / 1000
        if ms % 50 == 0:
            processor.feed(packet(ms//50), arrival=now, now=now)
        if ms % interval == 0:
            rows.extend(vars(e) for e in processor.poll(now))
            processor.poll_pointer(now)
    encoded = json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()
    assert hashlib.sha256(encoded).hexdigest() == expected


def test_pointer_does_not_construct_or_call_another_click_detector(monkeypatch):
    from ring_python_sdk.touchpad import core
    original = core.ClickDetector
    detectors = []
    class ObservedClickDetector(original):
        def __init__(self):
            super().__init__()
            self.probabilities = self.movements = 0
            detectors.append(self)
        def probability(self, *args):
            self.probabilities += 1
            return super().probability(*args)
        def movement(self, *args):
            self.movements += 1
            return super().movement(*args)
    monkeypatch.setattr(core, 'ClickDetector', ObservedClickDetector)
    processor = TouchpadProcessor(backbone=Backbone())
    processor.enable_pointer_moves()
    assert len(detectors) == 1
    for seq in range(30):
        now = 1 + seq * .05
        processor.feed(packet(seq), arrival=now, now=now)
        processor.poll(now)
        before = detectors[0].probabilities, detectors[0].movements
        for j in range(10):
            processor.poll_pointer(now+j*.005)
        assert (detectors[0].probabilities, detectors[0].movements) == before
    assert detectors[0].probabilities > 0 and detectors[0].movements > 0
