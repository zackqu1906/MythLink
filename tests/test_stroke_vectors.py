"""Same-code reranking by per-stroke start→end vectors."""
import pytest

from proximic_ring.touchpad.stroke.stroke_fuzzy import retune_evidence, stroke_evidence, FuzzyPolicy
from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary
from proximic_ring.touchpad.stroke.stroke_vectors import StrokeVectorModel, stroke_vector, vector_cost


def line(dx, dy, n=20):
    return [(dx*i/n, dy*i/n) for i in range(n+1)]


@pytest.fixture(scope='module')
def dictionary():
    return StrokeDictionary(recognizer='dtw')


def written(dictionary, *strokes):
    evidence = []
    for points in strokes:
        stroke = stroke_evidence(dictionary.recognize(points)['category'], dictionary.recognize(points))
        stroke['vector'] = stroke_vector(points)
        evidence.append(stroke)
    return evidence


def test_vector_and_cost_are_scale_free():
    assert stroke_vector(line(3, -4)) == [3., -4.]
    assert stroke_vector([(1, 1), (1, 1)]) is None and stroke_vector([]) is None
    assert vector_cost([[1, 0], [0, 2]], [[10, 0], [0, 20]]) == pytest.approx(0)
    assert vector_cost([[1, 0], [2, 0]], [[2, 0], [1, 0]]) > .2
    assert vector_cost([[1, 0]], [[1, 0], [1, 0]]) is None


def test_cost_requires_aligned_order_and_automatic_strokes():
    model = StrokeVectorModel({'甲': dict(code='hs', vectors=[[1, 0], [0, 1]])})
    evidence = [dict(vector=[1, 0], manual=False), dict(vector=[0, 1], manual=False)]
    assert model.cost('甲', 'hs', evidence) == pytest.approx(0)
    assert model.cost('甲', 'hh', evidence) is None
    assert model.cost('乙', 'hs', evidence) is None
    assert model.cost('甲', 'hs', [evidence[0], dict(evidence[1], manual=True)]) is None
    assert model.cost('甲', 'hs', [evidence[0], dict(manual=False)]) is None


def test_rerank_moves_only_exact_complete_rows():
    model = StrokeVectorModel({'甲': dict(code='hh', vectors=[[1, 0], [3, 0]]),
                               '乙': dict(code='hh', vectors=[[3, 0], [1, 0]])}, scope='complete')
    evidence = [dict(vector=[3, 0]), dict(vector=[1, 0])]
    rows = [dict(char='甲', code='hh', exact_match=True, score=0.),
            dict(char='丙', code='hhs', exact_match=False, score=-1.),
            dict(char='乙', code='hh', exact_match=True, score=.1)]
    ranked = model.rerank(rows, evidence, {})
    assert [r['char'] for r in ranked] == ['乙', '丙', '甲']
    assert ranked[1] is rows[1] and ranked[0]['vector_cost'] == pytest.approx(0)
    assert model.rerank(rows, evidence[:1], {}) is rows
    assert [r['char'] for r in StrokeVectorModel(model.characters, weight=0, scope='complete').rerank(rows, evidence, {})] == ['甲', '丙', '乙']


def test_rerank_keeps_fold_demoted_rows_in_their_tier():
    model = StrokeVectorModel({c: dict(code='hh', vectors=v) for c, v in
                               (('甲', [[1, 0], [3, 0]]), ('乙', [[3, 0], [1, 0]]), ('丙', [[3, 0], [1, 0]]))},
                              scope='complete')
    evidence = [dict(vector=[3, 0]), dict(vector=[1, 0])]
    rows = [dict(char='甲', code='hh', exact_match=True, key=(0,), score=0.),
            dict(char='乙', code='hh', exact_match=True, key=(0,), score=.1),
            dict(char='丙', code='hh', exact_match=True, key=(2,), score=-5.)]
    assert [r['char'] for r in model.rerank(rows, evidence, {})] == ['乙', '甲', '丙']


def test_fuzzy_corrected_candidates_keep_their_positions(dictionary):
    # 撇＋捺 with a near 横 alternative on the second stroke: fuzzy rows get corrections.
    evidence = written(dictionary, line(-40, 60), line(60, 50))
    assert ''.join(s['category'] for s in evidence) == 'pn'
    evidence[1]['costs']['h'], evidence[1]['ambiguous'] = .3, True
    before = dictionary.fuzzy_candidates(evidence)
    assert any(r['corrections'] for r in before) and sum(r['exact_match'] for r in before) >= 2
    dictionary.stroke_vectors = StrokeVectorModel(scope='complete')
    try:
        after = dictionary.fuzzy_candidates(evidence)
    finally:
        dictionary.stroke_vectors = None
    assert len(before) == len(after)
    for old, new in zip(before, after):
        if not old.get('exact_match'):
            assert new['char'] == old['char']
    assert sorted(r['char'] for r in before) == sorted(r['char'] for r in after)


