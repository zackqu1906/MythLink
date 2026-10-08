"""Predefined diagnostic stress tests, no hyperparameter adjustment from test results."""
import argparse
import json
from pathlib import Path
import time

import numpy as np
import torch

from benchmark_stroke_models import (ROOT, SEEDS, CONFIGS, DTW_WEIGHTS, features, fit_tcn,
    predict_tcn, dtw_matrix, nearest_scores, metrics, grouped_folds)
from evaluate_stroke_samples import load_labels
from proximic_ring.stroke_input import StrokeDictionary


def run(directory, main_report, output):
    torch.set_num_threads(1)
    rows, _ = load_labels(directory)
    rows = [r for r in rows if r['rating'] != 3]
    reference = json.loads(main_report.read_text(encoding='utf-8'))
    assert [(r['file'], r['trial_index']) for r in rows] == [(r['file'], r['trial_index']) for r in reference['dataset']]
    names, categories = reference['classes'], reference['categories']
    labels = np.array([names.index(r['target']['shape']) for r in rows])
    values = [features(r['value']['points']) for r in rows]
    sequence, summary = np.stack([v[0] for v in values]), np.stack([v[1] for v in values])
    dictionary = StrokeDictionary()
    templates = [t for t in dictionary.templates if t['shape'] in names]
    template_sequence = np.stack([features(t['raw_trace'])[0] for t in templates])
    template_labels = np.array([names.index(t['shape']) for t in templates])
    distances = dtw_matrix(sequence, np.concatenate([template_sequence, sequence]), DTW_WEIGHTS[0])
    variants = {s: sorted(set(r['target']['variant'] for r in rows if r['target']['shape'] == s)) for s in names}
    variant_groups = [variants[r['target']['shape']].index(r['target']['variant']) % 3 for r in rows]
    rankings = {method: np.zeros((len(rows), len(names)), dtype=int) for method in ('dtw_unseen_variant', 'tcn_unseen_variant', 'tcn_no_absolute_size')}
    audit = []
    for group in range(3):
        train = np.array([i for i, g in enumerate(variant_groups) if g != group])
        test = np.array([i for i, g in enumerate(variant_groups) if g == group])
        assert set((rows[i]['target']['shape'], rows[i]['target']['variant']) for i in train).isdisjoint(
            (rows[i]['target']['shape'], rows[i]['target']['variant']) for i in test)
        columns = np.concatenate([np.arange(len(templates)), len(templates)+train])
        scores = nearest_scores(distances[np.ix_(test, columns)], np.concatenate([template_labels, labels[train]]), len(names))
        rankings['dtw_unseen_variant'][test] = scores.argsort(axis=1)
        models = [fit_tcn(sequence, summary, labels, train, CONFIGS[0], seed, categories) for seed in SEEDS]
        probabilities = np.mean([predict_tcn(model, sequence[test], summary[test]) for model in models], axis=0)
        rankings['tcn_unseen_variant'][test] = (-probabilities).argsort(axis=1)
        audit.append(dict(train=train.tolist(), test=test.tolist()))
        print('Unseen variant group', group+1, 'done', flush=True)
    # Same outer rounds, fixed smallest config: remove absolute size to inspect dependence on amplitude.
    reduced = summary.copy()
    reduced[:, :3] = 0
    groups = [(r['file'], r['target']['round']) for r in rows]
    for train, test in grouped_folds(groups):
        models = [fit_tcn(sequence, reduced, labels, train, CONFIGS[0], seed, categories) for seed in SEEDS]
        probabilities = np.mean([predict_tcn(model, sequence[test], reduced[test]) for model in models], axis=0)
        rankings['tcn_no_absolute_size'][test] = (-probabilities).argsort(axis=1)
    # Full-library DTW uses outer training only; weights selected by the existing inner folds.
    full_names = sorted(set(t['shape'] for t in dictionary.templates))
    full_categories = [next(t['category'] for t in dictionary.templates if t['shape']==s) for s in full_names]
    full_labels = np.array([full_names.index(r['target']['shape']) for r in rows])
    all_sequence = np.stack([features(t['raw_trace'])[0] for t in dictionary.templates])
    all_labels = np.array([full_names.index(t['shape']) for t in dictionary.templates])
    full_distances = {w: dtw_matrix(sequence, np.concatenate([all_sequence, sequence]), w) for w in DTW_WEIGHTS}
    full_ranks = np.zeros((len(rows), len(full_names)), dtype=int)
    full_latency = []
    for fold in reference['folds']:
        train, test = np.array(fold['train']), np.array(fold['test'])
        columns = np.concatenate([np.arange(len(all_sequence)), len(all_sequence)+train])
        scores = nearest_scores(full_distances[fold['selected_dtw']][np.ix_(test, columns)],
            np.concatenate([all_labels, full_labels[train]]), len(full_names))
        full_ranks[test] = scores.argsort(axis=1)
        for index in test:
            start = time.perf_counter()
            x, _ = features(rows[index]['value']['points'])
            dtw_matrix(x[None], np.concatenate([all_sequence, sequence[train]]), fold['selected_dtw'])
            full_latency.append((time.perf_counter()-start)*1000)
    # Metrics omit absent target classes for macro recall, while rankings retain all library classes.
    full = metrics(full_ranks, full_labels, full_names, full_categories, full_latency)
    recalls = [g['shape_top1']/g['count'] for key, g in full['groups'].items() if key != 'all' and g['count']]
    full['macro_shape_accuracy'] = float(np.mean(recalls))
    report = dict(note='Diagnostic stress only. Fixed first configs; no tuning after viewing results. '
        'Variants are held out completely per shape. Still the same recording session, not independent wearing. '
        'No-absolute-size removes length/width/height but retains normalized geometry and endpoint ratio. '
        'TCN learns only collected classes. DTW fixed library includes all prior recordings plus outer training paths.',
        variant_folds=audit, methods={m: metrics(r, labels, names, categories, [0.]) for m, r in rankings.items()},
        full_library_dtw=full, predictions={m: r.tolist() for m, r in rankings.items()},
        full_predictions=full_ranks.tolist(), full_classes=full_names)
    for value in report['methods'].values():
        value['latency_ms'] = None  # These diagnostic runs were not timed.
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    for method, value in report['methods'].items(): print(method, value['groups']['all'], flush=True)
    print('Full library DTW', full['groups']['all'], full['latency_ms'], flush=True)
    print('Report', output, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=ROOT/'data/stroke-collection')
    parser.add_argument('--main-report', type=Path, default=ROOT/'data/stroke-collection/model-comparison.json')
    parser.add_argument('--output', type=Path, default=ROOT/'data/stroke-collection/model-stress.json')
    args = parser.parse_args()
    run(args.directory, args.main_report, args.output)
