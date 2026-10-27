from proximic_ring.touchpad.stroke.stroke_fuzzy import stroke_evidence, match_prefix
from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary
from proximic_ring.touchpad.stroke.stroke_context import StrokeContext
import pytest


def recognized(category='s', **scores):
    return stroke_evidence(category, dict(category_scores=[
        dict(category=letter, distance=distance) for letter, distance in scores.items()]))


def dictionary(entries=None, ranks=None):
    return StrokeDictionary(dict(templates=[], entries=entries or [
        ['小', 'spn', 1], ['你', 'pspzspn', 1], ['十', 'hs', 1]],
        ranks=ranks or {'小': 0, '你': 1, '十': 2}))


def test_later_strokes_recover_an_earlier_alternative_without_rewriting_observation():
    d = dictionary()
    first = recognized(s=.001, p=.002, n=.2, h=.4, z=.4)
    assert d.fuzzy_candidates([first])[0]['char'] == '小'
    assert not d.candidates('ss')
    rows = d.fuzzy_candidates([first, stroke_evidence('s')])
    assert [row['char'] for row in rows] == ['你']
    assert rows[0]['corrections'] == [0] and first['category'] == 's'


def test_closest_trace_wins_when_priors_are_equal():
    d = dictionary(ranks={'小': 1, '你': 1})
    assert d.fuzzy_candidates([recognized(s=.001, p=.004)])[0]['char'] == '小'
    assert d.fuzzy_candidates([recognized('p', s=.004, p=.001)])[0]['char'] == '你'


def test_frequency_and_context_can_resolve_close_shapes_without_admitting_invalid_prefixes():
    d = dictionary(ranks={'小': 1, '你': 1, '十': 0})
    first = recognized(s=.001, p=.004)
    predictor = StrokeContext(dict(maxContext=4, total=100,
        unigrams={'小': 50, '你': 50, '十': 0}, contexts={'爱': [100, '你', [100]]}))
    assert d.fuzzy_candidates([first], predictor=predictor)[0]['char'] == '小'
    assert d.fuzzy_candidates([first], predictor=predictor, context='我爱')[0]['char'] == '你'
    assert '十' not in [row['char'] for row in d.fuzzy_candidates([first], predictor=predictor, context='爱')]
    frequent = StrokeContext(dict(maxContext=4, total=100, unigrams={'小': 1, '你': 99}, contexts={}))
    assert d.fuzzy_candidates([first], predictor=frequent)[0]['char'] == '你'


def test_rescue_is_limited_to_one_spn_substitution_and_manual_input_is_locked():
    first = recognized(s=0., p=.04, n=.06, h=.05, z=.05)
    assert not first['ambiguous']
    assert match_prefix('p', [first])['rescues'] == 1
    assert match_prefix('pn', [first, first]) is None
    assert match_prefix('h', [first]) is None
    assert match_prefix('z', [first]) is None
    assert match_prefix('p', [stroke_evidence('s')]) is None
    assert match_prefix('s', [first, first]) is None


def test_poor_or_invalid_scores_do_not_expand_into_wildcards():
    assert list(recognized(s=.1, p=.101)['costs']) == ['s']
    assert list(recognized(s=0., p=float('nan'), n=-1., z=float('inf'))['costs']) == ['s']


def test_multiple_sequences_deduplicate_using_the_lowest_cost():
    d = dictionary(entries=[['丁', 'hs', 1], ['丁', 'hz', 3]], ranks={'丁': 0})
    rows = d.fuzzy_candidates([stroke_evidence('h'), recognized('z', z=.001, s=.002)])
    assert len(rows) == 1 and rows[0]['code'] == 'hz' and rows[0]['cost'] == 0


def test_explicit_strokes_preserve_legacy_candidate_order():
    d = StrokeDictionary()
    for code in ('h', 'sp', 'pspzspn'):
        assert [r['char'] for r in d.fuzzy_candidates([stroke_evidence(c) for c in code])] == d.candidates(code)


def test_complete_uncorrected_stroke_code_precedes_frequent_longer_prefixes():
    d = dictionary(entries=[['什','pshs',3],['何','pshszhs',1],['使','pshszhpn',1]],
                   ranks={'什':20000,'何':1,'使':0})
    assert d.candidates('pshs')[0] == '什'
    strokes = [recognized(c, **{c:.001, ('n' if c != 'n' else 'p'):.002}) for c in 'pshs']
    predictor = StrokeContext(dict(maxContext=4,total=100,unigrams={'使':99,'什':1},
                                   contexts={'是':[100,'使',[100]]}))
    rows = d.fuzzy_candidates(strokes, predictor=predictor, context='是')
    assert rows[0]['char'] == '什' and rows[0]['exact_match']
    # Partial input still uses normal frequency/context scoring.
    assert d.candidates('psh')[0] == '使'


