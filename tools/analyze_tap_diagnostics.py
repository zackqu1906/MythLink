"""Replay recorded SDK contacts and attribute published strokes to click verdicts."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from proximic_ring.stroke_input import StrokeCollector
from ring_python_sdk.touchpad import TouchpadContact, TouchpadMove, TouchpadClick, TouchpadClickVerdict


def analyze(path, all_clicks=False):
    content = Path(path).read_bytes()
    rows = [json.loads(line) for line in content.decode('utf-8').splitlines() if line.strip()]
    collector = StrokeCollector()
    candidates = defaultdict(list)
    verdicts = {row['end_step']: row for row in rows if row['kind'] == 'sdk_click_verdict'}
    for row in rows:
        if row['kind'] != 'sdk_batch':
            continue
        for data in row['events']:
            cls = {'move': TouchpadMove, 'contact': TouchpadContact, 'click': TouchpadClick,
                   'click_verdict': TouchpadClickVerdict}[data['kind']]
            event = cls(**data)
            start = collector.start_step
            end = event.step if event.kind == 'contact' and event.state == 'up' else collector.end_step
            points = collector.feed(event)
            if points is not None:
                candidates[tuple(tuple(point) for point in points)].append((start, end))
    failures = []
    for row in rows:
        if row['kind'] != 'stroke':
            continue
        points = row['points']
        matches = candidates.get(tuple(tuple(point) for point in points), [])
        boundary = matches.pop(0) if matches else (None, None)
        verdict = verdicts.get(boundary[1], {})
        length = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
        failures.append(dict(time=row['time'], trial_id=row['trial_id'],
            intended='click' if all_clicks else row.get('intended'),
            shape=row['result']['shape'], category=row['result']['category'],
            points=len(points), processed_path_length=length,
            processed_net_displacement=math.dist(points[0], points[-1]),
            start_step=boundary[0], end_step=boundary[1],
            sdk_reason=verdict.get('reason', 'unmatched'),
            duration_ms=verdict.get('duration_ms'),
            raw_net_displacement=verdict.get('raw_net_displacement')))
    stats = [row['stats'] for row in rows if row['kind'] == 'stats']
    result = dict(source=str(Path(path).resolve()), sha256=hashlib.sha256(content).hexdigest(),
        intent_source='User explicitly stated that all actions in this recording were clicks.' if all_clicks else
            'Continuous-click session metadata.' if rows[0].get('mode') == 'continuous_clicks' else 'No global intent override.',
        counts=dict(Counter(row['kind'] for row in rows)),
        synthetic=rows[0].get('synthetic'),
        click_verdicts=dict(Counter(row['reason'] for row in rows if row['kind'] == 'sdk_click_verdict')),
        firmware_classes=dict(Counter(str(row['event']['class_id']) for row in rows if row['kind'] == 'firmware_gesture')),
        stroke_rejection_reasons=dict(Counter(row['sdk_reason'] for row in failures)),
        observed_contact_resets=sum(row['kind'] == 'sdk_contact' and row['event']['state'] == 'reset' for row in rows),
        health_max={key: max((value.get(key, 0) for value in stats), default=0) for key in
            ['resets', 'invalid_packets', 'stale_packets', 'queue_overflows', 'sequence_gaps', 'reboots']},
        session_end=next((row for row in reversed(rows) if row['kind'] == 'session_end'), None),
        emitted_strokes=failures,
        limitations=['SDK contacts and firmware gestures are not counts of independently labeled physical actions.',
                     'No true strokes were performed, so this recording cannot measure short-stroke retention.',
                     'Event counts do not determine the number of independently labeled physical clicks.',
                     'Cross-channel temporal overlap is not required for the per-contact SDK rejection analysis.'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    parser.add_argument('--all-clicks', action='store_true', help='Only use when the human explicitly labeled the entire run as clicks')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = analyze(args.log, args.all_clicks)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({key: report[key] for key in ['stroke_rejection_reasons', 'click_verdicts', 'health_max']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
