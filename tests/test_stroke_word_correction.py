from copy import deepcopy
from itertools import product

import pytest

from proximic_ring.touchpad.stroke.stroke_context import StrokeContext
from proximic_ring.touchpad.stroke.stroke_fuzzy import stroke_evidence
from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary
from proximic_ring.touchpad.stroke.stroke_word_correction import (
    WholeCharacterCorrector, align_strokes, merge_candidates,
)


def corrector(entries=None):
    return WholeCharacterCorrector(entries or [['我', 'phshzpn', 1]], {'我': 1})


@pytest.mark.parametrize('observed,kind', [
    ('phshhpn', 'substitute'),  # Confidently wrong category, no trace ambiguity.
    ('hshzpn', 'missing'),     # Missing first stroke.
    ('phshpn', 'missing'),     # Missing an internal stroke.
    ('phshzzpn', 'extra'),     # Repeated stroke.
    ('phshzpnh', 'extra'),     # Extra final stroke.
    ('phszhpn', 'transpose'),  # Adjacent order swap.
])
def test_recovers_complete_character_without_rewriting_manual_evidence(observed, kind):
    evidence = [stroke_evidence(c) for c in observed]
    before = deepcopy(evidence)
    row = corrector().candidates(observed, evidence=evidence)[0]
    assert row['char'] == '我' and row['code'] == 'phshzpn'
    assert row['edit_distance'] == 1 and row['edits'][0]['kind'] == kind
    assert evidence == before and all(e['manual'] for e in evidence)
    assert len(row['expected_to_observed']) == len(row['code'])
    assert all(0 <= i < len(observed) for i in row['corrections'])
    assert all(0 <= i < len(row['code']) for i in row['glyph_corrections'])


def test_bounded_combined_errors_and_unfinished_input():
    assert corrector().candidates('nhshpn')[0]['edit_distance'] == 2
    for observed in ['', 'p', 'ph', 'phsh', 'phshz', 'phshzp', 'phshzpn', 'nnnnnnn', 'xphshpn', 'h' * 33]:
        assert not corrector().candidates(observed)
    # Short words get one edit at most; two-edit recovery needs six inputs.
    assert not corrector([['中', 'szhsh', 1]]).candidates('nnzsh')


def test_scope_variants_deduplication_and_valid_existing_candidate():
    d = StrokeDictionary(dict(templates=[], ranks={'我': 1, '甲': 2, '龘': 0},
        entries=[['我', 'phshzpn', 1], ['我', 'phshznp', 1], ['甲', 'phshhpn', 1], ['龘', 'phshspn', 1]]))
    c = WholeCharacterCorrector(d.entries, d.ranks)
    normal = d.candidates('phshhpn')
    assert normal == ['甲']
    rows = c.candidates('phshhpn', allowed_characters={'我', '甲'})
    assert [r['char'] for r in rows] == ['我']
    assert merge_candidates(normal, rows) == ['甲', '我']
    assert not c.candidates('phshhpn', allowed_characters={'我'}, excluded_characters={'我'})
    # Indexing the common scope is also enforced when query has no scope.
    scoped = WholeCharacterCorrector(d.entries, d.ranks, allowed_characters={'我'})
    assert [r['char'] for r in scoped.candidates('phshhpn')] == ['我']


def test_context_orders_supported_repairs_but_cannot_invent_a_distant_character():
    c = WholeCharacterCorrector([['我', 'phshzpn', 1], ['你', 'phshspn', 1], ['他', 'nnnnnnn', 1]],
                                {'我': 1, '你': 1, '他': 1})
    predictor = StrokeContext(dict(maxContext=1, total=1000,
        unigrams={'我': 100, '你': 100, '他': 800}, contexts={'爱': [1000, '你他', [500, 500]]}))
    rows = c.candidates('phshhpn', context='爱', predictor=predictor)
    assert rows[0]['char'] == '你' and {r['char'] for r in rows} == {'我', '你'}


def test_merge_reserves_two_first_page_slots_and_preserves_every_normal_candidate():
    normal = list('一二三四五六七八九')
    repairs = [dict(char=c) for c in '我你一我他']
    merged = merge_candidates(normal, repairs)
    assert merged[:6] == list('一二三四我你')
    assert [c for c in merged if c in normal] == normal
    assert len(merged) == len(set(merged)) == 12
    assert merge_candidates([], repairs) == list('我你一他')


def test_trie_search_matches_full_alignment_including_swaps_and_repeated_strokes():
    codes = [''.join(chars) for n in range(1, 5) for chars in product('hs', repeat=n)]
    c = WholeCharacterCorrector([['我', code, 1] for code in codes], {})
    for observed in codes:
        for budget in (1, 2):
            expected = {code: align_strokes(observed, code)['distance'] for code in codes}
            expected = {code: distance for code, distance in expected.items() if 0 < distance <= budget}
            assert dict(c._nearby(observed, budget)) == expected


def test_real_dictionary_recovers_wo_with_no_nearby_recognition_alternative():
    from proximic_ring.touchpad.stroke.stroke_common import common_characters
    d = StrokeDictionary()
    evidence = [stroke_evidence(c) for c in 'phshhpn']
    # Expanded scope includes 犄 for this exact input; it must not prevent 我
    # from being suggested independently by whole-character repair.
    assert '我' not in [row['char'] for row in d.fuzzy_candidates(evidence, allowed_characters=common_characters())]
    c = WholeCharacterCorrector(d.entries, d.ranks, allowed_characters=common_characters())
    rows = c.candidates('phshhpn', evidence=evidence)
    assert rows[0]['char'] == '我' and rows[0]['code'] == 'phshzpn'


def test_real_prefix_is_not_labelled_as_a_missing_final_stroke():
    assert not corrector().candidates('phshzp')
    missing = corrector().candidates('phshpn')[0]
    assert missing['glyph_corrections'] == [4]
    assert missing['expected_to_observed'] == [0, 1, 2, 3, None, 4, 5]
