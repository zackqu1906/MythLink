"""Nested grouped comparison. Experimental only; never changes runtime recognition."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys
import time

import numpy as np
import torch
from torch import nn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from proximic_ring.stroke_input import StrokeDictionary, _normalize
from proximic_ring.stroke_structure import splits, nearly_straight, compare_parts
from evaluate_stroke_samples import load_labels

CONFIGS = [dict(width=16, epochs=100), dict(width=24, epochs=160)]
DTW_WEIGHTS = [.15, .4]
SEEDS = [42, 43, 44]


def grouped_folds(groups):
    """Never split one session/round across a fold boundary."""
    unique = sorted(set(groups))
    if len(unique) < 4:
        raise ValueError('Need at least four independent round groups')
    blocks = [unique[i:i+2] for i in range(0, len(unique), 2)]
    return [(np.array([i for i, g in enumerate(groups) if g not in block]),
             np.array([i for i, g in enumerate(groups) if g in block])) for block in blocks]


def features(points, count=32):
    xy = np.asarray(_normalize(points, count), dtype=np.float32)
    delta = np.diff(xy, axis=0, prepend=xy[:1])
    direction = delta / np.maximum(np.linalg.norm(delta, axis=1, keepdims=True), 1e-6)
    raw = np.asarray(points, dtype=np.float32)
    length = np.linalg.norm(np.diff(raw, axis=0), axis=1).sum()
    width, height = np.ptp(raw, axis=0)
    summary = np.array([np.log1p(length), np.log1p(width), np.log1p(height),
        np.linalg.norm(raw[-1]-raw[0])/max(length, 1e-6)], dtype=np.float32)
    return np.concatenate([xy, direction], axis=1).T, summary


def dtw_matrix(queries, templates, weight, radius=6):
    """Banded monotonic alignment; no rotation, all pair comparisons vectorized."""
    q = np.asarray(queries, dtype=np.float32)
    t = np.asarray(templates, dtype=np.float32)
    count = q.shape[-1]
    if t.shape[-1] != count:
        raise ValueError('Query and template sampling counts must match')
    previous = np.full((q.shape[0], t.shape[0], count+1), np.inf, dtype=np.float32)
    previous[:, :, 0] = 0
    for i in range(1, count+1):
        current = np.full_like(previous, np.inf)
        for j in range(max(1, i-radius), min(count, i+radius)+1):
            difference = q[:, None, :, i-1]-t[None, :, :, j-1]
            cost = (difference[:, :, :2]**2).sum(axis=2)
            cost += weight*(difference[:, :, 2:]**2).sum(axis=2)
            current[:, :, j] = cost + np.minimum(np.minimum(previous[:, :, j],
                current[:, :, j-1]), previous[:, :, j-1])
        previous = current
    return previous[:, :, -1]/count


def nearest_scores(distances, template_labels, classes):
    return np.column_stack([distances[:, template_labels == k].min(axis=1)
                            for k in range(classes)])


class TinyTCN(nn.Module):
    def __init__(self, width, classes):
        super().__init__()
        self.sequence = nn.Sequential(nn.Conv1d(4, width, 5, padding=2), nn.GELU(),
            nn.Conv1d(width, width, 3, padding=2, dilation=2), nn.GELU(),
            nn.Conv1d(width, width, 3, padding=4, dilation=4), nn.GELU())
        self.head = nn.Sequential(nn.Linear(2*width+4, width), nn.GELU(),
            nn.Dropout(.15), nn.Linear(width, classes))

    def forward(self, sequence, summary):
        hidden = self.sequence(sequence)
        return self.head(torch.cat([hidden.mean(dim=2), hidden.amax(dim=2), summary], dim=1))


def fit_tcn(sequence, summary, labels, train, config, seed, categories):
    torch.manual_seed(seed)
    mean = summary[train].mean(axis=0)
    scale = np.maximum(summary[train].std(axis=0), 1e-5)
    x = torch.from_numpy(sequence[train])
    s = torch.from_numpy((summary[train]-mean)/scale)
    y = torch.from_numpy(labels[train]).long()
    model = TinyTCN(config['width'], len(categories))
    counts = np.bincount(labels[train], minlength=len(categories))
    if np.any(counts == 0):
        raise ValueError('Training fold lacks a class')
    weights = torch.tensor(1/counts, dtype=torch.float32)
    weights /= weights.mean()
    loss_fn = nn.CrossEntropyLoss(weight=weights)
    optimizer = torch.optim.AdamW(model.parameters(), lr=.003, weight_decay=.01)
    cats = sorted(set(categories))
    cat_y = torch.tensor([cats.index(categories[k]) for k in labels[train]])
    model.train()
    for _ in range(config['epochs']):
        optimizer.zero_grad()
        logits = model(x, s)
        cat_logits = torch.stack([torch.logsumexp(logits[:, [i for i, c in enumerate(categories) if c == cat]], dim=1)
                                  for cat in cats], dim=1)
        loss = loss_fn(logits, y)+.3*nn.functional.cross_entropy(cat_logits, cat_y)
        loss.backward()
        optimizer.step()
    model.eval()
    return model, mean, scale


def predict_tcn(fitted, sequence, summary):
    model, mean, scale = fitted
    with torch.inference_mode():
        return model(torch.from_numpy(sequence), torch.from_numpy((summary-mean)/scale)).softmax(dim=1).numpy()


def macro_accuracy(scores, labels):
    predictions = scores.argmin(axis=1)
    return float(np.mean([np.mean(predictions[labels == k] == k) for k in sorted(set(labels))]))


def metrics(ranking, labels, shape_names, categories, latency):
    groups = defaultdict(lambda: dict(count=0, shape_top1=0, shape_top2=0, category_top1=0, category_top2=0))
    confusion = Counter()
    for rank, label in zip(ranking, labels):
        cat_rank = list(dict.fromkeys(categories[i] for i in rank))
        actual = categories[label]
        for key in ('all', shape_names[label]):
            group = groups[key]
            group['count'] += 1
            group['shape_top1'] += bool(rank[0] == label)
            group['shape_top2'] += bool(label in rank[:2])
            group['category_top1'] += cat_rank[0] == actual
            group['category_top2'] += actual in cat_rank[:2]
        confusion[(shape_names[label], shape_names[rank[0]])] += 1
    times = sorted(latency)
    return dict(groups=dict(groups), macro_shape_accuracy=float(np.mean([
        g['shape_top1']/g['count'] for key, g in groups.items() if key != 'all'])),
        confusion=[dict(actual=a, predicted=p, count=n) for (a, p), n in confusion.items()],
        latency_ms=dict(median=float(np.median(times)), p95=times[int(.95*(len(times)-1))]))


def baseline_scores(dictionary, rows, names):
    """Same structured scores as production, restricted only for fair six-class comparison."""
    output = []
    for row in rows:
        points = row['value']['points']
        trace = np.asarray(_normalize(points))
        evidence = splits(points)
        straight = not evidence and nearly_straight(points)
        scores = dict.fromkeys(names, float('inf'))
        for i, template in enumerate(dictionary.templates):
            shape = template['shape']
            if shape not in scores: continue
            distance = float(((trace-np.asarray(template['trace']))**2).sum()/32)
            if shape in evidence and shape in dictionary._template_parts[i]:
                distance = compare_parts(evidence[shape], dictionary._template_parts[i][shape], distance)
            elif evidence and shape in {'横', '提', '竖', '撇', '点', '捺', '竖钩'}:
                distance += .025
            elif straight and shape == '竖钩': distance += .025
            scores[shape] = min(scores[shape], distance)
        output.append([scores[s] for s in names])
    return np.asarray(output)


def run(directory, output):
    torch.set_num_threads(1)
    rows, excluded = load_labels(directory)
    rows = [r for r in rows if r['rating'] != 3]
    names = sorted(set(r['target']['shape'] for r in rows))
    categories = [next(r['target']['category'] for r in rows if r['target']['shape'] == s) for s in names]
    groups = [(r['file'], r['target']['round']) for r in rows]
    labels = np.array([names.index(r['target']['shape']) for r in rows])
    extracted = [features(r['value']['points']) for r in rows]
    sequence = np.stack([x[0] for x in extracted])
    summary = np.stack([x[1] for x in extracted])
    dictionary = StrokeDictionary()
    templates = [t for t in dictionary.templates if t['shape'] in names]
    template_sequence = np.stack([features(t['raw_trace'])[0] for t in templates])
    template_labels = np.array([names.index(t['shape']) for t in templates])
    distances = {w: dtw_matrix(sequence, np.concatenate([template_sequence, sequence]), w) for w in DTW_WEIGHTS}
    print('Distance matrices ready; protocol frozen before fold evaluation.', flush=True)
    rankings = {method: np.zeros((len(rows), len(names)), dtype=int) for method in ('template_closed', 'dtw', 'tcn')}
    latency = {method: [] for method in rankings}
    start = time.perf_counter()
    baseline = baseline_scores(dictionary, rows, names)
    latency['template_closed'] = [(time.perf_counter()-start)*1000/len(rows)]*len(rows)
    rankings['template_closed'] = np.argsort(baseline, axis=1)
    full_baseline = [dictionary.recognize(r['value']['points']) for r in rows]
    audit = []
    seed_predictions = {seed: np.zeros(len(rows), dtype=int) for seed in SEEDS}
    for fold, (train, test) in enumerate(grouped_folds(groups)):
        inner_groups = [groups[i] for i in train]
        inner = [(train[a], train[b]) for a, b in grouped_folds(inner_groups)]
        dtw_validation, tcn_validation = [], []
        for weight in DTW_WEIGHTS:
            evaluations = []
            for fit, validation in inner:
                columns = np.concatenate([np.arange(len(templates)), len(templates)+fit])
                scores = nearest_scores(distances[weight][np.ix_(validation, columns)],
                    np.concatenate([template_labels, labels[fit]]), len(names))
                evaluations.append(macro_accuracy(scores, labels[validation]))
            dtw_validation.append(float(np.mean(evaluations)))
        for config in CONFIGS:
            evaluations = []
            for fit, validation in inner:
                model = fit_tcn(sequence, summary, labels, fit, config, 42, categories)
                scores = predict_tcn(model, sequence[validation], summary[validation])
                evaluations.append(macro_accuracy(-scores, labels[validation]))
            tcn_validation.append(float(np.mean(evaluations)))
        weight = DTW_WEIGHTS[int(np.argmax(dtw_validation))]
        config = CONFIGS[int(np.argmax(tcn_validation))]
        columns = np.concatenate([np.arange(len(templates)), len(templates)+train])
        scores = nearest_scores(distances[weight][np.ix_(test, columns)],
            np.concatenate([template_labels, labels[train]]), len(names))
        rankings['dtw'][test] = scores.argsort(axis=1)
        fitted = [fit_tcn(sequence, summary, labels, train, config, seed, categories) for seed in SEEDS]
        probabilities = [predict_tcn(model, sequence[test], summary[test]) for model in fitted]
        rankings['tcn'][test] = (-np.mean(probabilities, axis=0)).argsort(axis=1)
        for seed, probability in zip(SEEDS, probabilities): seed_predictions[seed][test] = probability.argmax(axis=1)
        # Per-stroke timing, including transforms and three-model ensemble.
        for index in test:
            start = time.perf_counter()
            x, s = features(rows[index]['value']['points'])
            dtw_matrix(x[None], np.concatenate([template_sequence, sequence[train]]), weight)
            latency['dtw'].append((time.perf_counter()-start)*1000)
            start = time.perf_counter()
            x, s = features(rows[index]['value']['points'])
            for model in fitted: predict_tcn(model, x[None], s[None])
            latency['tcn'].append((time.perf_counter()-start)*1000)
        # Audit every fit/validation/test membership; raw or enhanced samples never cross groups.
        item = dict(fold=fold+1, train=train.tolist(), test=test.tolist(),
            inner=[dict(train=a.tolist(), validation=b.tolist()) for a, b in inner],
            dtw_validation=dtw_validation, tcn_validation=tcn_validation,
            selected_dtw=weight, selected_tcn=config)
        audit.append(item)
        output.with_suffix('.progress.json').write_text(json.dumps(dict(protocol='nested grouped', folds=audit), indent=2), encoding='utf-8')
        print(f'Fold {fold+1}/5 done; DTW={weight}, TCN={config}', flush=True)
    report = dict(protocol=dict(outer='Leave consecutive two-round blocks out', inner='Same grouped split on outer training only',
        scope='Closed-set six collected shapes; not evidence of full-library recognition. Production baseline separately reported.',
        limitations='One participant, one recording session; data already viewed during previous rule development. No untouched test set.',
        tuning='Two predefined configs per method, fixed epochs, three TCN seeds averaged; no post-test tuning or augmentation.',
        timing='Single-thread CPU per complete stroke; excludes BLE and UI; baseline averaged batch time.',
        labels='Prompt targets, not old recognizer acceptance ratings; actual writing may differ from prompt.'),
        configs=dict(tcn=CONFIGS, dtw_direction_weights=DTW_WEIGHTS, dtw_radius=6, seeds=SEEDS),
        classes=names, categories=categories, excluded=excluded,
        dataset=[dict(file=r['file'], trial_index=r['trial_index'], target=r['target']) for r in rows],
        folds=audit, methods={method: metrics(rankings[method], labels, names, categories, latency[method]) for method in rankings},
        production_baseline=dict(count=len(rows), shape_correct=sum(p['shape']==r['target']['shape'] for p,r in zip(full_baseline,rows)),
            category_correct=sum(p['category']==r['target']['category'] for p,r in zip(full_baseline,rows))),
        seed_accuracy={seed: float(np.mean(values==labels)) for seed, values in seed_predictions.items()},
        predictions={method: rank.tolist() for method, rank in rankings.items()},
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        source_sha256={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in {Path(r['file']) for r in rows}})
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    for method, value in report['methods'].items(): print(method, value['groups']['all'], value['latency_ms'], flush=True)
    print('Report:', output, flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=ROOT/'data/stroke-collection')
    parser.add_argument('--output', type=Path, default=ROOT/'data/stroke-collection/model-comparison.json')
    args = parser.parse_args()
    run(args.directory, args.output)
