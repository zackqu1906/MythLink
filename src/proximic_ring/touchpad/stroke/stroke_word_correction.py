"""Bounded whole-character repair, independent of single-stroke recognition.

These costs are heuristics, not probabilities. Manual strokes are observations
too: a user can confidently enter the wrong category. The original observations
are never changed. Only complete dictionary orders are proposed as repairs.
"""
from collections import defaultdict
from dataclasses import dataclass
import math

from .stroke_input import SYMBOLS
from .stroke_context import CORRECTION_LANGUAGE_WEIGHT


@dataclass(frozen=True)
class CorrectionPolicy:
    min_strokes: int = 3
    max_strokes: int = 32
    max_edits: int = 2
    two_edit_min_strokes: int = 6
    limit: int = 18

    def snapshot(self):
        return dict(version=1, minStrokes=self.min_strokes, maxStrokes=self.max_strokes,
                    maxEdits=self.max_edits, twoEditMinStrokes=self.two_edit_min_strokes,
                    limit=self.limit, operations=['substitute', 'missing', 'extra', 'transpose'],
                    completeOrdersOnly=True, trailingCompletionIsNotError=True)


DEFAULT_CORRECTION_POLICY = CorrectionPolicy()


def align_strokes(observed, expected):
    """Optimal-string-alignment distance and an executable edit alignment.

    Indices are zero-based in the original observed / expected strings. Missing
    refers to a stroke the user omitted; extra refers to an observed stroke.
    Transposition is one swap of two adjacent, different categories.
    """
    n, m = len(observed), len(expected)
    distances = [[0] * (m + 1) for _ in range(n + 1)]
    previous = {}
    for i in range(1, n + 1):
        distances[i][0] = i
        previous[i, 0] = (i - 1, 0, 'extra')
    for j in range(1, m + 1):
        distances[0][j] = j
        previous[0, j] = (0, j - 1, 'missing')
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            equal = observed[i - 1] == expected[j - 1]
            options = [(distances[i - 1][j - 1] + (not equal), i - 1, j - 1,
                        'match' if equal else 'substitute')]
            if (i > 1 and j > 1 and observed[i - 1] == expected[j - 2]
                    and observed[i - 2] == expected[j - 1] and observed[i - 1] != observed[i - 2]):
                options.append((distances[i - 2][j - 2] + 1, i - 2, j - 2, 'transpose'))
            options.extend(((distances[i - 1][j] + 1, i - 1, j, 'extra'),
                            (distances[i][j - 1] + 1, i, j - 1, 'missing')))
            best = min(options, key=lambda item: item[0])
            distances[i][j], pi, pj, kind = best
            previous[i, j] = (pi, pj, kind)
    i, j, edits, mapping = n, m, [], [None] * m
    while i or j:
        pi, pj, kind = previous[i, j]
        if kind == 'match':
            mapping[pj] = pi
        else:
            edits.append(dict(kind=kind, observed_index=pi, expected_index=pj,
                              observed=observed[pi:i], expected=expected[pj:j]))
            if kind == 'substitute':
                mapping[pj] = pi
            elif kind == 'transpose':
                mapping[pj], mapping[pj + 1] = pi + 1, pi
        i, j = pi, pj
    edits.reverse()
    return dict(distance=distances[n][m], edits=edits, expected_to_observed=mapping)


def describe_edits(edits):
    def symbols(code):
        return ''.join(SYMBOLS[c].replace('𠃍', '乛') for c in code)
    parts = []
    for edit in edits:
        kind, oi, ei = edit['kind'], edit['observed_index'] + 1, edit['expected_index'] + 1
        if kind == 'missing':
            parts.append(f'补第{ei}笔{symbols(edit["expected"])}')
        elif kind == 'extra':
            parts.append(f'忽略输入第{oi}笔{symbols(edit["observed"])}')
        elif kind == 'transpose':
            parts.append(f'交换输入第{oi}、{oi + 1}笔')
        else:
            parts.append(f'输入第{oi}笔{symbols(edit["observed"])}→{symbols(edit["expected"])}')
    return '；'.join(parts)