@pytest.mark.parametrize('scope', ['complete', 'prefix'])
def test_dictionary_reorders_tu_shi_by_horizontal_lengths(dictionary, scope):
    short_long = written(dictionary, line(40, 0), line(0, 60), line(100, 0))
    long_short = written(dictionary, line(100, 0), line(0, 60), line(40, 0))
    assert ''.join(s['category'] for s in short_long) == 'hsh'
    def top(evidence):
        return [r['char'] for r in dictionary.fuzzy_candidates(evidence) if r['char'] in '土士工']
    baseline = top(short_long)
    assert baseline == top(long_short)  # five-class code alone cannot tell them apart
    dictionary.stroke_vectors = StrokeVectorModel(scope=scope)
    try:
        assert top(short_long)[0] == '土'
        assert top(long_short)[0] == '士'
    finally:
        dictionary.stroke_vectors = None
    assert top(short_long) == baseline


def test_retune_keeps_vectors(dictionary):
    evidence = written(dictionary, line(40, 0), line(0, 60))
    assert [s['vector'] for s in retune_evidence(evidence, FuzzyPolicy(80))] == [s['vector'] for s in evidence]


def test_prefix_cost_aligns_written_strokes_and_skips_manual_ones():
    model = StrokeVectorModel({'甲': dict(code='hsh', vectors=[[1, 0], [0, 1], [3, 0]])})
    ink = [dict(vector=[1, 0]), dict(vector=[0, 1])]
    assert model.prefix_cost('甲', 'hsh', ink) == (pytest.approx(0), 2)
    assert model.prefix_cost('甲', 'hsh', ink[::-1], (1, 0)) == (pytest.approx(0), 2)
    assert model.prefix_cost('甲', 'hss', ink) is None
    assert model.prefix_cost('甲', 'hsh', [ink[0], dict(ink[1], manual=True)]) == (pytest.approx(0), 1)
    assert model.prefix_cost('甲', 'hsh', [dict(manual=True)]) is None


def test_score_rows_reorders_within_tiers_and_is_neutral_without_glyph():
    model = StrokeVectorModel({'甲': dict(code='hsh', vectors=[[1, 0], [0, 1], [1, 0]]),
                               '乙': dict(code='hsp', vectors=[[3, 0], [0, 1], [-1, 1]]),
                               '丁': dict(code='hs', vectors=[[1, 0], [0, 1]])}, prefix_weight=1.)
    ink = [dict(vector=[3, 0]), dict(vector=[0, 1])]
    rows = [dict(char='丁', code='hs', key=(0,), final_score=5.),
            dict(char='甲', code='hsh', key=(3,), final_score=0.),
            dict(char='乙', code='hsp', key=(3,), final_score=.1),
            dict(char='丙', code='hsz', key=(3,), final_score=.2)]
    model.score_rows(rows, ink)
    by = {r['char']: r for r in rows}
    assert by['乙']['vector_term'] == pytest.approx(0) and by['甲']['vector_term'] > .1
    assert by['乙']['vector_score'] < by['甲']['vector_score']
    # 丙 has no glyph: it gets the tier mean, so it is neither first nor last by fiat.
    assert by['丙']['vector_term'] is None
    assert by['丙']['vector_score'] == pytest.approx(.2 + (by['甲']['vector_term'] + 0.)/2)
    # A lone row in its tier has nothing to be compared against, so it keeps its score.
    assert by['丁']['vector_score'] == pytest.approx(5. + by['丁']['vector_term'])


def test_prefix_scope_lifts_an_unfinished_character_whose_strokes_fit():
    data = dict(entries=[['甲', 'hsh', 1], ['乙', 'hsp', 1]], ranks={'甲': 1, '乙': 60}, templates=[])
    d = StrokeDictionary(data)
    ink = [dict(stroke_evidence(c), manual=False, vector=v) for c, v in (('h', [3, 0]), ('s', [0, 1]))]
    assert [r['char'] for r in d.fuzzy_candidates(ink)] == ['甲', '乙']
    d.stroke_vectors = StrokeVectorModel({'甲': dict(code='hsh', vectors=[[1, 0], [0, 3], [1, 0]]),
                                         '乙': dict(code='hsp', vectors=[[3, 0], [0, 1], [-1, 1]])},
                                         prefix_weight=2.)
    rows = d.fuzzy_candidates(ink)
    assert [r['char'] for r in rows] == ['乙', '甲']
    assert all(r['key'][0] == 3 for r in rows)
    assert StrokeVectorModel({}).snapshot()['scope'] == 'prefix'
    with pytest.raises(ValueError):
        StrokeVectorModel({}, scope='nope')
