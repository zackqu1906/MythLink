"""Local next-character suggestions, ported from the existing demo predictor."""
import gzip
from importlib.resources import files
import json
import math
import re


FUZZY_LANGUAGE_WEIGHT = 1.
COMPLETED_FUZZY_LANGUAGE_WEIGHT = 1.6
CORRECTION_LANGUAGE_WEIGHT = .35
# Over the first strokes the ink says little, so a well-covered context model
# may count for more. Only the Wanxiang model sets a factor (trial replay:
# 1.5 helped strokes 1–3 and slightly hurt 4–5, so it stops after three).
EARLY_STROKES = 3
EARLY_LANGUAGE_FACTOR = {'wanxiang': 1.5}


def ranking_snapshot():
    from .stroke_folds import FOLD_POLICY, NEAR_FOLDS
    return dict(version=6, fuzzyLanguageWeight=FUZZY_LANGUAGE_WEIGHT,
                foldRanking=dict(FOLD_POLICY, nearFolds=[list(pair) for pair in NEAR_FOLDS]),
                completedFuzzyLanguageWeight=COMPLETED_FUZZY_LANGUAGE_WEIGHT,
                correctionLanguageWeight=CORRECTION_LANGUAGE_WEIGHT,
                completedFuzzySlot=2, completedFuzzyMinStrokes=5,
                completedFuzzyMaxCost=2.5, completedFuzzyMaxCorrections=1,
                completedFuzzyMinLanguageBonus=.5, earlyStrokes=EARLY_STROKES,
                earlyLanguageFactor=dict(EARLY_LANGUAGE_FACTOR))


def early_bonuses(predictor, bonuses, strokes):
    factor = getattr(predictor, 'early_language_factor', 1.)
    if factor == 1. or strokes > EARLY_STROKES:
        return bonuses
    return {char: factor*value for char, value in bonuses.items()}


def promote_completed_fuzzy(rows, evidence, predictor, context, limit):
    """Reserve slot two for one complete, physically supported alternative.

The first literal choice stays first. Longer matching prefixes must not bury a
complete character with one ambiguous stroke. Context is required, but sparse
corpus support can fall back to the existing frequency-based language bonus.
No new character or stroke interpretation is introduced by this step.
"""
    if limit <= 0:
        return []
    if (limit < 2 or len(evidence) < 5 or not predictor or
            not getattr(predictor, 'has_context', lambda text: False)(context) or
            not any(not item.get('manual', True) for item in evidence)):
        return rows[:limit]
    completed = [row for row in rows if len(row['code']) == len(evidence)
                 and not row.get('exact_match') and len(row.get('corrections', ())) == 1
                 and row.get('cost', 0.) <= 2.5 and row.get('language_bonus', 0.) >= .5]
    if not completed:
        return rows[:limit]
    preferred = min(completed, key=lambda row: (
        row['final_score']-(COMPLETED_FUZZY_LANGUAGE_WEIGHT-FUZZY_LANGUAGE_WEIGHT)*row['language_bonus'], row['char']))
    if preferred is rows[0]:
        return rows[:limit]
    promoted = dict(preferred, context_promoted=True,
                    context_extra_slot=any(row is preferred for row in rows[4:]))
    return ([rows[0], promoted] + [row for row in rows[1:] if row is not preferred])[:limit]


# Legacy: Tatoeba + a Wikipedia sample, top 8 followers per context.
# Wanxiang: rime-wanxiang-data grammar and dictionary (tools/build_stroke_context_wanxiang.py).
CONTEXT_ASSETS = {'legacy': 'stroke-context.json.gz', 'wanxiang': 'stroke-context-wanxiang.json.gz'}


class StrokeContext:
    def __init__(self, data=None, *, model='legacy'):
        self.model = model if data is None else 'custom'
        self.early_language_factor = EARLY_LANGUAGE_FACTOR.get(self.model, 1.)
        if data is None:
            with gzip.open(files('proximic_ring').joinpath('assets/' + CONTEXT_ASSETS[model]), 'rt', encoding='utf-8') as stream:
                data = json.load(stream)
        self.data = data

    def _rows(self, text):
        match = re.search(r'[\u3400-\u9fff]+$', text)
        if not match:
            return []
        tail = match[0][-self.data['maxContext']:]
        return [self.data['contexts'][tail[-n:]] for n in range(1, len(tail)+1)
                if tail[-n:] in self.data['contexts']]

    def _baseline(self, char):
        return (self.data['unigrams'].get(char, 0)+.1)/(self.data['total']+.1*len(self.data['unigrams']))

    def has_context(self, text):
        return bool(self._rows(text))

    def _probability(self, char, rows):
        value = self._baseline(char)
        for total, followers, counts in rows:
            count = counts[followers.index(char)] if char in followers else 0
            value = (count+6*value)/(total+6)
        return value

    def stroke_bonuses(self, text, characters):
        """Bound corpus frequency and contextual lift so shape evidence still matters."""
        rows = self._rows(text)
        return {char: min(1., .12*math.log1p(self.data['unigrams'].get(char, 0))) +
                (.25*max(-2., min(5., math.log(self._probability(char, rows)/self._baseline(char))))
                 if rows else 0.) for char in characters}

    def suggestions(self, text, limit=30, *, allowed_characters=None):
        rows = self._rows(text)
        candidates = set(''.join(row[1] for row in rows))
        if allowed_characters is not None:
            candidates.intersection_update(allowed_characters)
        return sorted(candidates, key=lambda char: (-self._probability(char, rows), char))[:limit]

    def character_evidence(self, text, characters):
        """Expose corpus support separately from a candidate's stroke match."""
        rows = self._rows(text)
        evidence = {}
        for char in characters:
            baseline = self._baseline(char)
            probability = self._probability(char, rows)
            support = max((counts[followers.index(char)] if char in followers else 0
                           for _, followers, counts in rows), default=0)
            evidence[char] = dict(char=char, probability=probability, baseline=baseline,
                                  lift=probability / baseline, support=support)
        return dict(hasContext=bool(rows), contextSupport=max((row[0] for row in rows), default=0),
                    characters=evidence)
