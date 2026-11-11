"""Keep the first candidate page full, however wrong the written code is.

Ordinary matching only accepts near classes and bounded repairs, so after one
badly misread stroke a long character can have no candidates at all. This
module only fills empty slots behind the existing list; it never reorders or
removes an existing candidate.

Level 2 aligns the written strokes with every dictionary order's prefix,
allowing substituted, extra, missing and swapped strokes. A substitution is
cheap when the recognizer found the other class close, and cheaper for short
strokes, whose class is least reliable. The edit budget is widened step by
step until enough characters are found. Level 3 tops up with context
suggestions, then frequent characters. Costs are heuristics, not probabilities.
"""
from __future__ import annotations

from dataclasses import dataclass
import heapq
import math

from .stroke_word_correction import align_strokes

CATEGORIES = 'hspnz'
PAGE_SIZE = 6


@dataclass(frozen=True)
class TolerantPolicy:
    near_cost: float = .35
    far_cost: float = 1.
    near_gap: float = .3  # category distance gap at which a substitution costs far_cost
    short_length: float = 60.  # SDK units, matches ADAPTIVE_SETTINGS['shortLength']
    short_factor: float = .6
    # Above a far substitution, so a wrong last stroke reads as misrecognized, not as junk.
    extra_cost: float = 1.1
    missing_cost: float = 1.
    transpose_cost: float = .9
    budget_step: float = 1.
    # Beyond about four errors a guess is noise and the search gets slow; level 3 takes over.
    max_budget: float = 4.
    per_node: int = 8
    language_weight: float = 1.

    def snapshot(self):
        return dict(version=1, nearCost=self.near_cost, farCost=self.far_cost, nearGap=self.near_gap,
                    shortLength=self.short_length, shortFactor=self.short_factor,
                    extraCost=self.extra_cost, missingCost=self.missing_cost,
                    transposeCost=self.transpose_cost, budgetStep=self.budget_step, maxBudget=self.max_budget,
                    pageSize=PAGE_SIZE, languageWeight=self.language_weight)


DEFAULT_TOLERANT_POLICY = TolerantPolicy()


def substitution_costs(stroke, policy=DEFAULT_TOLERANT_POLICY):
    """Cost of reading this stroke as each class; the recognized class is free."""
    observed = stroke['category']
    scores = {}
    for item in stroke.get('recognition', {}).get('category_scores', []):
        category, distance = item.get('category'), item.get('distance')
        if (category in CATEGORIES and isinstance(distance, (int, float)) and not isinstance(distance, bool)
                and math.isfinite(distance) and distance >= 0):
            scores[category] = min(scores.get(category, math.inf), distance)
    best = scores.get(observed)
    factor = policy.short_factor if _short(stroke, policy) else 1.
    costs = {}
    for category in CATEGORIES:
        if category == observed:
            costs[category] = 0.
        elif stroke.get('manual', True) or best is None or category not in scores:
            costs[category] = policy.far_cost*factor
        else:
            ratio = min(1., max(0., scores[category]-best)/policy.near_gap)
            costs[category] = (policy.near_cost + (policy.far_cost-policy.near_cost)*ratio)*factor
    return costs


def _short(stroke, policy):
    length = stroke.get('kinematics', {}).get('length')
    return (not stroke.get('manual', True) and isinstance(length, (int, float))
            and not isinstance(length, bool) and 0 < length < policy.short_length)


class TolerantIndex:
    """Trie of dictionary orders; each node keeps its most frequent characters."""
    def __init__(self, entries, ranks, *, allowed_characters=None, per_node=DEFAULT_TOLERANT_POLICY.per_node):
        self.ranks = ranks
        self.root = {}
        self.frequent = []
        best = {}
        for char, code, *_ in entries:
            if allowed_characters is not None and char not in allowed_characters:
                continue
            if len(char) != 1 or not code or any(c not in CATEGORIES for c in code):
                continue
            node = self.root
            for letter in code:
                node = node.setdefault(letter, {})
            node.setdefault('', set()).add(char)
            best[char] = ranks.get(char, 100000)
        self.frequent = [c for c, _ in sorted(best.items(), key=lambda item: (item[1], item[0]))]
        self._top(self.root, per_node, '')

    def _top(self, node, limit, prefix):
        # (rank, char, full code) for the most frequent characters under this node.
        items = [(self.ranks.get(c, 100000), c, prefix) for c in node.get('', ())]
        for letter, child in node.items():
            if letter:
                items.extend(self._top(child, limit, prefix+letter))
        node['top'] = heapq.nsmallest(limit, items)
        return node['top']


