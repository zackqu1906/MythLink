"""Position-specific glyph alternatives, source alignment, and real dictionary coverage."""
import importlib.util
from importlib.resources import files
import gzip
import json
from pathlib import Path

import pytest

from proximic_ring.touchpad.stroke.stroke_common import common_characters
from proximic_ring.touchpad.stroke.stroke_fuzzy import match_prefix, stroke_evidence
from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary
from proximic_ring.touchpad.stroke.stroke_left_dots import left_dot_annotations, validate_annotations


def automatic(category):
    # A confident observed p/s with no right-dot interpretation.
    return stroke_evidence(category, {'category': category, 'distance': .001,
                                    'category_scores': [{'category': category, 'distance': .001}]})


def evidence(code):
    return [automatic(category) for category in code]


def dictionary(entries, dots, ranks=None):
    return StrokeDictionary(dict(templates=[], entries=entries, ranks=ranks or {}), left_dots=dots)


def annotation(character, code, *positions):
    return dict(character=character, code=code, positions=list(positions))


@pytest.mark.parametrize('observed', ['p', 's'])
def test_confident_first_left_dot_reaches_n_initial_without_changing_observation(observed):
    d = dictionary([['心', 'nznn', 1]], [annotation('心', 'nznn', 0)])
    strokes = evidence(observed+'znn')
    assert strokes[0]['costs'] == {observed: 0.}
    row = d.fuzzy_candidates(strokes)[0]
    assert row['char'] == '心' and row['code'] == 'nznn'
    assert row['left_dot_corrections'] == row['corrections'] == [0]
    assert row['rescues'] == 0 and row['cost'] == pytest.approx(.45)
    assert strokes[0]['category'] == observed
    assert '心' in [item['char'] for item in d.fuzzy_candidates(strokes[:1])]
    assert not d.candidates(observed+'znn')


def test_cache_cannot_share_annotation_with_other_character_having_same_code():
    d = dictionary([['宝', 'nnz', 1], ['它', 'nnz', 1]], [annotation('宝', 'nnz', 1)])
    assert [row['char'] for row in d.fuzzy_candidates(evidence('npz'))] == ['宝']
    assert not d.fuzzy_candidates(evidence('pnz'))  # unannotated central point
    assert not d.fuzzy_candidates(evidence('nhz'))  # no arbitrary h/z wildcard
    assert not d.fuzzy_candidates(evidence('nzz'))
    assert not d.fuzzy_candidates(evidence('npz'), allowed_characters={'它'})


def test_multiple_left_dots_use_independent_budget_while_generic_rescue_is_bounded():
    strokes = evidence('ps') + [stroke_evidence('h', dict(shape='横', category_scores=[
        dict(category='h', distance=.001), dict(category='z', shape='横钩', distance=.04)]))]
    row = match_prefix('nnz', strokes, left_dots=(0, 1))
    assert row['left_dot_corrections'] == [0, 1] and row['rescues'] == 1
    assert match_prefix('nnzz', strokes + [strokes[-1]], left_dots=(0, 1)) is None


def test_manual_strokes_and_strict_mode_remain_locked_including_mixed_input():
    d = dictionary([['宝', 'nnzhhshn', 1]], [annotation('宝', 'nnzhhshn', 1)])
    code = 'npzhhshn'
    strokes = evidence(code)
    strokes[1] = stroke_evidence('p')
    assert not d.fuzzy_candidates(strokes)
    assert not d.fuzzy_candidates([stroke_evidence(c) for c in code])
    assert not d.candidates(code)
    assert d.candidates('nnzhhshn') == ['宝']


def test_complete_left_dot_variant_precedes_longer_prefix_but_exact_input_stays_first():
    d = dictionary([['心', 'nznn', 1], ['必', 'nznnp', 1], ['甲', 'pznn', 1]],
                   [annotation('心', 'nznn', 0), annotation('必', 'nznnp', 0)],
                   ranks={'心': 100000, '必': 0, '甲': 500000})
    assert [row['char'] for row in d.fuzzy_candidates(evidence('pznn'))] == ['甲', '心', '必']
    assert [row['char'] for row in d.fuzzy_candidates(evidence('pzn'))] == ['必', '心', '甲']


