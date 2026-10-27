from pathlib import Path

import pytest


@pytest.fixture
def audit(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'tools'))
    import analyze_pie_dian_ambiguity
    return analyze_pie_dian_ambiguity


def test_expansion_only_applies_to_explicit_shape_and_caps_splits(audit):
    assert audit.interpretations('zM') == ['zpn', 'zz']
    assert audit.interpretations('MM') == ['pnpn', 'pnz', 'zpn', 'zz']
    assert audit.interpretations('MM', max_splits=1) == ['pnz', 'zpn', 'zz']
    assert audit.interpretations('MM', max_splits=0) == ['zz']


def test_characters_deduplicate_across_prefixes_and_exact_is_separate(audit):
    entries = [('甲', 'z', 1), ('甲', 'pn', 2), ('乙', 'zh', 1), ('丙', 'pnz', 1), ('丁', 'pn', 1)]
    index = audit.PrefixIndex(entries, {})
    row = audit.compare(index, 'M')
    assert row['primaryCount'] == 2
    assert row['unionCount'] == 4
    assert row['addedCount'] == 2
    assert row['primaryExactCount'] == 1 and row['unionExactCount'] == 2
    assert row['addedExact'] == ['丁']
    allowed = audit.PrefixIndex(entries, {}, {'甲', '乙'})
    assert audit.compare(allowed, 'M')['unionCount'] == 2


def test_index_matches_actual_runtime_dictionary(audit):
    from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary
    from proximic_ring.touchpad.stroke.stroke_common import common_characters
    dictionary = StrokeDictionary()
    common = common_characters()
    for allowed in (None, common):
        index = audit.PrefixIndex(dictionary.entries, dictionary.ranks, allowed)
        for prefix in ('z', 'pn', 'zph', 'pnph', 'nzspn', 'nzsz', 'nzszzpszn'):
            assert index.candidates(prefix) == frozenset(dictionary.candidates(prefix, 200000, allowed_characters=allowed))
            expected = {c for c, code, _ in dictionary.entries if code == prefix and (allowed is None or c in allowed)}
            assert index.exact(prefix) == expected
