"""Adaptive alternatives must use physical ink and remain bounded by base settings."""
import math
import pytest

from proximic_ring.touchpad.stroke.stroke_kinematics import measure_stroke
from proximic_ring.touchpad.stroke.stroke_fuzzy import FuzzyPolicy, adaptive_policy, stroke_evidence, retune_evidence, match_prefix


def metrics(length, duration):
    return measure_stroke([(0, 0), (length, 0)], duration)


def raw(best=.001, alternative=.091):
    return dict(category='s', shape='竖', category_scores=[
        dict(category='s', distance=best), dict(category='p', distance=alternative)])


def test_length_uses_full_path_and_speed_uses_contact_duration():
    measured = measure_stroke([(0, 0), (3, 4), (0, 0)], 100)
    assert measured['length'] == 10 and measured['speed'] == 100
    assert measured['samples'] == 3 and measured['timing'] == 'confirmed_contact_frames'


@pytest.mark.parametrize('length,duration,boost', [
    (100, 500, 0), (15, 500, 15), (100, 62.5, 15), (15, 5, 30),
    (37.5, 500, 8), (100, 1000/12, 8)])
def test_slow_long_short_fast_and_combined_ink(length, duration, boost):
    effective, detail = adaptive_policy(FuzzyPolicy(50), metrics(length, duration))
    assert effective.level == 50+boost and detail['boost'] == boost


def test_only_this_stroke_expands_and_slider_retunes_from_preserved_metrics():
    ordinary = stroke_evidence('s', raw(), metrics=metrics(100, 500))
    quick = stroke_evidence('s', raw(), metrics=metrics(15, 5))
    assert 'p' not in ordinary['costs'] and 'p' in quick['costs']
    assert ordinary['category'] == quick['category'] == 's'
    low = retune_evidence([ordinary, quick], FuzzyPolicy(0))
    assert [s['adaptation']['effectiveLevel'] for s in low] == [0, 30]
    assert all('p' not in s['costs'] for s in low)
    restored = retune_evidence(low, FuzzyPolicy(50))
    assert restored == [ordinary, quick]
    assert match_prefix('pp', [quick, quick]) is None  # baseline word budget remains one
    assert match_prefix('pp', retune_evidence([quick, quick], FuzzyPolicy(75)),
                        policy=FuzzyPolicy(75))['rescues'] == 2


def test_left_dot_cost_uses_each_strokes_effective_level():
    quick = stroke_evidence('p', dict(category='p'), metrics=metrics(15, 5))
    assert match_prefix('n', [quick], left_dots=(0,))['cost'] == .33
    manual = stroke_evidence('p', metrics=metrics(15, 5))
    assert manual == stroke_evidence('p') and match_prefix('n', [manual], left_dots=(0,)) is None


@pytest.mark.parametrize('bad', [None, {}, dict(length=15, duration_ms=5),
    dict(length=15, duration_ms=0, timing='confirmed_contact_frames', units='sdk'),
    dict(length=math.nan, duration_ms=5, timing='confirmed_contact_frames', units='sdk')])
def test_unknown_or_invalid_timing_falls_back_to_slider(bad):
    effective, detail = adaptive_policy(FuzzyPolicy(50), bad)
    assert effective.level == 50 and not detail['applied']


def test_max_level_and_quality_gate_are_unchanged():
    effective, detail = adaptive_policy(FuzzyPolicy(95), metrics(15, 5))
    assert effective.level == 100 and detail['boost'] == 5
    assert stroke_evidence('s', raw(best=.09, alternative=.091),
                           metrics=metrics(15, 5))['costs'] == {'s': 0.}
    assert stroke_evidence('s', raw()) == stroke_evidence('s', raw(), metrics=None)