def test_rescued_complete_match_does_not_receive_exact_priority_and_variants_deduplicate():
    d = dictionary(entries=[['什','pshs',1],['何','pshszhs',1],['什','pshsh',3]],
                   ranks={'什':100000,'何':0})
    strokes = [stroke_evidence(c) for c in 'psh']+[recognized('n',n=.001,s=.04)]
    rows = d.fuzzy_candidates(strokes)
    assert rows[0]['char'] == '何'
    assert not next(row for row in rows if row['char']=='什')['exact_match']
    assert len([row for row in rows if row['char']=='什']) == 1


def test_real_shi_is_first_after_all_four_strokes_in_strict_and_fuzzy_modes():
    d = StrokeDictionary()
    assert d.candidates('pshs')[0] == '什'
    strokes = [recognized(c, **{c:.001, ('n' if c != 'n' else 'p'):.002}) for c in 'pshs']
    assert d.fuzzy_candidates(strokes)[0]['char'] == '什'


def test_complete_match_priority_is_shared_by_other_short_characters():
    d = StrokeDictionary()
    for character in ('千','干','十','王','大','小','人','八','什'):
        code = next(code for char,code,_ in d.entries if char==character)
        exact = {char for char,full_code,_ in d.entries if full_code==code}
        assert d.candidates(code)[0] in exact
        evidence = [recognized(c, **{c:.001, ('n' if c!='n' else 'p'):.002}) for c in code]
        rows = d.fuzzy_candidates(evidence)
        assert rows[0]['char'] in exact and rows[0]['exact_match']


def test_real_dtw_trace_exposes_all_five_classes_and_can_retrieve_ni():
    d = StrokeDictionary(recognizer='dtw')
    result = d.recognize([(0, 0), (-10, 100)])
    assert result['category'] == 's'
    assert {r['category'] for r in result['category_scores']} == set('hspnz')
    strokes = [stroke_evidence(result['category'], result)] + [stroke_evidence(c) for c in 'spzspn']
    assert not d.candidates('sspzspn') or '你' not in d.candidates('sspzspn')
    assert '你' in [r['char'] for r in d.fuzzy_candidates(strokes)]


def test_dot_can_rescue_diagonal_hook_even_when_another_fold_scores_better():
    result = dict(category='n', shape='捺', category_scores=[
        dict(category='n', shape='捺', distance=.005),
        dict(category='z', shape='竖弯', distance=.048)], shape_scores=[
        dict(category='z', shape='斜钩', distance=.084)])
    stroke = stroke_evidence('n', result)
    assert stroke['rescues'] == ['z'] and not stroke['ambiguous']
    expected = 1.+(.084-.005)/(.04+.005)
    assert stroke['costs']['z'] == pytest.approx(expected)
    assert match_prefix('z', [stroke])['rescues'] == 1
    assert match_prefix('zz', [stroke, stroke]) is None


@pytest.mark.parametrize('shape', ['横钩', '竖弯', '横折'])
def test_new_rescue_does_not_turn_arbitrary_folds_into_dot_alternatives(shape):
    for category in ('n', 'z'):
        result = dict(shape=shape if category == 'z' else '点', category_scores=[
            dict(category=category, shape=shape if category == 'z' else '点', distance=.001),
            dict(category='z' if category == 'n' else 'n', shape=shape, distance=.04)])
        assert len(stroke_evidence(category, result)['costs']) == 1


def test_diagonal_hook_can_rescue_dot_but_a_distant_hook_cannot_rescue_dot_input():
    result = dict(shape='斜钩', category_scores=[dict(category='z', distance=.001),
        dict(category='n', distance=.03)])
    assert stroke_evidence('z', result)['rescues'] == ['n']
    result = dict(shape='点', category_scores=[dict(category='n', distance=.001),
        dict(category='z', shape='竖弯', distance=.04)], shape_scores=[
        dict(category='z', shape='斜钩', distance=.12)])
    assert stroke_evidence('n', result)['rescues'] == []
    for distance in (float('nan'), float('inf'), -1.):
        result['shape_scores'][0]['distance'] = distance
        assert stroke_evidence('n', result)['rescues'] == []


