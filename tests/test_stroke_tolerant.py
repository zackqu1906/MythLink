"""Filling a short first page with tolerant guesses."""
import pytest

from proximic_ring.touchpad.stroke.stroke_fuzzy import stroke_evidence
from proximic_ring.touchpad.stroke.stroke_tolerant import (
    DEFAULT_TOLERANT_POLICY, TolerantIndex, TolerantPolicy, fill_page, substitution_costs, tolerant_candidates,
)

ENTRIES = [['甲', 'hszhh', 1], ['乙', 'hspnz', 1], ['丙', 'pnhs', 1], ['丁', 'hs', 1],
           ['戊', 'sss', 1], ['己', 'nnnn', 1], ['庚', 'zzzz', 1], ['辛', 'hhh', 1]]
RANKS = {c: i for i, (c, *_) in enumerate(ENTRIES)}


def ink(code, **extra):
    return [dict(stroke_evidence(c), manual=False, **extra) for c in code]


def scored(category, distances, length=None):
    stroke = dict(category=category, manual=False,
                  recognition=dict(category_scores=[dict(category=c, distance=d) for c, d in distances.items()]))
    if length is not None:
        stroke['kinematics'] = dict(length=length)
    return stroke


def test_substitution_costs_follow_recognizer_distances_and_length():
    costs = substitution_costs(scored('h', dict(h=.01, z=.05, s=.9)))
    assert costs['h'] == 0. and costs['z'] < .5 < costs['s'] == pytest.approx(1.)
    assert costs['p'] == pytest.approx(1.)  # no score: treated as far
    short = substitution_costs(scored('h', dict(h=.01, z=.05, s=.9), length=20))
    assert short['s'] == pytest.approx(DEFAULT_TOLERANT_POLICY.short_factor)
    assert short['z'] < costs['z']
    manual = substitution_costs(dict(category='h', manual=True))
    assert manual['h'] == 0. and manual['z'] == pytest.approx(1.)


def test_prefix_with_a_wrong_stroke_still_finds_the_character():
    index = TolerantIndex(ENTRIES, RANKS)
    # 乙 is hspnz; the third stroke was read as h, and the character is unfinished.
    rows = tolerant_candidates(index, ink('hsh'), needed=3)
    by = {row['char']: row for row in rows}
    assert '乙' in by and by['乙']['prefix_length'] == 3
    assert by['乙']['glyph_corrections'] == [2] and by['乙']['corrections'] == [2]
    assert all(row['match_type'] == 'tolerant' and not row['exact_match'] for row in rows)


def test_extra_and_missing_strokes_are_aligned():
    index = TolerantIndex(ENTRIES, RANKS)
    extra = {r['char']: r for r in tolerant_candidates(index, ink('pnnhs'), needed=8)}
    assert extra['丙']['cost'] == pytest.approx(DEFAULT_TOLERANT_POLICY.extra_cost)
    missing = {r['char']: r for r in tolerant_candidates(index, ink('pns'), needed=8)}
    assert missing['丙']['cost'] == pytest.approx(DEFAULT_TOLERANT_POLICY.missing_cost)


def test_fill_page_keeps_existing_order_and_always_reaches_six():
    index = TolerantIndex(ENTRIES, RANKS)
    chars, rows = fill_page(['辛'], [dict(char='辛')], index=index, evidence=ink('zzzzzzzzzzzz'))
    assert chars[0] == '辛' and len(chars) == 6 == len(set(chars))
    assert [r['char'] for r in rows] == chars
    assert {r.get('tolerant_level') for r in rows[1:]} <= {2, 3}
    full = list('甲乙丙丁戊己')
    assert fill_page(full, [], index=index, evidence=ink('h')) == (full, [])
    assert fill_page([], [], index=index, evidence=[]) == ([], [])
    manual = [stroke_evidence(c) for c in 'zzzz']
    assert fill_page([], [], index=index, evidence=manual) == ([], [])


def test_budget_cap_falls_back_to_frequent_characters():
    index = TolerantIndex(ENTRIES, RANKS)
    tight = TolerantPolicy(max_budget=.5)
    assert tolerant_candidates(index, ink('zzzzzzzz'), needed=6, policy=tight) == []
    chars, rows = fill_page([], [], index=index, evidence=ink('zzzzzzzz'), policy=tight)
    assert chars == index.frequent[:6] and all(r['tolerant_level'] == 3 for r in rows)


def test_allowed_scope_and_exclusions_are_respected():
    index = TolerantIndex(ENTRIES, RANKS, allowed_characters={'甲', '乙'})
    rows = tolerant_candidates(index, ink('hs'), needed=6, excluded={'甲'})
    assert [r['char'] for r in rows] == ['乙']
