"""Bounded stroke substitutions; costs are heuristics, not probabilities."""
import math
from dataclasses import dataclass
from .stroke_left_dots import LEFT_DOT_COST

CATEGORIES = 'hspnz'
CONFUSABLE = frozenset('spn')
# Weak hooks / short final rises can disappear into the preceding long stroke.
# These are shape-gated alternatives, never a general permission to replace z.
SHORT_TAIL_RESCUES = {
    'h': ('横钩',),
    's': ('弯钩', '竖提'),
    'p': ('撇点', '撇折'),
    'n': ('斜钩', '卧钩'),
}


@dataclass(frozen=True)
class FuzzyPolicy:
    """0–100 tolerance; 50 exactly preserves the established default rules."""
    level: int = 50

    def __post_init__(self):
        if type(self.level) is not int or not 0 <= self.level <= 100:
            raise ValueError('Fuzzy level must be an integer from 0 to 100')

    @property
    def scale(self): return .5 + self.level / 100

    @property
    def max_rescues(self): return 2 if self.level >= 75 else 1

    @property
    def left_dot_cost(self): return round(max(.05, LEFT_DOT_COST + .004*(50-self.level)), 4)

    def snapshot(self):
        return dict(version=1, level=self.level, bestDistanceLimit=.08,
                    nearAbsoluteGap=round(.004*self.scale, 6),
                    nearRelativeGap=round(.5*self.scale, 6),
                    rescueGapLimit=round(.08*self.scale, 6),
                    maxRescues=self.max_rescues, leftDotCost=self.left_dot_cost)


DEFAULT_FUZZY_POLICY = FuzzyPolicy()


# Prototype thresholds in raw SDK displacement units (not pixels or mm).
# Keep the increment bounded, and leave the per-word rescue budget at baseline.
ADAPTIVE_SETTINGS = dict(version=1, shortLength=60., veryShortLength=15.,
                         fastSpeed=800., veryFastSpeed=1600., maxBoost=30)


def adaptive_policy(policy, metrics):
    """Short and fast ink independently add up to 15 levels each."""
    detail = dict(ADAPTIVE_SETTINGS, baseLevel=policy.level, effectiveLevel=policy.level,
                  boost=0, shortFactor=0., fastFactor=0., applied=False)
    if not metrics or metrics.get('timing') != 'confirmed_contact_frames' or metrics.get('units') != 'sdk':
        return policy, detail
    length, duration = metrics.get('length'), metrics.get('duration_ms')
    if any(not isinstance(v, (int, float)) or isinstance(v, bool)
           or not math.isfinite(v) or v <= 0 for v in (length, duration)):
        return policy, detail
    speed = length*1000/duration
    short = max(0., min(1., (ADAPTIVE_SETTINGS['shortLength']-length)/
                           (ADAPTIVE_SETTINGS['shortLength']-ADAPTIVE_SETTINGS['veryShortLength'])))
    fast = max(0., min(1., (speed-ADAPTIVE_SETTINGS['fastSpeed'])/
                          (ADAPTIVE_SETTINGS['veryFastSpeed']-ADAPTIVE_SETTINGS['fastSpeed'])))
    effective = FuzzyPolicy(min(100, policy.level+round(ADAPTIVE_SETTINGS['maxBoost']*(short+fast)/2)))
    detail.update(effectiveLevel=effective.level, boost=effective.level-policy.level,
                  shortFactor=round(short, 4), fastFactor=round(fast, 4), applied=True)
    return effective, detail


def retune_evidence(evidence, policy):
    """Rebuild automatic alternatives from retained scores, preserving manual ink."""
    return [dict(stroke_evidence(stroke['category'], stroke['recognition'], policy=policy,
                            metrics=stroke.get('kinematics')), **({'ink': stroke['ink']} if 'ink' in stroke else {}))
            if not stroke.get('manual', True) and 'recognition' in stroke else stroke
            for stroke in evidence]


def _shape_distances(result):
    values = {}
    for item in result.get('shape_scores', []) + result.get('category_scores', []):
        distance = item.get('distance')
        shape = item.get('shape')
        if (shape and item.get('category') == 'z' and isinstance(distance, (int, float))
                and math.isfinite(distance) and distance >= 0):
            values[shape] = min(values.get(shape, math.inf), distance)
    return values


