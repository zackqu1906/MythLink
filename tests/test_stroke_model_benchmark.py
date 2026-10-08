"""Leakage boundaries and alignment behavior of the offline experiment."""
import importlib.util
from pathlib import Path
import sys

import numpy as np
import pytest

TOOLS = Path(__file__).resolve().parents[1]/'tools'
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location('stroke_benchmark', TOOLS/'benchmark_stroke_models.py')
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def test_nested_groups_never_cross_training_validation_or_test():
    groups = [('session', round_) for round_ in range(1, 11) for _ in range(19)]
    tested = []
    for train, test in benchmark.grouped_folds(groups):
        tested.extend(test.tolist())
        assert set(groups[i] for i in train).isdisjoint(groups[i] for i in test)
        for fit, validation in benchmark.grouped_folds([groups[i] for i in train]):
            actual_fit, actual_validation = train[fit], train[validation]
            assert set(actual_fit).isdisjoint(actual_validation)
            assert set(actual_fit).isdisjoint(test)
            assert set(actual_validation).isdisjoint(test)
            assert set(groups[i] for i in actual_fit).isdisjoint(groups[i] for i in actual_validation)
    assert sorted(tested) == list(range(190))


@pytest.mark.parametrize('count', [32, 64])
def test_dtw_preserves_direction_and_self_distance(count):
    right, _ = benchmark.features([(i, 0) for i in range(21)], count)
    left, _ = benchmark.features([(-i, 0) for i in range(21)], count)
    distances = benchmark.dtw_matrix(np.stack([right, left]), np.stack([right, left]), .4, 6*count//32)
    assert distances[0, 0] == 0 and distances[1, 1] == 0
    assert distances[0, 1] > 0 and distances[1, 0] > 0


def test_dtw_rejects_mismatched_resolution():
    a, _ = benchmark.features([(0, 0), (1, 0)], 32)
    b, _ = benchmark.features([(0, 0), (1, 0)], 64)
    with pytest.raises(ValueError, match='sampling counts'):
        benchmark.dtw_matrix(a[None], b[None], .15)


def test_template_scores_use_only_explicit_training_columns():
    # The closest value belongs to a held-out sample. It must never be a template.
    distances = np.array([[.7, .6, 0.]])
    train = np.array([0, 1])
    scores = benchmark.nearest_scores(distances[:, train], np.array([0, 1]), 2)
    assert scores.tolist() == [[.7, .6]]


def test_tcn_scaling_is_fit_on_training_only():
    sequence = np.zeros((6, 4, 32), dtype=np.float32)
    summary = np.arange(24, dtype=np.float32).reshape(6, 4)
    labels = np.array([0, 1, 0, 1, 0, 1])
    summary[4:] = 100000
    _, mean, scale = benchmark.fit_tcn(sequence, summary, labels, np.arange(4),
                                     dict(width=4, epochs=1), 42, ['h', 's'])
    assert np.allclose(mean, summary[:4].mean(axis=0))
    assert np.allclose(scale, summary[:4].std(axis=0))


def test_too_few_rounds_are_rejected():
    with pytest.raises(ValueError): benchmark.grouped_folds([1, 1, 2, 2])


def test_metrics_do_not_count_unobserved_targets_or_duplicate_codes():
    # Two z shapes must not consume both code slots; h remains the second code.
    rankings = np.array([[0, 1, 2]])
    result = benchmark.metrics(rankings, np.array([2]), ['折一', '折二', '横'], ['z', 'z', 'h'], [1.])
    assert result['groups']['all']['shape_top2'] == 0
    assert result['groups']['all']['category_top2'] == 1
    assert result['macro_shape_accuracy'] == 0
