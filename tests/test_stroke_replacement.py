"""Empty candidate recovery stays local to one nearby, observed stroke."""
from copy import deepcopy

import pytest

from proximic_ring.touchpad.stroke.stroke_context import StrokeContext
from proximic_ring.touchpad.stroke.stroke_fuzzy import replacement_options, stroke_evidence
from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary


def automatic(category, **distances):
    return stroke_evidence(category, dict(category_scores=[
        dict(category=letter, distance=distance, shape='横折' if letter == 'z' else '横')
        for letter, distance in distances.items()]))


def dictionary(entries):
    return StrokeDictionary(dict(templates=[], entries=entries,
        ranks={char: 1 for char, _, _ in entries}))


def test_replacement_is_limited_to_two_nearest_classes_and_bounded_distances():
    stroke = automatic('h', h=.001, z=.02, p=.03, n=.04, s=.05)
    assert set(replacement_options(stroke)) == {'z', 'p'}
    assert not replacement_options(automatic('h', h=.001, z=.09))
    assert not replacement_options(automatic('h', h=.079, z=.13))
    assert not replacement_options(automatic('h', h=.09, z=.091))
    assert not replacement_options(automatic('h', h=.001, z=float('nan'), p=-1, s=float('inf')))
    assert not replacement_options(stroke_evidence('h'))
    assert not replacement_options(stroke_evidence('h', dict(shape='横', distance=.001)))


@pytest.mark.parametrize('position', range(4))
def test_first_middle_and_last_stroke_can_be_replaced_without_changing_observation(position):
    d = dictionary([['字', 'zzzz', 1]])
    evidence = [stroke_evidence('z') for _ in range(4)]
    evidence[position] = automatic('h', h=.001, z=.03)
    before = deepcopy(evidence)
    rows = d.fuzzy_candidates(evidence)
    assert rows[0]['char'] == '字' and rows[0]['code'] == 'zzzz'
    # From the second stroke on, the far class covers what the empty-list fallback did.
    assert rows[0]['corrections'] == rows[0]['replacement_corrections'] == rows[0]['far_corrections'] == [position]
    assert not rows[0]['exact_match']
    assert evidence == before
    evidence[(position+1) % 4] = automatic('h', h=.001, z=.03)
    assert not d.fuzzy_candidates(evidence)  # Never replace two strokes.
    # Below the far-class level the original single-replacement fallback still answers.
    from proximic_ring.touchpad.stroke.stroke_fuzzy import FuzzyPolicy
    evidence[(position+1) % 4] = stroke_evidence('z')
    rows = d.fuzzy_candidates(evidence, policy=FuzzyPolicy(10))
    assert rows[0]['match_type'] == 'single_replacement' and rows[0]['replacement_corrections'] == [position]


def test_context_ranks_only_stroke_supported_replacements_before_truncation():
    d = dictionary([['乙', 'zz', 1], ['乙', 'zzz', 1], ['人', 'pz', 1], ['十', 'sz', 1]])
    evidence = [automatic('h', h=.001, z=.02, p=.03, s=.2), stroke_evidence('z')]
    predictor = StrokeContext(dict(maxContext=1, total=200,
        unigrams={'乙': 100, '人': 100, '十': 0}, contexts={'个': [200, '人十', [100, 100]]}))
    assert d.fuzzy_candidates(evidence)[0]['char'] == '乙'
    rows = d.fuzzy_candidates(evidence, predictor=predictor, context='个')
    assert [row['char'] for row in rows][:1] == ['人'] and '乙' in [row['char'] for row in rows]
    assert d.fuzzy_candidates(evidence, 1, predictor=predictor, context='个')[0]['char'] == '人'
    # A distant class is never free: 十 only enters at its far cost, behind 人.
    shi = next(row for row in rows if row['char'] == '十')
    assert shi['far_corrections'] == [0] and shi['cost'] > 3
    assert len([row for row in rows if row['char'] == '乙']) == 1
    assert d.fuzzy_candidates(evidence, allowed_characters={'十'})[0]['far_corrections'] == [0]


def test_normal_candidates_win_and_manual_input_stays_locked():
    d = dictionary([['一', 'h', 1], ['乙', 'z', 1]])
    evidence = [automatic('h', h=.001, z=.03)]
    rows = d.fuzzy_candidates(evidence)
    assert [row['char'] for row in rows] == ['一'] and not rows[0].get('replacement_corrections')
    assert d.fuzzy_candidates(evidence, allowed_characters={'乙'})[0]['char'] == '乙'
    assert not d.fuzzy_candidates([stroke_evidence('h')], allowed_characters={'乙'})


def test_partial_replacement_prefers_completed_words():
    d = dictionary([['乙', 'zz', 1], ['字', 'zzzz', 1]])
    evidence = [automatic('h', h=.001, z=.03), stroke_evidence('z')]
    rows = d.fuzzy_candidates(evidence)
    assert [row['char'] for row in rows] == ['乙', '字']
    assert rows[1]['code'] == 'zzzz' and rows[1]['replacement_corrections'] == [0]


def test_real_wo_recovers_a_confident_but_nearby_wrong_fifth_stroke():
    from proximic_ring.touchpad.stroke.stroke_common import common_characters
    from proximic_ring.touchpad.stroke.stroke_glyphs import candidate_glyph
    d = StrokeDictionary()
    evidence = [stroke_evidence(c) for c in 'phshhpn']
    evidence[4] = automatic('h', h=.001, z=.03, s=.04, p=.2, n=.3)
    # Restrict this legacy empty-candidate fallback test explicitly: the wider
    # default scope now has an exact matching 犄, which intentionally wins there.
    assert not d.candidates('phshhpn', allowed_characters={'我'})
    rows = d.fuzzy_candidates(evidence, allowed_characters={'我'})
    wo = next(row for row in rows if row['char'] == '我')
    assert wo['replacement_corrections'] == [4]
    visual = candidate_glyph('我', wo['code'], 7, corrections=(4,))
    assert visual['available'] and visual['states'] == ['entered']*4 + ['correction'] + ['entered']*2
    assert '第5笔' in visual['status']