def _search(index, costs, policy, budget):
    """Weighted alignment of all written strokes against every code prefix."""
    n = len(costs)
    extra = [policy.extra_cost*(policy.short_factor if c['_short'] else 1.) for c in costs]
    first = [0.]
    for i in range(n):
        first.append(first[-1]+extra[i])
    hits = []
    stack = [(index.root, '', first, None)]
    while stack:
        node, prefix, row, before = stack.pop()
        for letter, child in node.items():
            if letter in ('', 'top'):
                continue
            depth = len(prefix)+1
            current = [row[0]+policy.missing_cost]
            for j in range(1, n+1):
                value = min(row[j-1]+costs[j-1][letter], row[j]+policy.missing_cost, current[j-1]+extra[j-1])
                if (before is not None and j > 1 and prefix and letter != prefix[-1]
                        and costs[j-1][prefix[-1]] == 0. and costs[j-2][letter] == 0.):
                    value = min(value, before[j-2]+policy.transpose_cost)
                current.append(value)
            if current[n] <= budget:
                hits.append((current[n], depth, child))
            if min(current) <= budget:
                stack.append((child, prefix+letter, current, row))
    return hits


def tolerant_candidates(index, evidence, *, needed=PAGE_SIZE, excluded=(), predictor=None, context='',
                        policy=DEFAULT_TOLERANT_POLICY, priors=None):
    """Up to ``needed`` new rows, widening the edit budget until enough exist."""
    if not evidence or needed <= 0:
        return []
    observed = ''.join(stroke['category'] for stroke in evidence)
    costs = []
    for stroke in evidence:
        table = substitution_costs(stroke, policy)
        table['_short'] = _short(stroke, policy)
        costs.append(table)
    excluded = set(excluded)
    found = {}
    budget = policy.budget_step
    ceiling = min(policy.max_budget, policy.far_cost*len(evidence)+policy.missing_cost)
    while True:
        for cost, depth, node in _search(index, costs, policy, budget):
            for rank, char, code in node['top']:
                if char in excluded:
                    continue
                if char not in found or cost < found[char][0]:
                    found[char] = (cost, depth, code, rank)
        if len(found) >= needed or budget >= ceiling:
            break
        budget += policy.budget_step
    priors = priors or {}
    bonuses = predictor.stroke_bonuses(context, found) if predictor and found else {}
    def score(char):
        cost, _, _, rank = found[char]
        prior = priors.get(char, .35*math.log1p(rank))
        return cost + prior - policy.language_weight*bonuses.get(char, 0.)
    rows = []
    for char in sorted(found, key=lambda c: (score(c), c))[:needed]:
        cost, depth, code, _ = found[char]
        alignment = align_strokes(observed, code[:depth])
        glyph_corrections, corrections = [], []
        for edit in alignment['edits']:
            kind, oi, ei = edit['kind'], edit['observed_index'], edit['expected_index']
            if kind != 'extra':
                glyph_corrections.extend(range(ei, ei+len(edit['expected'])))
            if kind != 'missing':
                corrections.extend(range(oi, oi+len(edit['observed'])))
        rows.append(dict(char=char, code=code, prefix_length=depth, cost=round(cost, 4),
                         score=round(score(char), 4), language_bonus=bonuses.get(char, 0.),
                         edits=alignment['edits'], corrections=corrections, glyph_corrections=glyph_corrections,
                         exact_match=False, match_type='tolerant', tolerant_level=2))
    return rows


def fill_page(chars, rows, *, index, evidence, predictor=None, context='', allowed=None,
              policy=DEFAULT_TOLERANT_POLICY, priors=None, page_size=PAGE_SIZE):
    """Append guesses until the first page is full; existing order is kept.

    Entirely manual input is locked, as in fuzzy matching, and gets no guesses.
    """
    if (not evidence or len(chars) >= page_size
            or all(stroke.get('manual', True) for stroke in evidence)):
        return chars, rows
    chars, rows = list(chars), list(rows)
    needed = page_size-len(chars)
    guesses = tolerant_candidates(index, evidence, needed=needed, excluded=chars, predictor=predictor,
                                  context=context, policy=policy, priors=priors)
    chars += [row['char'] for row in guesses]
    rows += guesses
    if len(chars) < page_size:
        # Level 3: what usually comes next here, then simply frequent characters.
        pool = (predictor.suggestions(context, allowed_characters=allowed) if predictor and context else [])
        for char in [*pool, *index.frequent]:
            if len(chars) >= page_size:
                break
            if char not in chars:
                chars.append(char)
                rows.append(dict(char=char, code='', exact_match=False, corrections=[], glyph_corrections=[],
                                 match_type='tolerant', tolerant_level=3))
    return chars, rows


def index_for(dictionary, allowed=None):
    """One cached index per dictionary and allowed-character scope."""
    key = frozenset(allowed) if allowed is not None else None
    cache = dictionary.__dict__.setdefault('_tolerant_indexes', {})
    if key not in cache:
        cache[key] = TolerantIndex(dictionary.entries, dictionary.ranks, allowed_characters=key)
    return cache[key]
