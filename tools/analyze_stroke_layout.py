"""Offline feasibility check: can raw gyro tokens place strokes relative to each other?

The SDK touchpad model reports almost no movement while the hand is lifted, so
the gap between strokes is estimated here from the gyro tokens (T4–T6):

1. Gyro bias per sample: median of the quietest 10% frames in its window.
2. A 2×3 map from integrated gyro to model displacement is fitted on contact
   segments only, leaving the evaluated sample out (one map per session).
3. Lifted gaps carry a fixed lift/landing component (mostly vertical). A linear
   correction with offset, from mapped gyro gap to the template gap, is fitted
   on the other samples of the session (their prompted characters are labels).

Each sample is then compared, within its prompted group, against the
hanzi-writer-data medians (the data behind cnchar-draw) by these methods:

- gyroOnly: whole character rebuilt from gyro, stroke start/end points;
- strokes: each contact's start→end vector (model trajectory), no positions;
- boundaryX: per gap, new stroke start x minus the right edge of all earlier
  strokes (chained model strokes + corrected gaps), i.e. left/right frames;
- strokes+w·boundaryX for a few fixed weights (chosen on the same data, so
  only indicative).

compareStrokes in a task limits every method to a shared stroke prefix. Scores
are engineering distances, not probabilities; this is an exploratory check,
not a validated recognizer. Example:

python tools/analyze_stroke_layout.py data/recordings/stroke-layout/stroke-layout-<id>.json
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import tarfile

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GLYPHS = ROOT / 'tmp' / 'hanzi-writer-data-2.0.1.tgz'
SEGMENT_FRAMES = 40  # 200 ms contact pieces give the map more than one equation per stroke.
QUIET_FRACTION = .10


def load_tokens(path):
    tokens = {}
    with Path(path).open(encoding='utf-8') as stream:
        for line in stream:
            if not line.strip():
                continue
            record = json.loads(line)['tokens']
            for step, value in zip(record['steps'], record['values']):
                tokens[(record['epoch'], step)] = value
    return tokens


def load_medians(archive_path, characters):
    medians = {}
    with tarfile.open(archive_path) as archive:
        # One pass: name lookups rescan the compressed stream each time.
        for member in archive:
            name = Path(member.name).stem
            if member.isfile() and member.name.endswith('.json') and name in characters:
                medians[name] = json.load(archive.extractfile(member))['medians']
    return medians


def template_points(medians):
    """Stroke start/end points; hanzi-writer y grows upward, ours downward."""
    return np.array([point for stroke in medians for point in (stroke[0], stroke[-1])],
                    dtype=float) * (1, -1)


def normalize(points):
    centered = points - points.mean(axis=0)
    radius = math.sqrt((centered**2).sum(axis=1).mean())
    return centered / radius if radius > 1e-12 else None


def layout_cost(observed, template):
    a, b = normalize(observed), normalize(template)
    if a is None or b is None or len(a) != len(b):
        return None
    return float(((a-b)**2).sum(axis=1).mean())


def gyro_window(tokens, sample):
    epoch, start, end = sample['tokenEpoch'], sample['windowStartStep'], sample['windowEndStep']
    steps = range(start, end+1)
    missing = sum((epoch, s) not in tokens for s in steps)
    if missing:
        return None, missing
    gyro = np.array([tokens[(epoch, s)][3:6] for s in steps], dtype=float)
    norms = np.linalg.norm(gyro, axis=1)
    quiet = gyro[norms <= np.quantile(norms, QUIET_FRACTION)]
    return gyro - np.median(quiet, axis=0), 0


def contact_segments(sample, gyro):
    """(integrated gyro, model displacement) pairs for pieces of each contact."""
    start = sample['windowStartStep']
    pairs = []
    for contact in sample['contacts']:
        points = contact['points']
        for i in range(0, len(points)-1, SEGMENT_FRAMES):
            j = min(i+SEGMENT_FRAMES, len(points)-1)
            a, b = points[i]['step']-start, points[j]['step']-start
            # Point k holds moves through its own step; the first move is excluded.
            pairs.append((gyro[a+1:b+1].sum(axis=0),
                          (points[j]['x']-points[i]['x'], points[j]['y']-points[i]['y'])))
    return pairs


def fit_map(pairs):
    if len(pairs) < 6:
        return None
    g = np.array([p[0] for p in pairs])
    d = np.array([p[1] for p in pairs])
    solution, *_ = np.linalg.lstsq(g, d, rcond=None)
    return solution.T  # 2×3


def angle(a, b):
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na < 1e-9 or nb < 1e-9:
        return None
    return math.degrees(math.acos(max(-1., min(1., float(np.dot(a, b)/(na*nb))))))


def reconstruct(sample, gyro, mapping):
    path = np.vstack([np.zeros(2), np.cumsum(gyro @ mapping.T, axis=0)])
    start, last = sample['windowStartStep'], len(path)-1
    # path[k] integrates gyro through step start+k-1, as in contact_segments.
    ends = [(min(c['startStep']-start+1, last), min(c['endStep']-start, last)) for c in sample['contacts']]
    endpoints = np.array([path[i] for pair in ends for i in pair])
    gaps = [path[b2]-path[e1] for (_, e1), (b2, _) in zip(ends, ends[1:])]
    return endpoints, gaps


BOUNDARY_WEIGHTS = (.25, .5, 1.)
SIGN_MIN = .25  # template gap share of stroke size counted as clearly one side


def template_strokes(medians):
    return [np.array(stroke, float) * (1, -1) for stroke in medians]


def unit(vectors):
    return float(np.sqrt((np.asarray(vectors)**2).sum(axis=1).mean()))


def stroke_cost(observed, template):
    """Mean squared difference of start→end vectors after RMS normalization."""
    a, b = np.asarray(observed), np.asarray(template)
    return float(((a/unit(a) - b/unit(b))**2).sum(axis=1).mean())


def boundary_features(strokes):
    """Start x of each later stroke minus the right edge of all earlier strokes."""
    scale = unit([s[-1]-s[0] for s in strokes])
    return np.clip([(strokes[k][0][0] - max(s[:, 0].max() for s in strokes[:k])) / scale
                    for k in range(1, len(strokes))], -1, 1)


def chain(strokes, gaps):
    placed, position = [], np.zeros(2)
    for k, stroke in enumerate(strokes):
        if k:
            position = position + gaps[k-1]
        placed.append(stroke - stroke[0] + position)
        position = placed[-1][-1]
    return placed


def expected_gaps(strokes, template):
    """Template gaps in this sample's model units, scaled by its stroke vectors."""
    observed = np.array([s[-1]-s[0] for s in strokes])
    vectors = np.array([t[-1]-t[0] for t in template])
    scale = float((vectors*observed).sum() / (vectors*vectors).sum())
    return np.array([scale*(template[k+1][0]-template[k][-1]) for k in range(len(template)-1)]).reshape(-1, 2)