def test_real_weak_hook_recovers_wo_after_later_strokes():
    d = StrokeDictionary(recognizer='dtw')
    result = d.recognize([(0, 0), (25, 30), (50, 65), (75, 100), (73, 98)])
    assert result['category'] == 'n' and any(r['shape'] == '斜钩' for r in result['shape_scores'])
    assert '我' not in d.candidates('phshnpn')
    evidence = [stroke_evidence(c) for c in 'phsh'] + [stroke_evidence('n', result)]
    evidence += [stroke_evidence(c) for c in 'pn']
    rows = d.fuzzy_candidates(evidence)
    wo = next(row for row in rows if row['char'] == '我')
    assert wo['corrections'] == [4] and 'z' in evidence[4]['rescues']
    assert [r['char'] for r in d.fuzzy_candidates([stroke_evidence(c) for c in 'phshnpn'])] == d.candidates('phshnpn')


@pytest.mark.parametrize('simple,shape', [
    ('h','横钩'), ('s','弯钩'), ('s','竖提'), ('p','撇点'), ('p','撇折'),
    ('n','斜钩'), ('n','卧钩')])
def test_short_tail_pairs_rescue_both_directions_with_one_substitution_limit(simple, shape):
    for category, alternative in [(simple,'z'), ('z',simple)]:
        result = dict(shape=shape if category=='z' else '横', category_scores=[
            dict(category=category, shape=shape if category=='z' else '横', distance=.003),
            dict(category=alternative, shape=shape if alternative=='z' else '横', distance=.045)])
        evidence = stroke_evidence(category, result)
        assert alternative in evidence['rescues'] and not evidence['ambiguous']
        assert match_prefix(alternative, [evidence])['rescues'] == 1
        assert match_prefix(alternative*2, [evidence, evidence]) is None
        assert evidence['category'] == category and evidence['costs'][category] == 0


@pytest.mark.parametrize('simple,shape', [('h','横钩'),('s','竖提'),('p','撇折'),('n','卧钩')])
def test_shape_specific_distance_is_used_when_unrelated_fold_is_closer(simple,shape):
    result = dict(shape='横', category_scores=[dict(category=simple,distance=.005),
        dict(category='z',shape='横折',distance=.025)], shape_scores=[
        dict(category='z',shape=shape,distance=.075)])
    evidence = stroke_evidence(simple,result)
    assert evidence['costs']['z'] == pytest.approx(1.+(.075-.005)/(.04+.005))
    result['shape_scores'][0]['distance'] = .1
    assert 'z' not in stroke_evidence(simple,result)['costs']


def test_new_pairs_do_not_admit_arbitrary_fold_or_high_distance_inputs():
    for category in 'hspn':
        result = dict(shape='横', category_scores=[dict(category=category,distance=.001),
            dict(category='z',shape='横折折折',distance=.03)])
        assert 'z' not in stroke_evidence(category,result)['costs']
    for shape in ['横钩','弯钩','竖提','撇折','卧钩']:
        result = dict(shape=shape,category_scores=[dict(category='z',distance=.09),
            dict(category='h',distance=.1),dict(category='s',distance=.1),
            dict(category='p',distance=.1),dict(category='n',distance=.1)])
        assert stroke_evidence('z',result)['costs'] == {'z':0.}


def test_real_small_horizontal_hook_retrieves_fold_candidates_and_preserves_exact_priority():
    d = StrokeDictionary(recognizer='dtw')
    result = d.recognize([(0,0),(40,0),(80,0),(100,0),(99,1)])
    assert result['category'] == 'h'
    evidence = stroke_evidence('h',result)
    assert 'z' in evidence['costs']
    small = dictionary(entries=[['一','h',1],['买','znnzpn',1]],ranks={'一':10,'买':0})
    rows = small.fuzzy_candidates([evidence])
    assert rows[0]['char']=='一' and rows[0]['exact_match']
    assert next(r for r in rows if r['char']=='买')['corrections']==[0]


@pytest.mark.parametrize('points,observed', [
    ([(0,0),(100,0),(99,1)], 'h'),
    ([(0,0),(1,30),(1,80),(0,100),(-2,98)], 's'),
    ([(0,0),(0,100),(2,98)], 's'),
    ([(0,0),(30,20),(70,40),(100,45),(98,43)], 'n'),
    ([(0,0),(25,30),(50,65),(75,100),(73,98)], 'n'),
    ([(0,0),(-100,100),(-98,101)], 'p'),
])
def test_dtw_short_tail_synthetic_traces_keep_fold_explanation(points,observed):
    d=StrokeDictionary(recognizer='dtw')
    result=d.recognize(points)
    assert result['category']==observed
    evidence=stroke_evidence(observed,result)
    assert 'z' in evidence['costs']
    assert evidence['category']==observed
