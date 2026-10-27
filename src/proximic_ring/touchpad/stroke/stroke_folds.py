"""Soft, position-specific fold ranking. Never removes a five-category match.

cnchar intentionally shares some names in one letter. Treat those names as a
set rather than inventing a distinction the dictionary cannot supervise.
"""
from functools import lru_cache
import gzip
from importlib.resources import files
import json
import math


FOLD_GROUPS = {
    'a': ('横折折撇',), 'b': ('竖弯',), 'c': ('横折',),
    'e': ('横撇', '横钩'), 'h': ('竖提',), 'm': ('撇点',),
    'n': ('撇折',), 'o': ('横斜钩',), 'p': ('横折提',),
    'q': ('横折折折',), 'r': ('横折钩',), 't': ('弯钩',),
    'u': ('竖弯钩',), 'v': ('横折折', '横折弯'),
    'w': ('横折折折钩', '横撇弯钩'), 'x': ('竖折撇', '竖折折'),
    'y': ('斜钩', '卧钩'), 'z': ('竖折折钩',),
}
COARSE = {letter: ('h' if letter in 'ji' else 's' if letter in 'fg' else
                   'p' if letter == 's' else 'n' if letter in 'dkl' else 'z')
          for letter in 'abcdefghijklmnopqrstuvwxyz'}
# The recording labels use these two extra names for cnchar's shared shapes.
# cnchar encodes 乙/九 with o and the L-shaped stroke in 山 with b.
SHAPE_ALIASES = {'横折弯钩': '横斜钩', '竖折': '竖弯'}
FOLD_NAMES = frozenset(name for names in FOLD_GROUPS.values() for name in names)
FOLD_POLICY = dict(version=1, distanceScale=.06, maxCost=2.4,
                   neutralCost=.9, weight=1.6, compatibleCost=1.2, maxAdjustment=4.)


def coarse_order(order):
    return ''.join(COARSE[letter] for letter in order)


@lru_cache(maxsize=1)
def fold_orders():
    with gzip.open(files('proximic_ring').joinpath('assets/stroke-folds.json.gz'),
                   'rt', encoding='utf-8') as stream:
        data = json.load(stream)
    if data['schema'] != 'ring-fold-orders' or data['version'] != 1:
        raise ValueError('Unsupported fold annotations')
    return {char: (coarse_order(order), order) for char, order in data['orders'].items()}


def fold_evidence(evidence, policy):
    """Retain every scored fold, with a broad continuous cost and no top-k gate.

    Unreliable, manual, or category-only observations supply no fine evidence.
    Higher tolerance flattens the costs; the original observation stays intact.
    """
    result = {}
    for index, stroke in enumerate(evidence):
        if stroke.get('manual', True) or stroke['category'] != 'z':
            continue
        recognition = stroke.get('recognition', {})
        scores = {}
        for row in recognition.get('shape_scores', []):
            name, value = row.get('shape'), row.get('distance')
            name = SHAPE_ALIASES.get(name, name)
            if (row.get('category') == 'z' and name in FOLD_NAMES
                    and isinstance(value, (int, float)) and not isinstance(value, bool)
                    and math.isfinite(value) and value >= 0):
                scores[name] = min(scores.get(name, math.inf), value)
        # A lone winning label cannot establish how far away the alternatives are.
        if len(scores) < 2 or min(scores.values()) > .08:
            continue
        best = min(scores.values())
        scale = .5 + stroke.get('effective_policy', {}).get('level', policy.level)/100
        costs = {name: min(FOLD_POLICY['maxCost'], (value-best)/((FOLD_POLICY['distanceScale']+best)*scale))
                 for name, value in scores.items()}
        result[index] = dict(observed=recognition.get('shape'), costs=costs)
    return result


def fold_adjustment(char, code, orders, evidence):
    """Use only a shared canonical prefix, never align by stroke count alone.

    Unknown characters/variants are neutral. Known close shapes gain a bonus;
    distant ones get a bounded penalty, so lack of annotation is not a veto.
    """
    annotation = orders.get(char)
    details = []
    adjustment = 0.
    if annotation is not None:
        canonical, order = annotation
        for index, ink in evidence.items():
            if (code[index:index+1] != 'z' or index >= len(order)
                    or code[:index+1] != canonical[:index+1]):
                continue
            expected = FOLD_GROUPS.get(order[index], ())
            costs = [ink['costs'][name] for name in expected if name in ink['costs']]
            if not costs:
                continue
            cost = min(costs)
            adjustment += FOLD_POLICY['weight']*(cost-FOLD_POLICY['neutralCost'])
            observed = SHAPE_ALIASES.get(ink['observed'], ink['observed'])
            details.append(dict(index=index, observed=ink['observed'], expected=list(expected),
                                cost=cost, corrected=observed not in expected))
    bound = FOLD_POLICY['maxAdjustment']
    return dict(fold_score=max(-bound, min(bound, adjustment)), fold_matches=details,
                fold_compatible=all(row['cost'] <= FOLD_POLICY['compatibleCost'] for row in details),
                fold_corrections=[row['index'] for row in details if row['corrected']])
