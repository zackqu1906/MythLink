"""Compare sampling resolutions without changing rules or adding training samples."""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from proximic_ring.stroke_input import StrokeDictionary
from evaluate_stroke_samples import load_labels


def compare(directory, repeats=5):
    labels, excluded = load_labels(directory)
    labels = [row for row in labels if row['rating'] != 3]
    if not labels:
        raise ValueError('No real annotated paths to compare')
    dictionaries = {n: StrokeDictionary(sample_count=n) for n in (32, 64, 96)}
    predictions = {n: [] for n in dictionaries}
    durations = {n: [] for n in dictionaries}
    # Warm all engines. Interleave resolution order to reduce timing bias.
    for dictionary in dictionaries.values():
        for row in labels[:5]: dictionary.recognize(row['value']['points'])
    for row in labels:
        for n, dictionary in dictionaries.items():
            predictions[n].append(dictionary.recognize(row['value']['points']))
    for repetition in range(repeats):
        order = (32, 64, 96) if repetition % 2 == 0 else (96, 64, 32)
        for row in labels:
            for n in order:
                start = time.perf_counter()
                dictionaries[n].recognize(row['value']['points'])
                durations[n].append((time.perf_counter()-start)*1000)
    results = {}
    for n, values in predictions.items():
        groups = defaultdict(lambda: dict(count=0, shape_correct=0, category_correct=0,
                                          improved=0, regressed=0))
        changes = []
        for index, (row, value) in enumerate(zip(labels, values)):
            target = row['target']
            baseline = predictions[32][index]
            old_ok = baseline['shape'] == target['shape']
            new_ok = value['shape'] == target['shape']
            for key in ('all', target['shape']):
                group = groups[key]
                group['count'] += 1
                group['shape_correct'] += new_ok
                group['category_correct'] += value['category'] == target['category']
                group['improved'] += new_ok and not old_ok
                group['regressed'] += old_ok and not new_ok
            if value['shape'] != baseline['shape'] or value['category'] != baseline['category']:
                changes.append(dict(file=row['file'], trial_index=row['trial_index'],
                                    target=target, baseline=baseline, prediction=value))
        timings = sorted(durations[n])
        prototype_errors = [t['shape'] for t in dictionaries[n].templates
            if dictionaries[n].recognize(t.get('raw_trace', t['trace']))['category'] != t['category']]
        results[n] = dict(groups=dict(groups), changes=changes,
            prototype_category_errors=prototype_errors,
            latency_ms=dict(median=statistics.median(timings),
                p95=timings[int(.95*(len(timings)-1))], max=max(timings)))
    return dict(note='Existing 190 paths reused for resolution comparison; not a new untouched test set. '
        'No rules tuned. Template source is original recordings, not interpolation of legacy traces. '
        'Timing is complete CPU recognition after stroke completion, excluding BLE and rendering.',
        repeats=repeats, excluded=excluded, results=results,
        asset_sha256=hashlib.sha256((ROOT/'src/proximic_ring/assets/strokes.json.gz').read_bytes()).hexdigest())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=ROOT/'data/stroke-collection')
    parser.add_argument('--output', type=Path, default=ROOT/'data/stroke-collection/resolution-comparison.json')
    args = parser.parse_args()
    result = compare(args.directory)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    for n, data in result['results'].items():
        print(n, json.dumps(dict(overall=data['groups']['all'], latency_ms=data['latency_ms'],
            prototype_category_errors=data['prototype_category_errors']), ensure_ascii=True))
    print(args.output)


if __name__ == '__main__':
    main()