def test_injected_dictionary_does_not_inherit_builtin_masks():
    d = StrokeDictionary(dict(templates=[], entries=[['心','nznn',1]], ranks={}))
    assert not d.fuzzy_candidates(evidence('pznn'))


@pytest.mark.parametrize('character,code,observed', [
    ('宝','nnzhhshn','npzhhshn'), ('实','nnznnhpn','nsznnhpn'),
    ('控','hshnnzpnhsh','hshnpzpnhsh'), ('窗','nnzpnpszpznh','nszpnpszpznh'),
    ('快','nnszhpn','pnszhpn'), ('快','nsnzhpn','psnzhpn'), ('快','snnzhpn','spnzhpn'),
    ('心','nznn','sznn'), ('学','nnpnzzsh','nnpszzsh'),
])
def test_real_character_variants_retrieve_after_full_input(character, code, observed):
    d = StrokeDictionary()
    assert (character, code) in d._left_dots
    rows = d.fuzzy_candidates(evidence(observed), allowed_characters=common_characters())
    row = next(item for item in rows if item['char'] == character)
    assert row['left_dot_corrections'] and row['rescues'] == 0
    assert character not in d.candidates(observed, allowed_characters=common_characters())
    assert character not in [item['char'] for item in d.fuzzy_candidates(
        [stroke_evidence(c) for c in observed], allowed_characters=common_characters())]


def test_all_shipped_variants_reference_existing_codes_and_preserve_standard_query():
    d = StrokeDictionary()
    entries = {(character, code) for character, code, _ in d.entries}
    masks = left_dot_annotations()
    raw = json.loads(gzip.decompress(files('proximic_ring').joinpath('assets/strokes.json.gz').read_bytes()))
    source_entries = {(character, code) for character, code, _ in raw['entries']}
    assert masks and masks.keys() <= source_entries
    assert d._left_dots.keys() == masks.keys() & entries
    assert {c for c, _ in masks} <= common_characters()
    for (character, code), positions in masks.items():
        assert positions and all(code[index] == 'n' for index in positions)
    data = json.loads(files('proximic_ring').joinpath('assets/stroke-left-dots.json').read_text(encoding='utf-8'))
    assert data['statistics']['component_positions'] > 0
    assert all(files('proximic_ring').joinpath('assets/'+license).is_file() for license in data['licenses'])
    assert not any(character == '火' for character, _ in masks)
    assert not any(character == '膝' for character, _ in masks)


@pytest.mark.parametrize('positions', [[-1], [2], [True], [0, 0], []])
def test_invalid_annotations_fail_closed(positions):
    with pytest.raises(ValueError):
        validate_annotations([dict(character='心', code='nz', positions=positions)])


def builder():
    spec = importlib.util.spec_from_file_location('left_dot_builder',
        Path(__file__).resolve().parents[1] / 'tools/build_left_dot_annotations.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_nested_component_mapping_skips_incomplete_paths_and_handles_cycles():
    module = builder()
    data = {
        '控': dict(decomposition='⿰扌空', matches=[[0]]*3+[[1]]*8),
        '空': dict(decomposition='⿱穴工', matches=[[0]]*5+[[1]]*3),
        '穴': dict(decomposition='⿱宀八', matches=[[0]]*3+[[1]]*2),
        '圈': dict(decomposition='圈', matches=[[]]),
    }
    found = module.component_seeds('控', 11, data)
    assert found == [('宀', (3, 4, 5), (1,), '控/空/穴/宀')]
    data['穴']['matches'] = [[0]]*2+[[1]]*3
    assert module.component_seeds('控', 11, data) == []
    assert module.component_seeds('圈', 1, data) == []
    assert module.parse_ids('⿳艹冖心') == [('艹',(0,)), ('冖',(1,)), ('心',(2,))]
    assert module.parse_ids('⿰忄') == []


def test_variant_alignment_rejects_ambiguous_dots_and_tracks_inserted_strokes():
    module = builder()
    assert module.aligned_positions('nnz', 'nz', [0, 1]) == {}
    assert module.aligned_positions('hnz', 'hhnz', [1]) == {1: 2}
    assert module.aligned_positions('nnz', 'nsz', [1]) == {}
    assert module.aligned_positions('nznn', 'nznn', [0]) == {0: 0}
    assert module.aligned_positions('nzhhh', 'ssssnzhhh', [0]) == {}