def fit_correction(items):
    observed = [it['rawGaps'] for it in items if len(it['rawGaps'])]
    if sum(len(o) for o in observed) < 6:
        return None
    observed = np.vstack(observed)
    expected = np.vstack([it['expectedGaps'] for it in items if len(it['rawGaps'])])
    solution, *_ = np.linalg.lstsq(np.hstack([observed, np.ones((len(observed), 1))]), expected, rcond=None)
    return solution


def summarize_methods(rows):
    result = {}
    for method in (rows[0]['predicted'] if rows else ()):
        by_group = defaultdict(lambda: dict(n=0, correct=0))
        for row in rows:
            by_group[row['group']]['n'] += 1
            by_group[row['group']]['correct'] += row['predicted'][method] == row['character']
        correct = sum(v['correct'] for v in by_group.values())
        result[method] = dict(top1=correct/len(rows),
                              byGroup={g: f"{v['correct']}/{v['n']}" for g, v in by_group.items()})
    return result


def analyze(session_paths, glyph_archive, token_paths=None):
    sessions = []
    for i, path in enumerate(session_paths):
        path = Path(path)
        data = json.loads(path.read_text(encoding='utf-8'))
        token_path = Path(token_paths[i]) if token_paths else path.with_suffix('.tokens.jsonl')
        sessions.append((path, data, load_tokens(token_path)))
    groups = {s['task']['group'] for _, data, _ in sessions for s in data['samples']}
    medians = load_medians(glyph_archive, {c for g in groups for c in g})

    report = dict(samples=[], skipped=defaultdict(int))
    contact_angles, scale_ratios, axis = [], [], defaultdict(list)
    for path, data, tokens in sessions:
        items = []
        for sample in data['samples']:
            character = sample['task']['character']
            if len(sample['contacts']) != sample['task']['expectedLogicalStrokes']:
                report['skipped']['stroke-count'] += 1
                continue
            if len(medians.get(character, ())) != len(sample['contacts']):
                report['skipped']['no-template'] += 1
                continue
            gyro, missing = gyro_window(tokens, sample)
            if gyro is None:
                report['skipped']['missing-tokens'] += 1
                continue
            strokes = [np.array([[p['x'], p['y']] for p in c['points']]) for c in sample['contacts']]
            items.append(dict(sample=sample, gyro=gyro, pairs=contact_segments(sample, gyro), strokes=strokes,
                              expectedGaps=expected_gaps(strokes, template_strokes(medians[character]))))
        for index, it in enumerate(items):
            # Leave this sample out so its own strokes never calibrate it.
            it['mapping'] = fit_map([p for k, o in enumerate(items) if k != index for p in o['pairs']])
            if it['mapping'] is None:
                report['skipped']['too-few-calibration-segments'] += 1
            else:
                it['endpoints'], gaps = reconstruct(it['sample'], it['gyro'], it['mapping'])
                it['rawGaps'] = np.array(gaps).reshape(-1, 2)
        items = [it for it in items if it['mapping'] is not None]
        for index, it in enumerate(items):
            sample, task = it['sample'], it['sample']['task']
            correction = fit_correction([o for k, o in enumerate(items) if k != index])
            if correction is None:
                report['skipped']['too-few-correction-gaps'] += 1
                continue
            for g, d in it['pairs']:
                predicted = it['mapping'] @ g
                value = angle(predicted, np.array(d))
                if value is not None:
                    contact_angles.append(value)
                    scale_ratios.append(float(np.linalg.norm(predicted)/np.linalg.norm(d)))
            gaps = np.hstack([it['rawGaps'], np.ones((len(it['rawGaps']), 1))]) @ correction
            size = unit([s[-1]-s[0] for s in it['strokes']])
            for o, e in zip(gaps/size, it['expectedGaps']/size):
                for j, name in enumerate('xy'):
                    axis[name].append((o[j], e[j]))
            n = task.get('compareStrokes') or len(it['strokes'])
            observed_vectors = [s[-1]-s[0] for s in it['strokes'][:n]]
            observed_boundary = boundary_features(chain(it['strokes'], gaps)[:n])
            costs = {}
            for candidate in task['group']:
                template = template_strokes(medians.get(candidate, []))
                if len(template) < n:
                    continue
                template = template[:n]
                costs[candidate] = dict(
                    gyroOnly=layout_cost(it['endpoints'][:2*n], template_points(medians[candidate][:n])),
                    strokes=stroke_cost(observed_vectors, [t[-1]-t[0] for t in template]),
                    boundaryX=float(((observed_boundary - boundary_features(template))**2).mean()))
            if task['character'] not in costs:
                report['skipped']['no-template'] += 1
                continue
            predicted = {method: min(costs, key=lambda c: costs[c][method])
                         for method in ('gyroOnly', 'strokes', 'boundaryX')}
            for weight in BOUNDARY_WEIGHTS:
                predicted[f'strokes+{weight}·boundaryX'] = min(
                    costs, key=lambda c: costs[c]['strokes'] + weight*costs[c]['boundaryX'])
            report['samples'].append(dict(session=data['sessionId'], phase=data['phase'],
                taskIndex=sample['taskIndex'], character=task['character'], group=task['group'],
                compareStrokes=n, predicted=predicted, costs=costs,
                correctedGaps=gaps.tolist(), observedBoundaryX=observed_boundary.tolist()))

    rows = report['samples']
    gap_axes = {}
    for name, pairs in axis.items():
        o, e = np.array(pairs).T
        clear = np.abs(e) > SIGN_MIN
        gap_axes[name] = dict(signAccuracy=float(np.mean(np.sign(o[clear]) == np.sign(e[clear]))) if clear.any() else None,
                              medianAbsError=float(np.median(np.abs(o-e))), n=len(o))
    report['summary'] = dict(
        evaluated=len(rows),
        chanceTop1=(sum(1/len(r['group']) for r in rows)/len(rows)) if rows else None,
        methods=summarize_methods(rows),
        correctedGapAxes=gap_axes,
        contactAngleErrorDeg=dict(median=float(np.median(contact_angles)),
                                  p90=float(np.quantile(contact_angles, .9)))
                             if contact_angles else None,
        contactScaleRatio=dict(median=float(np.median(scale_ratios)),
                               p10=float(np.quantile(scale_ratios, .1)),
                               p90=float(np.quantile(scale_ratios, .9)))
                          if scale_ratios else None,
        skipped={k: v for k, v in report['skipped'].items() if v})
    report['skipped'] = report['summary']['skipped']
    report['method'] = dict(segmentFrames=SEGMENT_FRAMES, quietFraction=QUIET_FRACTION,
                            mapping='per-session leave-one-sample-out least squares, gyro→model displacement',
                            gapCorrection='per-session leave-one-sample-out linear map with offset, gyro gap→template gap',
                            template='hanzi-writer-data medians (first/last point, start→end vector per stroke)',
                            boundaryWeights=list(BOUNDARY_WEIGHTS), signMinimum=SIGN_MIN)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('sessions', nargs='+', type=Path)
    parser.add_argument('--glyphs', type=Path, default=DEFAULT_GLYPHS,
                        help='hanzi-writer-data-2.0.1.tgz (same archive as build_stroke_glyphs.py)')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = analyze(args.sessions, args.glyphs)
    print(json.dumps(report['summary'], ensure_ascii=False, indent=2))
    output = args.output or ROOT/'data'/'diagnostics'/'stroke-layout'/'report.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'完整结果：{output}')


if __name__ == '__main__':
    main()
