"""Dynamic tolerance reuses observed scores; it cannot edit ink or manual labels."""
import pytest

from proximic_ring.touchpad.stroke.stroke_fuzzy import FuzzyPolicy, stroke_evidence, retune_evidence, match_prefix
from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary


def result(category='s', **distances):
    return dict(category=category, shape='竖', category_scores=[
        dict(category=letter, distance=distance) for letter, distance in distances.items()])


def test_default_level_preserves_thresholds_and_left_dot_penalty():
    assert FuzzyPolicy().snapshot() == dict(version=1, level=50, bestDistanceLimit=.08,
        nearAbsoluteGap=.004, nearRelativeGap=.5, rescueGapLimit=.08,
        maxRescues=1, leftDotCost=.45)


@pytest.mark.parametrize('level', [-1, 101, 50.5, True])
def test_invalid_strength_is_rejected(level):
    with pytest.raises(ValueError): FuzzyPolicy(level)


def test_level_changes_membership_from_retained_scores_without_rerecognizing():
    original = stroke_evidence('s', result(s=.001, p=.071))
    d = StrokeDictionary(dict(templates=[], ranks={'小':0,'你':1},
                             entries=[['小','spn',1], ['你','pspzspn',1]]))
    low = retune_evidence([original], FuzzyPolicy(0))
    high = retune_evidence(low, FuzzyPolicy(100))
    assert [row['char'] for row in d.fuzzy_candidates(low, policy=FuzzyPolicy(0))] == ['小']
    assert {row['char'] for row in d.fuzzy_candidates(high, policy=FuzzyPolicy(100))} == {'小','你'}
    restored = retune_evidence(high, FuzzyPolicy(0))
    assert restored == low and all(item[0]['category']=='s' for item in (low,high,restored))
    assert original['recognition']['category_scores'][1]['distance']==.071


def test_high_degree_allows_two_bounded_rescues_and_low_keeps_one():
    raw = result(s=.001, p=.051)
    default = [stroke_evidence('s', raw)]*2
    high = retune_evidence(default, FuzzyPolicy(100))
    assert match_prefix('pp', default) is None
    assert match_prefix('pp', high, policy=FuzzyPolicy(100))['rescues']==2
    assert match_prefix('ppp', high+[high[0]], policy=FuzzyPolicy(100)) is None


def test_new_level_updates_nearby_mark_and_manual_is_locked():
    automatic = stroke_evidence('s', result(s=.01, p=.023))
    manual = stroke_evidence('p')
    assert not automatic['ambiguous']
    changed = retune_evidence([automatic,manual], FuzzyPolicy(100))
    assert changed[0]['ambiguous'] and changed[1] is manual
    assert match_prefix('sn', changed, left_dots=(1,), policy=FuzzyPolicy(100)) is None


def test_bad_scores_never_expand_even_at_maximum_and_unrelated_folds_remain_excluded():
    for raw in [result(s=.09,p=.091), result(s=.001,p=float('nan'), n=-1, z=float('inf'))]:
        assert stroke_evidence('s',raw,policy=FuzzyPolicy(100))['costs']=={'s':0.}
    raw=result(s=.001,z=.051)
    raw['category_scores'][1]['shape']='横折折折'
    assert 'z' not in stroke_evidence('s',raw,policy=FuzzyPolicy(100))['costs']


def test_left_dot_tolerance_changes_cost_without_mutating_static_assets():
    stroke=stroke_evidence('p',dict(category='p',distance=.001))
    low=match_prefix('n',[stroke],left_dots=(0,),policy=FuzzyPolicy(0))
    high=match_prefix('n',[stroke],left_dots=(0,),policy=FuzzyPolicy(100))
    assert low['cost']==.65 and high['cost']==.25
    assert low['left_dot_corrections']==high['left_dot_corrections']==[0]
