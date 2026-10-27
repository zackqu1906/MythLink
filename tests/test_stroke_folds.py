"""Fine fold ranking uses all shapes and keeps the broad five-code recall."""
from copy import deepcopy
import gzip
from importlib.resources import files
import json

import pytest

from proximic_ring.touchpad.stroke.stroke_folds import FOLD_GROUPS, FOLD_NAMES, coarse_order, fold_orders
from proximic_ring.touchpad.stroke.stroke_fuzzy import FuzzyPolicy, retune_evidence, stroke_evidence
from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary


def ink(shape, scores=None):
    scores = scores or {name: .001 if name == shape else .18 for name in FOLD_NAMES}
    return stroke_evidence('z', dict(category='z', shape=shape,
        category_scores=[dict(category='z', shape=shape, distance=min(scores.values()))],
        shape_scores=[dict(category='z', shape=name, distance=value) for name, value in scores.items()]))


def dictionary(entries, orders, ranks=None):
    return StrokeDictionary(dict(templates=[], entries=entries, ranks=ranks or {}),
                            fold_orders={char: (coarse_order(order), order) for char, order in orders.items()})


def test_fine_ranking_runs_even_with_only_one_coarse_class_before_truncation():
    # The matching shape begins below the old top 30, so reranking a shortlist fails.
    chars = [chr(0x4e00+i) for i in range(45)]
    entries = [[char, 'zz', 1] for char in chars]
    orders = {char: 'rr' for char in chars}
    orders[chars[-1]] = 'mr'
    ranks = {char: index for index, char in enumerate(chars)}
    d = dictionary(entries, orders, ranks)
    assert chars[-1] not in d.candidates('z')
    rows = d.fuzzy_candidates([ink('撇点')], limit=1)
    assert rows[0]['char'] == chars[-1] and rows[0]['fold_matches'][0]['expected'] == ['撇点']
    all_rows = d.fuzzy_candidates([ink('撇点')], limit=100)
    assert {row['char'] for row in all_rows} == set(chars)


def test_close_fold_can_win_with_context_and_is_corrected_without_changing_observation():
    from proximic_ring.touchpad.stroke.stroke_context import StrokeContext
    d = dictionary([['甲','zz',1], ['乙','zz',1]], {'甲':'rr','乙':'cr'}, {'甲':0,'乙':0})
    evidence = [ink('横折钩', {'横折钩':.001, '横折':.012, '撇点':.18})]
    before = deepcopy(evidence)
    assert d.fuzzy_candidates(evidence)[0]['char'] == '甲'
    predictor = StrokeContext(dict(maxContext=1, total=100, unigrams={'甲':50,'乙':50},
                                  contexts={'我':[100,'乙',[100]]}))
    rows = d.fuzzy_candidates(evidence, predictor=predictor, context='我')
    assert rows[0]['char'] == '乙'
    assert rows[0]['fold_corrections'] == [0] and rows[0]['corrections'] == []
    assert rows[0]['fold_compatible'] and evidence == before


def test_later_fold_evidence_resolves_earlier_ambiguity_with_no_rescue_budget():
    d = dictionary([['甲','zz',1], ['乙','zz',1]], {'甲':'rr','乙':'mc'}, {'甲':0,'乙':0})
    first = ink('横折钩', {'横折钩':.001, '撇点':.02, '横折':.1})
    second = ink('横折', {'横折钩':.18, '撇点':.2, '横折':.001})
    assert d.fuzzy_candidates([first])[0]['char'] == '甲'
    rows = d.fuzzy_candidates([first, second])
    assert rows[0]['char'] == '乙' and rows[0]['fold_corrections'] == [0]
    assert rows[0]['rescues'] == 0


def test_shared_cnchar_names_are_equivalent_not_a_spurious_correction():
    d = dictionary([['甲','zz',1]], {'甲':'er'})
    row = d.fuzzy_candidates([ink('横钩', {'横钩':.001,'横撇':.08,'横折':.2})])[0]
    assert row['fold_matches'][0]['cost'] == 0. and row['fold_corrections'] == []
    assert set(row['fold_matches'][0]['expected']) == {'横钩','横撇'}


@pytest.mark.parametrize('shape,order', [('横折弯钩','o'), ('竖折','b')])
def test_recording_names_missing_from_cnchar_are_mapped_to_the_shared_shape(shape, order):
    d = dictionary([['甲','z',1]], {'甲':order})
    row = d.fuzzy_candidates([ink(shape, {shape:.001,'横折钩':.18})])[0]
    assert row['fold_matches'][0]['cost'] == 0. and row['fold_corrections'] == []


