import gzip
import json
from importlib.resources import files
from pathlib import Path
from types import SimpleNamespace

import pytest

from proximic_ring.touchpad.stroke.stroke_common import common_characters
from proximic_ring.touchpad.stroke.stroke_dictionary_orders import (
    filter_order_variants, grass_components, is_order_variant, reference_orders,
)
from proximic_ring.touchpad.stroke.stroke_fuzzy import stroke_evidence
from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary
from proximic_ring.touchpad.stroke.stroke_word_correction import WholeCharacterCorrector, merge_candidates


@pytest.mark.parametrize('character,removed,reference', [
    ('舌', 'hhsszh', 'phsszh'), ('丁', 'hz', 'hs'),
    ('没', 'nnnpzzn', 'nnhpzzn'), ('今', 'pnhz', 'pnnz'),
    ('有', 'hppzhh', 'hpszhh'), ('儒', 'pshnzshhhhhpszss', 'pshnzsnnnnhpszss'),
    ('近', 'pphsnzzn', 'pphsnzn'), ('近', 'pphsnnzn', 'pphsnzn'),
    ('阳', 'zzsszhh', 'zsszhh'), ('充', 'hznpz', 'nhznpz'),
])
def test_builtin_dictionary_moves_shape_and_count_variants_out_of_literal_matches(character, removed, reference):
    dictionary = StrokeDictionary()
    codes = {row[1] for row in dictionary.by_character[character]}
    assert reference in codes
    assert removed not in codes


@pytest.mark.parametrize('character,codes', [
    ('快', {'nnszhpn', 'nsnzhpn', 'snnzhpn'}),
    ('门', {'nsz', 'snz'}), ('九', {'pz', 'zp'}),
    ('方', {'nhzp', 'nhpz'}), ('万', {'hzp', 'hpz'}),
    ('必', {'nznpn', 'nznnp'}), ('我', {'phshzpn', 'phshznp'}),
    ('草', {'hssszhhhs', 'hshsszhhhs', 'shhsszhhhs'}),
    ('花', {'hsspspz', 'hsspszp', 'hshspspz', 'shhspspz'}),
])
def test_builtin_dictionary_keeps_whole_and_component_orders(character, codes):
    assert codes <= {row[1] for row in StrokeDictionary().by_character[character]}


def test_component_exception_does_not_keep_other_shape_changes_or_guess_grass_from_prefix():
    dictionary = StrokeDictionary()
    assert not {'hsspshz', 'hshspshz', 'shhspshz'} & {
        row[1] for row in dictionary.by_character['花']}
    assert 0 in grass_components()['花']
    assert '世' not in grass_components()
    assert not is_order_variant('hssh', 'hshsh')
    assert is_order_variant('hssh', 'hshsh', (0,))
    assert is_order_variant('hsshss', 'hshshshs', (0, 3))


def test_filter_preserves_character_coverage_and_only_removes_reviewed_variants():
    raw = json.loads(gzip.decompress(files('proximic_ring').joinpath('assets/strokes.json.gz').read_bytes()))
    dictionary = StrokeDictionary()
    assert len(dictionary.entries) < len(raw['entries'])
    assert set(dictionary.by_character) == {row[0] for row in raw['entries']}
    assert common_characters() <= set(dictionary.by_character)
    assert dictionary._left_dots.keys() <= {(row[0], row[1]) for row in dictionary.entries}
    references = reference_orders()
    for character, rows in dictionary.by_character.items():
        reference = references.get(character)
        if reference and any(row[1] == reference for row in rows):
            assert all(is_order_variant(reference, row[1], grass_components().get(character, ()))
                       for row in rows)
    custom = [['甲', 'h', 1], ['甲', 'n', 2]]
    assert StrokeDictionary(dict(entries=custom, ranks={}, templates=[])).entries == custom
    assert filter_order_variants([['😀', 'h', 1], ['😀', 'n', 2]]) == [
        ['😀', 'h', 1], ['😀', 'n', 2]]


def test_she_family_stops_claiming_literal_prefix_and_recovers_with_red_first_stroke(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'tools'))
    from stroke_trial_candidates import trial_candidate_visuals
    dictionary = StrokeDictionary()
    allowed = common_characters()
    assert not set('舌乱甜适敌') & set(dictionary.candidates('hhss', allowed_characters=allowed))
    corrector = WholeCharacterCorrector(dictionary.entries, dictionary.ranks, allowed_characters=allowed)
    for character, observed in [('舌', 'hhsszh'), ('乱', 'hhsszhz'),
                                ('甜', 'hhsszhhsshh'), ('适', 'hhsszhnzn'), ('敌', 'hhsszhphpn')]:
        evidence = [stroke_evidence(letter) for letter in observed]
        assert character not in [row['char'] for row in dictionary.fuzzy_candidates(evidence, allowed_characters=allowed)]
        matches = corrector.candidates(observed, evidence=evidence)
        match = next(row for row in matches if row['char'] == character)
        assert match['correction_summary'] == '输入第1笔一→丿'
        window = SimpleNamespace(candidates=[character], matches=[match], selected=0,
                                 dictionary=dictionary, code=observed, evidence=evidence)
        visual = trial_candidate_visuals(window)[0]
        assert visual['available']
        assert visual['states'][0] == 'correction'
        assert visual['states'][1:] == ['entered'] * (visual['total']-1)
        assert '笔顺版本不同' not in visual['status']
        normal = dictionary.candidates(observed, allowed_characters=allowed)
        assert merge_candidates(normal, matches)[0] == character
