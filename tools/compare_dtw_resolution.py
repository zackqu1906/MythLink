"""Fixed split/weights DTW resolution check; not a new untouched evaluation."""
import json
import time
import numpy as np
from benchmark_stroke_models import ROOT, features, dtw_matrix, nearest_scores, metrics
from evaluate_stroke_samples import load_labels
from proximic_ring.stroke_input import StrokeDictionary


def run():
    directory = ROOT/'data/stroke-collection'
    reference = json.loads((directory/'model-comparison.json').read_text(encoding='utf-8'))
    rows, _ = load_labels(directory)
    rows = [r for r in rows if r['rating'] != 3]
    assert [(r['file'], r['trial_index']) for r in rows] == [(r['file'], r['trial_index']) for r in reference['dataset']]
    dictionary = StrokeDictionary()
    names = sorted(set(t['shape'] for t in dictionary.templates))
    cats = [next(t['category'] for t in dictionary.templates if t['shape']==s) for s in names]
    labels = np.array([names.index(r['target']['shape']) for r in rows])
    template_labels = np.array([names.index(t['shape']) for t in dictionary.templates])
    result, ranks = {}, {}
    for count in (32, 64):
        # Preserve the same relative alignment window: 6/32 versus 12/64.
        radius = 6*count//32
        sequence = np.stack([features(r['value']['points'], count)[0] for r in rows])
        templates = np.stack([features(t['raw_trace'], count)[0] for t in dictionary.templates])
        matrices = {w: dtw_matrix(sequence, np.concatenate([templates, sequence]), w, radius)
                    for w in sorted(set(f['selected_dtw'] for f in reference['folds']))}
        ranking = np.zeros((len(rows), len(names)), dtype=int)
        times = []
        for fold in reference['folds']:
            train, test = np.array(fold['train']), np.array(fold['test'])
            columns = np.concatenate([np.arange(len(templates)), len(templates)+train])
            scores = nearest_scores(matrices[fold['selected_dtw']][np.ix_(test, columns)],
                np.concatenate([template_labels, labels[train]]), len(names))
            ranking[test] = scores.argsort(axis=1)
            bank = np.concatenate([templates, sequence[train]])
            for index in test:
                for _ in range(3):
                    start = time.perf_counter()
                    x, _ = features(rows[index]['value']['points'], count)
                    distance = dtw_matrix(x[None], bank, fold['selected_dtw'], radius)
                    nearest_scores(distance, np.concatenate([template_labels, labels[train]]), len(names))
                    times.append((time.perf_counter()-start)*1000)
        result[count] = metrics(ranking, labels, names, cats, times)
        ranks[count] = ranking
        print(count, result[count]['groups']['all'], result[count]['latency_ms'], flush=True)
    changes = [dict(trial_index=row['trial_index'], target=row['target'],
        old=names[a[0]], new=names[b[0]]) for row, a, b in zip(rows, ranks[32], ranks[64]) if a[0]!=b[0]]
    output = directory/'dtw-resolution-comparison.json'
    output.write_text(json.dumps(dict(note='Existing data reused; fixed previous outer folds and weights, '
        'no new tuning. Original raw template traces; equal relative alignment windows. '
        'CPU timing includes features and ranking, excludes BLE/UI. Runtime untouched.',
        results=result, changes=changes, classes=names,
        predictions={n:r.tolist() for n,r in ranks.items()}),ensure_ascii=False,indent=2),encoding='utf-8')
    print('Changes', json.dumps(changes, ensure_ascii=True), flush=True)


if __name__ == '__main__': run()
