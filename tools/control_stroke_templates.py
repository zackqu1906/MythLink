"""Control for personal training data: Euclidean comparison gets the same templates as DTW."""
import json
from pathlib import Path

import numpy as np

from benchmark_stroke_models import ROOT, features, nearest_scores, metrics
from evaluate_stroke_samples import load_labels
from proximic_ring.stroke_input import StrokeDictionary


def run():
    directory = ROOT/'data/stroke-collection'
    reference = json.loads((directory/'model-comparison.json').read_text(encoding='utf-8'))
    rows, _ = load_labels(directory)
    rows = [r for r in rows if r['rating'] != 3]
    assert [(r['file'], r['trial_index']) for r in rows] == [(r['file'], r['trial_index']) for r in reference['dataset']]
    dictionary = StrokeDictionary()
    sequences = np.stack([features(r['value']['points'])[0] for r in rows])
    templates = np.stack([features(t['raw_trace'])[0] for t in dictionary.templates])
    all_sequences = np.concatenate([templates, sequences])
    # Same original recordings and personalized training examples as full-library DTW.
    differences = sequences[:, None, :2]-all_sequences[None, :, :2]
    distances = (differences**2).sum(axis=2).mean(axis=2)
    full_names = sorted(set(t['shape'] for t in dictionary.templates))
    cats = [next(t['category'] for t in dictionary.templates if t['shape']==s) for s in full_names]
    y = np.array([full_names.index(r['target']['shape']) for r in rows])
    template_y = np.array([full_names.index(t['shape']) for t in dictionary.templates])
    full_ranks = np.zeros((len(rows), len(full_names)), dtype=int)
    restricted_names = reference['classes']
    restricted_y = np.array([restricted_names.index(r['target']['shape']) for r in rows])
    restricted_cats = reference['categories']
    restricted_ranks = np.zeros((len(rows), len(restricted_names)), dtype=int)
    for fold in reference['folds']:
        train, test = np.array(fold['train']), np.array(fold['test'])
        columns = np.concatenate([np.arange(len(templates)), len(templates)+train])
        scores = nearest_scores(distances[np.ix_(test, columns)],
            np.concatenate([template_y, y[train]]), len(full_names))
        full_ranks[test] = scores.argsort(axis=1)
        restricted_ranks[test] = scores[:, [full_names.index(s) for s in restricted_names]].argsort(axis=1)
    results = dict(note='Fixed plain position-only Euclidean distance, no structure rules or tuning. '
        'Same outer training indices and fixed original template library as DTW; test paths never enter templates.',
        full=metrics(full_ranks, y, full_names, cats, [0.]),
        closed=metrics(restricted_ranks, restricted_y, restricted_names, restricted_cats, [0.]),
        predictions=dict(full=full_ranks.tolist(), closed=restricted_ranks.tolist()), full_classes=full_names)
    for value in (results['full'], results['closed']): value['latency_ms'] = None
    output = directory/'model-controls.json'
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print('full', results['full']['groups']['all'])
    print('closed', results['closed']['groups']['all'])
    print(output)


if __name__ == '__main__': run()