class WholeCharacterCorrector:
    """Search a dictionary trie with a bounded Damerau edit-distance row.

    The trie searches all initials, so a wrong first stroke can be recovered.
    Full alignments are constructed only for matching codes. No reference text
    or next target character is accepted by this API.
    """
    def __init__(self, entries, ranks, policy=DEFAULT_CORRECTION_POLICY, *, allowed_characters=None):
        self.policy = policy
        self.ranks = dict(ranks)
        self.by_code = defaultdict(set)
        self.trie = {}
        for char, code, *_ in entries:
            if allowed_characters is not None and char not in allowed_characters:
                continue
            if len(code) > policy.max_strokes + policy.max_edits:
                continue
            if len(char) != 1 or not code or any(c not in SYMBOLS for c in code):
                continue
            self.by_code[code].add(char)
        for code in self.by_code:
            node = self.trie
            for letter in code:
                node = node.setdefault(letter, {})
            node[''] = code

    def _nearby(self, observed, budget):
        n = len(observed)
        stack = [(self.trie, '', list(range(n + 1)), None, '')]
        while stack:
            node, prefix, previous, before_previous, last = stack.pop()
            for letter, child in node.items():
                if not letter:
                    continue
                depth = len(prefix) + 1
                row = [depth]
                for j, value in enumerate(observed, 1):
                    cost = min(row[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (letter != value))
                    if (before_previous is not None and j > 1
                            and letter == observed[j - 2] and last == value):
                        cost = min(cost, before_previous[j - 2] + 1)
                    row.append(cost)
                if '' in child and 0 < row[n] <= budget:
                    yield child[''], row[n]
                if depth < n + budget and min(row) <= budget:
                    stack.append((child, prefix + letter, row, previous, letter))

    def candidates(self, observed, *, evidence=(), context='', predictor=None,
                   allowed_characters=None, excluded_characters=()):
        policy = self.policy
        if (not policy.min_strokes <= len(observed) <= policy.max_strokes
                or any(c not in SYMBOLS for c in observed)):
            return []
        budget = policy.max_edits if len(observed) >= policy.two_edit_min_strokes else 1
        excluded = set(excluded_characters)
        rows = {}
        for code, distance in self._nearby(observed, budget):
            # A valid prefix is ordinary unfinished input, not a missing tail.
            if code.startswith(observed):
                continue
            characters = self.by_code[code] - excluded
            if allowed_characters is not None:
                characters = characters.intersection(allowed_characters)
            if not characters:
                continue
            alignment = align_strokes(observed, code)
            cost = 0.
            corrected_targets, corrected_observations = [], []
            for edit in alignment['edits']:
                kind, oi, ei = edit['kind'], edit['observed_index'], edit['expected_index']
                cost += dict(substitute=1., missing=1.15, extra=1.1, transpose=.95)[kind]
                if kind == 'substitute' and oi < len(evidence):
                    # Close recognition alternatives help rank; absence of one
                    # never prohibits a writing-error hypothesis.
                    if edit['expected'] in evidence[oi].get('costs', {}):
                        cost -= .2
                if kind != 'extra':
                    corrected_targets.extend(range(ei, ei + len(edit['expected'])))
                if kind != 'missing':
                    corrected_observations.extend(range(oi, oi + len(edit['observed'])))
            for char in sorted(characters):
                prior = .035 * math.log1p(max(0, self.ranks.get(char, 100000)))
                key = (distance, cost + prior, len(code), code)
                if char not in rows or key < rows[char]['_key']:
                    rows[char] = dict(char=char, code=code, cost=round(cost, 4),
                        edit_distance=distance, edits=alignment['edits'],
                        expected_to_observed=alignment['expected_to_observed'],
                        corrections=corrected_observations,
                        glyph_corrections=corrected_targets,
                        correction_summary=describe_edits(alignment['edits']),
                        exact_match=False, match_type='whole_character', _key=key)
        bonuses = predictor.stroke_bonuses(context, rows) if predictor else {}
        ranked = sorted(rows.values(), key=lambda row: (
            row['_key'][0], row['_key'][1] - CORRECTION_LANGUAGE_WEIGHT * bonuses.get(row['char'], 0.),
            *row['_key'][2:], row['char']))[:policy.limit]
        return [dict({k: v for k, v in row.items() if k != '_key'},
                     language_bonus=bonuses.get(row['char'], 0.)) for row in ranked]


def merge_candidates(normal, repairs, reserved=2, page_size=6):
    """Keep the first normal choice, reserve first-page space for real repairs.

    The existing list remains in order, with all original candidates reachable.
    A character already present through strict/fuzzy input is never duplicated.
    """
    seen = set(normal)
    additions = []
    for row in repairs:
        if row['char'] not in seen:
            additions.append(row['char'])
            seen.add(row['char'])
    keep = min(len(normal), max(1, page_size - reserved))
    return normal[:keep] + additions[:reserved] + normal[keep:] + additions[reserved:]
