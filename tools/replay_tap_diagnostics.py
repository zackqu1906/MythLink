"""Replay old SDK batches with their recorded explicit click decisions.

Runs the actual stroke consumer deterministically, preserving recorded batch
boundaries and times. This is an event replay, not new device inference.
"""
from collections import Counter
import argparse
import json
from pathlib import Path
import sys
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from proximic_ring.touchpad_strokes import TouchpadStrokeOutput
from ring_python_sdk.touchpad import TouchpadMove, TouchpadContact, TouchpadClick, TouchpadClickVerdict


def replay(path):
    rows = [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]
    batches, decisions = [], []
    types = dict(move=TouchpadMove, contact=TouchpadContact, click=TouchpadClick, click_verdict=TouchpadClickVerdict)
    for row in rows:
        if row['kind'] == 'sdk_click_verdict':
            decisions.append(TouchpadClickVerdict(row['start_step'], row['end_step'], row['reason'] == 'click', row['mono']))
        elif row['kind'] == 'sdk_batch':
            events = [types[value['kind']](**value) for value in row['events']]
            # Old recordings lacked verdict events; add the actual decisions that
            # the new SDK now publishes in that drain, rather than inventing labels.
            if not any(event.kind == 'click_verdict' for event in events):
                events += decisions
            decisions = []
            batches.append((row['mono'], events))
    written, taps = [], []
    output = TouchpadStrokeOutput(connection=threading.Event(), on_ready=lambda: None,
        on_end=lambda reason, error: (_ for _ in ()).throw(error) if error else None,
        on_stroke=written.append, on_tap=lambda: taps.append(True))
    clock = [rows[0]['mono']]

    class Wake:
        index = 0
        def wait(self, timeout):
            if self.index >= len(batches):
                output.stopped.set()
                return
            clock[0], events = batches[self.index]
            self.index += 1
            output.events.extend(events)
        def clear(self): pass
        def set(self): pass

    output.wake = Wake()
    with patch('proximic_ring.touchpad_strokes.time.monotonic', side_effect=lambda: clock[0]):
        output._run()
    old = [row for row in rows if row['kind'] == 'stroke']
    fingerprint = lambda points: tuple(tuple(point) for point in points)
    remaining = Counter(fingerprint(points) for points in written)
    removed = []
    for row in old:
        key = fingerprint(row['points'])
        if remaining[key]:
            remaining[key] -= 1
        else:
            removed.append(dict(time=row['time'], points=len(row['points']), shape=row['result']['shape']))
    return dict(source=str(Path(path).resolve()), original_strokes=len(old), replay_strokes=len(written),
                replay_taps=len(taps), removed_strokes=removed,
                unexpected_new_strokes=sum(remaining.values()), batches=len(batches),
                scope='Recorded SDK decisions replayed through fixed consumer; not a new real-device test.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = replay(args.log)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
