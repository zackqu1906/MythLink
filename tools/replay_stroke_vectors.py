"""Paired replay of stroke-vector ranking: off, complete-only (v1), prefix (v2).

Two sources, both with recorded recognition results and contact trajectories:
- sentence trials (data/stroke-test): every written prefix of a committed
  character, with the text committed so far as context;
- whole-character layout recordings (data/recordings/stroke-layout): every
  prefix of each character, no context.

The target is used only to score already-generated candidate lists. This is
retrospective ranking, not input speed. Prefix weights tuned here on the same
participant's data are optimistic; report both sources and the trial halves.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics
import sys
import time
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from evaluate_stroke_llm import load_cases, metrics
from proximic_ring.touchpad.stroke.stroke_common import common_characters
from proximic_ring.touchpad.stroke.stroke_context import StrokeContext
from proximic_ring.touchpad.stroke.stroke_fuzzy import FuzzyPolicy, stroke_evidence
from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary
from proximic_ring.touchpad.stroke.stroke_vectors import StrokeVectorModel, stroke_vector

LAYOUT = ROOT/'data/recordings/stroke-layout'
LAYOUT_FILES = ('stroke-layout-P01-train-50.json', 'stroke-layout-P01-train-50-2.json')


def layout_cases(paths):
    cases = []
    for path in paths:
        if not path.exists():
            continue
        for sample in json.loads(path.read_text(encoding='utf-8'))['samples']:
            results = sample.get('perStrokeBaseline') or []
            if len(results) != len(sample['contacts']):
                continue
            ink = []
            for contact, result in zip(sample['contacts'], results):
                stroke = stroke_evidence(result['category'], result)
                stroke['vector'] = stroke_vector([(p['x'], p['y']) for p in contact['points']])
                ink.append(stroke)
                cases.append(dict(source='layout', trial=path.name, target=sample['task']['character'],
                                  context='', evidence=list(ink), level=50, fuzzy=True))
    return cases


def ranks(cases, dictionary, model, predictor, allowed):
    dictionary.stroke_vectors = model
    result, timings = [], []
    for case in cases:
        start = time.perf_counter()
        rows = dictionary.fuzzy_candidates(case['evidence'], predictor=predictor,
            context=case['context'], allowed_characters=allowed, policy=FuzzyPolicy(case['level']))
        timings.append((time.perf_counter()-start)*1000)
        chars = [row['char'] for row in rows]
        result.append(chars.index(case['target'])+1 if case['target'] in chars else None)
    return result, timings


def summarize(cases, rank_lists):
    out = {}
    groups = defaultdict(list)
    for i, case in enumerate(cases):
        k = len(case['evidence'])
        groups[(case['source'], 'all')].append(i)
        groups[(case['source'], f'k={k}' if k <= 5 else 'k>=6')].append(i)
        if case['source'] == 'trial':
            groups[('trial', 'half=' + ('A' if zlib.crc32(case['trial'].encode()) % 2 else 'B'))].append(i)
    for (source, bucket), members in sorted(groups.items()):
        out[f'{source}/{bucket}'] = {name: metrics([dict(rank=ranks_[i]) for i in members], 'rank')
                                     for name, ranks_ in rank_lists.items()}
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--records', type=Path, default=ROOT/'data/stroke-test')
    parser.add_argument('--weights', type=float, nargs='*', default=[.25, .5, 1., 2.])
    parser.add_argument('--fold-free-weights', type=float, nargs='*', default=[1., 2.])
    parser.add_argument('--models', nargs='*', help='Only replay these model names')
    parser.add_argument('--max-strokes', type=int, default=8)
    parser.add_argument('--output', type=Path, default=ROOT/'data/diagnostics/stroke-vectors/replay.json')
    args = parser.parse_args()

    trial, trials, excluded = load_cases(args.records, max_strokes=args.max_strokes, vectors=True)
    trial = [dict(case, source='trial') for case in trial if case['fuzzy']]
    cases = trial + layout_cases([LAYOUT/name for name in LAYOUT_FILES])
    dictionary, predictor, allowed = StrokeDictionary(recognizer='dtw'), StrokeContext(), common_characters()
    models = dict(off=None, complete=StrokeVectorModel(scope='complete'))
    models.update({f'prefix{w:g}': StrokeVectorModel(scope='prefix', prefix_weight=w) for w in args.weights})
    models.update({f'prefix{w:g}-nofold': StrokeVectorModel(scope='prefix', prefix_weight=w, skip_folds=True)
                   for w in args.fold_free_weights})
    if args.models:
        models = {name: model for name, model in models.items() if name in args.models}
    rank_lists, latency = {}, {}
    for name, model in models.items():
        rank_lists[name], timings = ranks(cases, dictionary, model, predictor, allowed)
        latency[name] = dict(median_ms=statistics.median(timings), max_ms=max(timings))
        print(name, json.dumps(metrics([dict(rank=r) for r in rank_lists[name]], 'rank')), latency[name], flush=True)
    summary = summarize(cases, rank_lists)
    rows = [dict(source=c['source'], trial=c['trial'], target=c['target'], k=len(c['evidence']),
                 code=''.join(s['category'] for s in c['evidence']),
                 **{name: rank_lists[name][i] for name in models}) for i, c in enumerate(cases)]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dict(description=__doc__, trials=len(trials), excluded=len(excluded),
        cases=len(cases), latency=latency, summary=summary, rows=rows), ensure_ascii=False, indent=1), encoding='utf-8')
    for key, values in summary.items():
        print(key, ' '.join(f"{name}:{v['top1']:.3f}/{v['top6']:.3f}/{v['mrr']:.3f}" for name, v in values.items()
                            if v['samples']), f"n={next(iter(values.values()))['samples']}")


if __name__ == '__main__':
    main()