def test_complete_but_incompatible_short_character_no_longer_blocks_fitting_prefix():
    d = dictionary([['乙','z',1], ['女','zph',1]], {'乙':'o', '女':'msj'}, {'乙':0,'女':30})
    assert d.candidates('z')[0] == '乙'
    rows = d.fuzzy_candidates([ink('撇点')])
    assert [row['char'] for row in rows] == ['女','乙']
    assert rows[1]['exact_match'] and not rows[1]['fold_compatible']
    # A nearby fold interpretation keeps complete-match priority.
    near = ink('撇点', {'撇点':.001, '横斜钩':.025})
    assert d.fuzzy_candidates([near])[0]['char'] == '乙'


def test_variant_annotations_only_apply_to_verified_shared_prefixes_and_do_not_leak():
    d = dictionary([['甲','zhz',1], ['甲','zsz',1], ['乙','zhz',1]], {'甲':'rjr','乙':'mjm'})
    rows = d.fuzzy_candidates([ink('撇点')])
    assert rows[0]['char'] == '乙'
    # The second fold cannot be aligned through an earlier h/s order mismatch.
    evidence = [stroke_evidence('z'), stroke_evidence('s'), ink('撇点')]
    row = d.fuzzy_candidates(evidence)[0]
    assert row['char'] == '甲' and row['fold_matches'] == []
    assert row['fold_score'] == 0.
    # Two variants must not produce duplicate character results.
    assert len(rows) == 2


def test_manual_unknown_and_unreliable_ink_preserve_order_and_membership():
    d = dictionary([['甲','zz',1], ['乙','zz',1], ['丙','zz',1]], {'甲':'rr','乙':'mr'},
                   {'甲':0,'乙':1,'丙':2})
    invalid = ink('撇点', {'撇点':.09,'横折钩':.1})
    category_only = stroke_evidence('z', dict(category='z', shape='撇点', category_scores=[
        dict(category='z', distance=.001)]))
    for stroke in (stroke_evidence('z'), invalid, category_only,
                   ink('撇点', {'撇点':float('nan'),'横折钩':float('inf'),'横折':-1})):
        rows = d.fuzzy_candidates([stroke])
        assert [r['char'] for r in rows] == d.candidates('z')
    rows = d.fuzzy_candidates([ink('撇点')])
    unknown = next(row for row in rows if row['char'] == '丙')
    assert unknown['fold_score'] == 0. and not unknown['fold_matches']
    assert {r['char'] for r in rows} == set('甲乙丙')
    # Injected dictionaries never accidentally inherit built-in character annotations.
    plain = StrokeDictionary(dict(templates=[], entries=[['女','zz',1]], ranks={}))
    assert not plain._fold_orders


def test_fuzzy_strength_flattens_fold_penalties_and_retains_raw_scores():
    d = dictionary([['甲','zz',1]], {'甲':'rr'})
    evidence = [ink('横折', {'横折':.001,'横折钩':.06})]
    low = retune_evidence(evidence, FuzzyPolicy(0))
    high = retune_evidence(evidence, FuzzyPolicy(100))
    a = d.fuzzy_candidates(low, policy=FuzzyPolicy(0))[0]
    b = d.fuzzy_candidates(high, policy=FuzzyPolicy(100))[0]
    assert b['fold_score'] < a['fold_score']
    assert high[0]['recognition'] == low[0]['recognition']


def test_shipped_orders_align_to_actual_dictionary_and_cover_common_fold_characters():
    d = StrokeDictionary()
    orders = fold_orders()
    with gzip.open(files('proximic_ring').joinpath('assets/stroke-folds.json.gz'), 'rt', encoding='utf-8') as stream:
        data = json.load(stream)
    assert data['source']['version'] == '3.2.6' and len(data['source']['sha256']) == 64
    for char, (code, order) in orders.items():
        assert any(entry[1] == code for entry in d.by_character[char])
        assert len(code) == len(order)
    for char in '女好如也已己子习书马':
        assert char in orders
    assert len(orders) > 6000


@pytest.mark.parametrize('shape,target', [('撇点','女'), ('横折钩','习'), ('横撇','又')])
def test_real_short_prefixes_promote_the_expected_fold_family(shape, target):
    from proximic_ring.touchpad.stroke.stroke_common import common_characters
    d = StrokeDictionary()
    rows = d.fuzzy_candidates([ink(shape)], allowed_characters=common_characters())
    assert target in [r['char'] for r in rows[:5]]
    assert next(r for r in rows if r['char'] == target)['fold_matches'][0]['cost'] == 0.


def test_dtw_trace_scores_reach_candidate_ranking_without_a_second_recognition():
    d = StrokeDictionary(recognizer='dtw')
    # Use the same recorded trace that supplies a baseline recognition template.
    template = next(t for t in d.templates if t['shape'] == '撇点')
    result = d.recognize(template.get('raw_trace', template['trace']))
    assert result['category'] == 'z'
    rows = d.fuzzy_candidates([stroke_evidence(result['category'], result)], allowed_characters={'女','乙','也'})
    assert rows[0]['char'] == '女' and rows[0]['fold_matches']
