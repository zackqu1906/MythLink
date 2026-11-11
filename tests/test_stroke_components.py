"""Component encodings are ordinary paths, with reference-index evidence."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from proximic_ring.touchpad.stroke.stroke_components import RULES, component_annotations
from proximic_ring.touchpad.stroke.stroke_component_decoder import ComponentDecoder, ComponentGraph
from proximic_ring.touchpad.stroke.stroke_fuzzy import stroke_evidence
from proximic_ring.touchpad.stroke.stroke_glyphs import candidate_glyph, glyph_library
from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary
from proximic_ring.touchpad.stroke.stroke_vectors import StrokeVectorModel


@pytest.fixture(scope='module')
def dictionary():
    return StrokeDictionary()


def evidence(code):
    return [stroke_evidence(c) for c in code]


def annotation(component, start=0, prefix='', suffix=''):
    code = prefix+RULES[component][0]+suffix
    indices = list(range(start, start+len(RULES[component][0])))
    return dict(code=code, components=[dict(component=component, indices=indices, scope=indices)])


@pytest.mark.parametrize('component', list(RULES))
def test_all_components_have_verified_occurrences_and_accepted_role_orders(component):
    rows = component_annotations()
    assert any(any(c['component'] == component for c in row['components']) for row in rows.values())
    decoder = ComponentDecoder({'甲': annotation(component)})
    canonical, variants = RULES[component]
    for permutation in variants:
        code = ''.join(canonical[i] for i in permutation)
        matches = decoder.match(evidence(code))
        row = next(r for r in matches if r['observed_to_expected'] == permutation)
        assert row['encoding_valid'] and row['exact_match']
        assert row['corrections'] == []


@pytest.mark.parametrize('char,code,mapping', [
    ('快', 'snnzhpn', (2, 0, 1, 3, 4, 5, 6)),
    ('放', 'nhpzphpn', (0, 1, 3, 2, 4, 5, 6, 7)),
    ('加', 'pzszh', (1, 0, 2, 3, 4)),
    ('及', 'zpn', (1, 0, 2)),
    ('回', 'szhszh', (0, 1, 5, 2, 3, 4)),
    ('区', 'hzpn', (0, 3, 1, 2)),
    ('过', 'nznhsn', (3, 4, 5, 0, 1, 2)),
    ('建', 'znzhhhhs', (6, 7, 0, 1, 2, 3, 4, 5)),
])
def test_builtin_paths_include_component_scheduling_without_repairs(dictionary, char, code, mapping):
    rows = dictionary.encoding_candidates(evidence(code), allowed_characters={char}, fuzzy=False)
    row = next(r for r in rows if r['char'] == char)
    assert row['match_type'] == 'component'
    assert row['observed_to_expected'] == mapping
    assert row['is_complete'] and row['encoding_valid'] and row['corrections'] == []
    assert 'edits' not in row
    visual = candidate_glyph(char, row['code'], len(code), stroke_indices=mapping)
    assert visual['available'] and set(visual['states']) == {'entered'}


def test_generalizes_without_legacy_variant_and_competes_for_first_place():
    # Custom dictionary contains only the reference spelling for the component.
    # The other character is a literal complete match of the observed spelling.
    annotations = {'甲': annotation('忄', suffix='h')}
    d = StrokeDictionary(dict(entries=[['甲', 'nnsh', 1], ['乙', 'nsnh', 1]],
                              ranks={'甲': 1, '乙': 5000}, templates=[]), components=annotations)
    rows = d.encoding_candidates(evidence('nsnh'), limit=1, fuzzy=False)
    assert rows[0]['char'] == '甲'
    assert rows[0]['corrections'] == []
    assert 'nsnh' not in {entry[1] for entry in d.entries if entry[0] == '甲'}
    d.components_enabled = False
    assert d.candidates('nsnh') == ['乙']


def test_context_ranks_component_and_literal_paths_together():
    annotations = {'甲': annotation('忄', suffix='h')}
    d = StrokeDictionary(dict(entries=[['甲', 'nnsh', 1], ['乙', 'nsnh', 1]],
                              ranks={'甲': 10, '乙': 10}, templates=[]), components=annotations)
    predictor = SimpleNamespace(stroke_bonuses=lambda context, chars: {'甲': 1. if context == '前文' else 0.})
    assert d.encoding_candidates(evidence('nsnh'))[0]['char'] == '乙'
    assert d.encoding_candidates(evidence('nsnh'), predictor=predictor, context='前文')[0]['char'] == '甲'


def test_same_code_components_keep_identities_and_arbitrary_substrings_do_not_activate():
    annotations = {'甲': annotation('刀'), '乙': annotation('力')}
    d = StrokeDictionary(dict(entries=[['甲', 'zp', 1], ['乙', 'zp', 1], ['丙', 'hzp', 1]],
                              ranks={}, templates=[]), components=annotations)
    rows = d.encoding_candidates(evidence('pz'), fuzzy=False)
    assert {r['component_path'][0]['component'] for r in rows if r.get('component_path')} == {'刀', '力'}
    assert '丙' not in [r['char'] for r in d.encoding_candidates(evidence('hpz'), fuzzy=False)]


def test_multiple_components_compose_without_expanding_whole_dictionary_codes():
    decoder = ComponentDecoder({'甲': dict(code='nnszp', components=[
        dict(component='忄', indices=[0, 1, 2], scope=[0, 1, 2]),
        dict(component='刀', indices=[3, 4], scope=[3, 4])])})
    row = next(r for r in decoder.match(evidence('snnpz')) if r['is_complete'])
    assert row['observed_to_expected'] == (2, 0, 1, 4, 3)
    assert len(row['component_path']) == 2
    assert row['component_cost'] == pytest.approx(.24)
    assert decoder.snapshot()['nodes'] < 30


def test_nested_enclosure_keeps_inner_component_and_partial_mapping():
    annotation = dict(code='szzph', components=[
        dict(component='囗', indices=[0, 1, 4], scope=[0, 1, 2, 3, 4]),
        dict(component='刀', indices=[2, 3], scope=[2, 3])])
    decoder = ComponentDecoder({'甲': annotation})
    row = next(r for r in decoder.match(evidence('szhpz')) if r['is_complete'])
    assert row['observed_to_expected'] == (0, 1, 4, 3, 2)
    partial = decoder.match(evidence('szh'))[0]
    assert not partial['is_complete']
    assert partial['observed_to_expected'][:3] == (0, 1, 4)
    assert len(decoder.match(evidence('sz'))) >= 2  # Both close-now and close-later remain alive.


def test_partial_enclosure_glyph_colors_reference_indices_and_never_red(dictionary):
    row = dictionary.encoding_candidates(evidence('szh'), fuzzy=False, allowed_characters={'回'})[0]
    visual = candidate_glyph('回', row['code'], 3, stroke_indices=row['observed_to_expected'])
    assert visual['available']
    assert [i for i, state in enumerate(visual['states']) if state == 'entered'] == [0, 1, 5]
    assert 'correction' not in visual['states']
    assert dictionary.encoding_candidates(evidence('sz'), fuzzy=False, allowed_characters={'回'})
    assert dictionary.encoding_candidates(evidence('szh'), fuzzy=False, allowed_characters={'回'})[0] == row


def test_rejects_invalid_annotations_and_glyph_mapping():
    with pytest.raises(ValueError):
        ComponentGraph(dict(code='nnsh', components=[dict(component='忄', indices=[0, 1, 3], scope=[0, 1, 2])]))
    with pytest.raises(ValueError):
        ComponentGraph(dict(code='nns', components=[dict(component='忄', indices=[0, 1, 2], scope=[0, 1, 2])]*2))
    assert not candidate_glyph('回', 'szhszh', 3, stroke_indices=(0, 1, 1, 2, 3, 4))['available']


def test_component_path_can_include_real_recognition_alternative_separately():
    d = StrokeDictionary(dict(entries=[['甲', 'nnsh', 1]], ranks={}, templates=[]),
                         components={'甲': annotation('忄', suffix='h')})
    ink = evidence('nsns')
    ink[-1]['costs']['h'] = .2
    row = d.encoding_candidates(ink)[0]
    assert row['corrections'] == [3] and not row['encoding_valid']
    assert row['order_variant'] and row['match_type'] == 'component'
    assert d.encoding_candidates(ink, fuzzy=False) == []


def test_fold_evidence_follows_reordered_reference_role(dictionary):
    char = '加'
    reference = dictionary.component_decoder.graphs[char].code
    normal = evidence(reference)
    fold = next(i for i, c in enumerate(reference) if c == 'z')
    normal[fold] = stroke_evidence('z', dict(category='z', shape='横折钩', distance=.001,
        category_scores=[dict(category='z', distance=.001)], shape_scores=[
            dict(category='z', shape='横折钩', distance=.001),
            dict(category='z', shape='横斜钩', distance=.14)]))
    baseline = dictionary.encoding_candidates(normal, allowed_characters={char})[0]
    swapped = [normal[1], normal[0], *normal[2:]]
    row = dictionary.encoding_candidates(swapped, allowed_characters={char})[0]
    assert row['fold_score'] == pytest.approx(baseline['fold_score'])
    assert row['fold_matches'][0]['reference_index'] == 0
    assert row['fold_matches'][0]['index'] == 1
    assert row['key'][0] == baseline['key'][0]


def test_vectors_follow_mapping_and_missing_template_is_neutral():
    annotations = {'甲': annotation('刀'), '乙': annotation('力')}
    d = StrokeDictionary(dict(entries=[['甲', 'zp', 1], ['乙', 'zp', 1]],
                              ranks={'甲': 1, '乙': 1}, templates=[]), components=annotations)
    d.stroke_vectors = StrokeVectorModel({'甲': dict(code='zp', vectors=[[1., 1.], [-3., 2.]]),
                                         '乙': dict(code='zp', vectors=[[1., 1.], [-1., 3.]])}, scope='complete')
    normal = evidence('zp')
    for ink, vector in zip(normal, [[1., 1.], [-3., 2.]]):
        ink.update(vector=vector, manual=False)
    swapped = normal[::-1]
    before = deepcopy(swapped)
    row = d.encoding_candidates(swapped)[0]
    assert row['char'] == '甲' and row['vector_cost'] == pytest.approx(0.)
    assert row['observed_to_expected'] == (1, 0)
    assert swapped == before
    d.stroke_vectors = StrokeVectorModel(d.stroke_vectors.characters, scope='prefix')
    row = d.encoding_candidates(swapped)[0]
    assert row['char'] == '甲' and row['vector_term'] == pytest.approx(0.)
    assert swapped == before


def test_new_initial_and_allowed_scope_do_not_escape_restricted_views(dictionary):
    from copy import copy
    from collections import defaultdict
    view = copy(dictionary)
    view.by_initial = defaultdict(list, {'h': dictionary.by_initial['h']})
    assert not view.encoding_candidates(evidence('snnzhpn'), allowed_characters={'快'})
    assert dictionary.encoding_candidates(evidence('snnzhpn'), allowed_characters={'快'})
    assert not dictionary.encoding_candidates(evidence('snnzhpn'), allowed_characters=set())


def test_trial_visuals_use_component_mapping_in_strict_mode(dictionary, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'tools'))
    from stroke_trial_candidates import trial_candidate_visuals
    ink = evidence('szh')
    row = dictionary.encoding_candidates(ink, fuzzy=False, allowed_characters={'回'})[0]
    window = SimpleNamespace(matches=[row], candidates=['回'], selected=0, evidence=ink, code='szh',
                             dictionary=dictionary, fuzzy_toggle=SimpleNamespace(isChecked=lambda: False))
    visual = trial_candidate_visuals(window)[0]
    assert visual['available'] and visual['states'][5] == 'entered'
    assert '纠错' not in visual['status'] and '组件匹配' in visual['status']


def test_limits_and_no_component_mode_preserve_literal_access(dictionary):
    assert dictionary.encoding_candidates(evidence('n'), limit=0) == []
    assert dictionary.encoding_candidates([]) == []
    assert dictionary.candidates('invalid') == []
    for char in ('快', '回', '过', '炎'):
        code = glyph_library()[char]['code']
        row = dictionary.encoding_candidates(evidence(code), allowed_characters={char})[0]
        assert row['observed_to_expected'] == tuple(range(len(code)))


def test_search_is_bounded_and_reports_pruned_paths():
    from proximic_ring.touchpad.stroke.stroke_component_decoder import MAX_PATHS
    components = [dict(component='忄', indices=list(range(i, i+3)), scope=list(range(i, i+3)))
                  for i in range(0, 18, 3)]
    decoder = ComponentDecoder({'甲': dict(code='nns'*6, components=components)})
    ink = evidence('n'*18)
    for stroke in ink:
        stroke['costs']['s'] = .1
    rows = decoder.match(ink)
    assert len(rows) <= MAX_PATHS and any(r['search_truncated'] for r in rows)
    assert decoder.snapshot()['nodes'] < 120