def stroke_evidence(category, result=None, *, policy=DEFAULT_FUZZY_POLICY, metrics=None):
    """Keep close classes and one rescue; manual input remains locked."""
    evidence = dict(category=category, costs={category: 0.}, rescues=[], ambiguous=False,
                    manual=not bool(result))
    if not result:
        return evidence
    policy, adaptation = adaptive_policy(policy, metrics)
    if metrics is not None:
        evidence['kinematics'] = dict(metrics)
        evidence['adaptation'] = adaptation
        evidence['effective_policy'] = policy.snapshot()
    evidence['recognition'] = dict(category=category, shape=result.get('shape'),
        category_scores=[dict(item) for item in result.get('category_scores', [])],
        shape_scores=[dict(item) for item in result.get('shape_scores', [])])
    if result.get('joined_options'):
        from copy import deepcopy
        evidence['joined_options'] = deepcopy(result['joined_options'])
        evidence['recognition']['joined_options'] = deepcopy(result['joined_options'])
    if 'base_category_scores' in result:
        evidence['recognition']['base_category_scores'] = [dict(item) for item in result['base_category_scores']]
    scores = {}
    for item in result.get('category_scores', []):
        distance = item.get('distance')
        if (item.get('category') in CATEGORIES and isinstance(distance, (int, float))
                and math.isfinite(distance) and distance >= 0):
            scores[item['category']] = distance
    if category not in scores:
        return evidence
    best = scores[category]
    # Poor matches must not turn into an unrestricted wildcard.
    if best > .08:
        return evidence
    shape_distances = _shape_distances(result)
    for alternative, distance in scores.items():
        if alternative == category:
            continue
        gap = max(0., distance-best)
        if gap <= .004*policy.scale + .5*policy.scale*best:
            evidence['costs'][alternative] = gap/(.01+best)
            evidence['ambiguous'] = True
        else:
            rescue_gap = None
            if category in CONFUSABLE and alternative in CONFUSABLE:
                rescue_gap = gap
            elif alternative == 'z' and category in SHORT_TAIL_RESCUES:
                # The nearest z template may be unrelated. Use only the named
                # compatible shapes' own distances, not the class minimum.
                paired_distance = min((shape_distances[shape] for shape in SHORT_TAIL_RESCUES[category]
                                       if shape in shape_distances), default=None)
                if paired_distance is not None:
                    rescue_gap = max(0., paired_distance-best)
            elif category == 'z' and result.get('shape') in SHORT_TAIL_RESCUES.get(alternative, ()):
                rescue_gap = gap
            if rescue_gap is not None and rescue_gap <= .08*policy.scale:
                evidence['costs'][alternative] = 1. + min(2., rescue_gap/(.04+best))
                evidence['rescues'].append(alternative)
    return evidence


def match_prefix(code, evidence, *, left_dots=(), policy=DEFAULT_FUZZY_POLICY):
    if len(code) < len(evidence):
        return None
    cost, rescues, corrections, left_dot_corrections = 0., 0, [], []
    for index, stroke in enumerate(evidence):
        letter = code[index]
        glyph_alternative = (index in left_dots and letter == 'n'
                             and stroke['category'] in 'ps' and not stroke.get('manual', True))
        if glyph_alternative:
            # Only enriched ink has a stroke-specific policy. Legacy callers
            # continue to use the supplied baseline policy.
            dot_cost = stroke.get('effective_policy', {}).get('leftDotCost', policy.left_dot_cost)
            cost += min(dot_cost, stroke['costs'].get(letter, math.inf))
            left_dot_corrections.append(index)
        elif letter not in stroke['costs']:
            return None
        else:
            cost += stroke['costs'][letter]
        rescues += not glyph_alternative and letter in stroke['rescues']
        if rescues > policy.max_rescues:
            return None
        if letter != stroke['category']:
            corrections.append(index)
    return dict(cost=cost, rescues=rescues, corrections=corrections,
                left_dot_corrections=left_dot_corrections)


def replacement_options(stroke, *, policy=DEFAULT_FUZZY_POLICY):
    """At most two nearby trace classes for the empty-candidate fallback.

    Unlike ordinary rescues, these need not belong to a named confusion pair.
    Distance gates still apply even if the dictionary or context wants a letter.
    """
    if stroke.get('manual', True):
        return {}
    scores = {}
    for item in stroke.get('recognition', {}).get('category_scores', []):
        category, distance = item.get('category'), item.get('distance')
        if (isinstance(category, str) and category in CATEGORIES and isinstance(distance, (int, float))
                and not isinstance(distance, bool) and math.isfinite(distance) and distance >= 0):
            scores[category] = min(scores.get(category, math.inf), distance)
    observed = stroke['category']
    best = scores.get(observed)
    if best is None or best > .08:
        return {}
    # Use the per-stroke adaptive tolerance when available, as ordinary fuzzy
    # matching does, while keeping the word budget at exactly one substitution.
    scale = FuzzyPolicy(stroke.get('effective_policy', {}).get('level', policy.level)).scale
    nearest = sorted((distance, category) for category, distance in scores.items() if category != observed)[:2]
    return {category: 1. + max(0., distance-best)/(.04+best)
            for distance, category in nearest
            if distance <= .12 and max(0., distance-best) <= .08*scale}
