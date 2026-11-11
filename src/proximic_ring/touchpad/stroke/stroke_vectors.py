"""Rank candidates by each stroke's start→end vector.

The five-class code drops stroke length and angle, so 土／士／工 or 人／八／入
tie and fall back to frequency. Each written stroke keeps its contact vector;
both sets are scaled by their RMS length and compared position by position.
Lifted-hand gaps are not used: the SDK does not observe them.

Scope 'prefix' (default) scores every candidate from the first stroke on,
including unfinished and corrected ones, before the list is truncated; the
vector then also acts as a soft check on each stroke's class.

Scope 'complete' (v1) reorders only exact complete matches, among their own list positions.
Weight 1.75 against the fuzzy frequency prior corresponds to the cost +
0.2·log(rank) setting chosen across two P01 sessions (docs/STROKE_LAYOUT.md).
Scores are engineering distances, not probabilities.
"""
from __future__ import annotations

from functools import lru_cache
import gzip
import json
import math
from importlib.resources import files

DEFAULT_WEIGHT = 1.75
MIN_STROKES = 2
# Prefix scope: weight per written stroke on the mean cost. 1.0 was best on the
# 2026-10 sentence-trial replay among .25/.5/1/2 (docs/STROKE_LAYOUT.md).
PREFIX_WEIGHT = 1.
SCOPES = ('prefix', 'complete')
# Optionally leave folds out: their start→end chord depends on how far each leg
# is drawn. Off by default; skipping them lost more than it saved in the replay.
SKIP_FOLDS = False


def stroke_vector(points):
    """Raw SDK displacement from first to last point, or None."""
    try:
        (x0, y0), (x1, y1) = points[0], points[-1]
        vector = [float(x1)-float(x0), float(y1)-float(y0)]
    except (IndexError, TypeError, ValueError):
        return None
    return vector if all(math.isfinite(v) for v in vector) and any(vector) else None


def _scaled(vectors):
    rms = math.sqrt(sum(x*x+y*y for x, y in vectors)/len(vectors))
    return [(x/rms, y/rms) for x, y in vectors] if rms > 1e-9 else None


def vector_cost(observed, template):
    a, b = _scaled(observed), _scaled(template)
    if a is None or b is None or len(a) != len(b):
        return None
    return sum((ax-bx)**2+(ay-by)**2 for (ax, ay), (bx, by) in zip(a, b))/len(a)


class StrokeVectorModel:
    def __init__(self, characters=None, *, weight=DEFAULT_WEIGHT, scope='prefix', prefix_weight=PREFIX_WEIGHT,
                 skip_folds=SKIP_FOLDS):
        if scope not in SCOPES:
            raise ValueError('Unknown stroke vector scope')
        self.characters = _builtin() if characters is None else characters
        self.weight = weight
        self.scope = scope
        self.prefix_weight = prefix_weight
        self.skip_folds = skip_folds

    def snapshot(self):
        if self.scope == 'prefix':
            return dict(model='stroke_vectors_v2', scope='prefix', prefixWeight=self.prefix_weight,
                        skipFolds=self.skip_folds, characters=len(self.characters))
        return dict(model='stroke_vectors_v1', weight=self.weight, minStrokes=MIN_STROKES,
                    characters=len(self.characters), scope='exact complete same-length matches')

    def prefix_cost(self, character, code, evidence, mapping=None):
        """(mean cost, stroke count) of the written strokes against the glyph.

        ``code`` is the reference spelling; ``mapping`` sends written stroke i
        to its reference index (component paths may reorder). Manual strokes
        and strokes without a vector are left out rather than guessed.
        """
        entry = self.characters.get(character)
        if entry is None or entry['code'] != code:
            return None
        observed, template = [], []
        for i, stroke in enumerate(evidence):
            j = mapping[i] if mapping else i
            vector = stroke.get('vector')
            if vector is None or stroke.get('manual', False) or j is None or j >= len(entry['vectors']):
                continue
            if self.skip_folds and code[j] == 'z':
                continue
            observed.append(vector)
            template.append(entry['vectors'][j])
        cost = vector_cost(observed, template) if observed else None
        return None if cost is None else (cost, len(observed))

    def score_rows(self, rows, evidence):
        """Add ``vector_score`` to every row: final score + weight·n·cost.

        Applies to complete, prefix and corrected rows alike, so a corrected
        stroke whose ink fits the candidate's stroke is helped and one that does
        not is penalized. Rows without an aligned glyph get their tier's mean
        term, so they are neither helped nor hurt. Tiers are never crossed.
        """
        if self.prefix_weight <= 0 or not evidence:
            return rows
        terms, known = [], {}
        for row in rows:
            result = self.prefix_cost(row['char'], row.get('reference_code', row['code']), evidence,
                                      row.get('observed_to_expected'))
            term = None if result is None else self.prefix_weight*result[0]*result[1]
            terms.append(term)
            if term is not None:
                known.setdefault(row.get('key', (0,))[0], []).append(term)
        for row, term in zip(rows, terms):
            tier = known.get(row.get('key', (0,))[0])
            if not tier:
                continue
            row['vector_term'] = term
            base = row.get('final_score', row.get('score', 0.))
            row['vector_score'] = base + (term if term is not None else sum(tier)/len(tier))
        return rows

    def cost(self, character, code, evidence):
        entry = self.characters.get(character)
        # Vector i must belong to code letter i; other stroke orders are skipped.
        if entry is None or entry['code'] != code or len(code) != len(evidence):
            return None
        observed = [stroke.get('vector') for stroke in evidence]
        if any(v is None for v in observed) or any(s.get('manual', False) for s in evidence):
            return None
        return vector_cost(observed, entry['vectors'])

    def rerank(self, rows, evidence, priors):
        """Reorder exact complete rows in place of each other; others stay put.

        Fuzzy, left-dot and replacement rows are never exact. An exact row that
        fold evidence demoted keeps its tier: rows move only within one tier.
        """
        if self.scope != 'complete' or len(evidence) < MIN_STROKES or self.weight <= 0:
            return rows
        tiers = {}
        for i, row in enumerate(rows):
            if row.get('exact_match') and len(row.get('code', '')) == len(evidence):
                tiers.setdefault(row.get('key', (0,))[0], []).append(i)
        result = list(rows)
        for slots in tiers.values():
            self._rerank_slots(result, slots, evidence, priors)
        return result

    def _rerank_slots(self, rows, slots, evidence, priors):
        costs = {i: self.cost(rows[i]['char'], rows[i]['code'], evidence) for i in slots}
        known = [c for c in costs.values() if c is not None]
        if len(known) < 2:
            return
        # A character without an aligned template is neither helped nor hurt.
        neutral = sum(known)/len(known)
        def base(row):
            return row.get('final_score', row.get('score', priors.get(row['char'], 0.)))
        scored = []
        for i in slots:
            cost = costs[i] if costs[i] is not None else neutral
            scored.append((base(rows[i]) + self.weight*cost, i, dict(
                rows[i], vector_cost=costs[i], vector_score=base(rows[i]) + self.weight*cost)))
        for slot, (_, _, row) in zip(slots, sorted(scored, key=lambda item: (item[0], item[1]))):
            rows[slot] = row


@lru_cache(maxsize=1)
def _builtin():
    with gzip.open(files('proximic_ring').joinpath('assets/stroke-vectors.json.gz'), 'rt', encoding='utf-8') as stream:
        return json.load(stream)['characters']
