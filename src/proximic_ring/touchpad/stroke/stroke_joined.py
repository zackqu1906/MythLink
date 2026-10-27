"""Opt-in joined-contact hypotheses for the standalone input trial only."""
from __future__ import annotations

from copy import copy, deepcopy
from collections import defaultdict
import gzip
import json
import math
from pathlib import Path

import numpy as np

from .stroke_dtw import sequence, distances
from .stroke_fuzzy import stroke_evidence

FAMILIES = {'fan': ('pp', 'hp'), 'de': ('ps',), 'le-zi': ('zs', 'zz'), 'yi-pie-dian': ('pn',)}
POLICY = dict(version=1, maxSplits=1, maxFamilies=2, distanceLimits={
    'fan': .04, 'de': .04, 'le-zi': .085, 'yi-pie-dian': .04},
    controlRatio=2., controlFloor=.02, splitCost=.65, distanceScale=.04,
    reservedSlots=2, normalFirst=True)


def sample_family(sample):
    target = sample['task'].get('target')
    return ('le-zi' if target in ('le', 'zi') else target) if sample['task']['kind'] == 'joined' else 'single'


class JoinedStrokeModel:
    def __init__(self, payload):
        if payload.get('schema') != 'ring-joined-stroke-model' or payload.get('version') != 1:
            raise ValueError('不支持的连笔模型')
        self.source = deepcopy(payload['source'])
        self.policy = deepcopy(payload['policy'])
        if self.policy != POLICY:
            raise ValueError('连笔模型参数与程序版本不匹配')
        self.labels = [t['family'] for t in payload['templates']]
        self.bank = np.asarray([t['features'] for t in payload['templates']], dtype=np.float32)
        if (self.bank.shape != (len(self.labels), 4, 32) or not len(self.labels)
                or not np.isfinite(self.bank).all() or set(self.labels) != set(FAMILIES) | {'single'}):
            raise ValueError('连笔模板不完整或无效')
        self.positions = payload['positions']
        self.entries_by_pair = defaultdict(list)
        for char, variants in self.positions.items():
            for full, annotations in variants.items():
                for row in annotations:
                    self.entries_by_pair[(row['family'], row['start'], full[row['start']:row['start']+2])].append((char, full))

    @classmethod
    def load(cls, path):
        with gzip.open(Path(path), 'rt', encoding='utf-8') as stream:
            return cls(json.load(stream))

    def snapshot(self):
        return dict(source=self.source, policy=self.policy, templates=len(self.labels),
                    annotatedCharacters=len(self.positions))

    def recognize(self, points):
        query = sequence(points)
        if query is None:
            return []
        values = distances(query, self.bank)
        scores = {label: min(float(v) for v, family in zip(values, self.labels) if family == label)
                  for label in set(self.labels)}
        control = scores['single']
        options = []
        for family, codes in FAMILIES.items():
            score = scores[family]
            if score <= POLICY['distanceLimits'][family] and score <= max(POLICY['controlFloor'], POLICY['controlRatio']*control):
                options.append(dict(family=family, codes=list(codes), distance=score,
                    controlDistance=control, cost=POLICY['splitCost']+score/POLICY['distanceScale']))
        return sorted(options, key=lambda row: row['distance'])[:POLICY['maxFamilies']]

    def permits(self, dictionary, char, prefix, family, start, code):
        for full, annotations in self.positions.get(char, {}).items():
            if full.startswith(prefix) and any(
                    row['family'] == family and row['start'] == start and full[start:start+2] == code
                    for row in annotations):
                # The active dictionary may be replaced or have different variants.
                if any(entry[1] == full for entry in dictionary.by_character.get(char, [])):
                    return True
        return False

    def candidate_view(self, dictionary, family, start, code, allowed):
        # Scan the verified component positions before running fuzzy matching.
        # A joined hypothesis never rescans the entire 100k-character dictionary.
        view = copy(dictionary)
        view.by_initial = defaultdict(list)
        for char, full in self.entries_by_pair.get((family, start, code), []):
            if allowed is not None and char not in allowed:
                continue
            for entry in dictionary.by_character.get(char, []):
                if entry[1] == full:
                    view.by_initial[full[0]].append(entry)
        chars = {entry[0] for rows in view.by_initial.values() for entry in rows}
        view._left_dot_initial = [entry for entry in dictionary._left_dot_initial if entry[0] in chars]
        return view


def merge_joined_candidates(dictionary, evidence, normal_chars, normal_rows, model, *,
                            enabled=True, fuzzy=True, predictor=None, context='', allowed=None, policy=None, limit=30):
    """One contact expands as a unit; normal alternatives stay in contention.

    Keep the original first candidate and reserve up to two places for the best
    distinct families. Other normal candidates retain their relative order.
    """
    if not enabled or model is None or not any(s.get('joined_options') for s in evidence):
        return list(normal_chars), list(normal_rows)
    alternatives = {}
    normal_by_char = {row['char']: row for row in normal_rows}
    family_best = {}
    for physical, stroke in enumerate(evidence):
        if stroke.get('manual', True):
            continue
        for option in stroke.get('joined_options', []):
            for code in option['codes']:
                view = model.candidate_view(dictionary, option['family'], physical, code, allowed)
                if not view.by_initial:
                    continue
                expanded = list(evidence[:physical]) + [stroke_evidence(c) for c in code] + list(evidence[physical+1:])
                if fuzzy:
                    rows = view.fuzzy_candidates(expanded, limit=limit, predictor=predictor, context=context,
                        allowed_characters=allowed, **({'policy': policy} if policy is not None else {}))
                else:
                    prefix = ''.join(s['category'] for s in expanded)
                    rows = [dict(char=c, code=prefix, cost=0., corrections=[], exact_match=False)
                            for c in view.candidates(prefix, limit, allowed_characters=allowed)]
                logical_to_physical = list(range(physical))+[physical, physical]+list(range(physical+1, len(evidence)))
                for row in rows:
                    prefix = row['code'][:len(expanded)]
                    if not model.permits(dictionary, row['char'], prefix, option['family'], physical, code):
                        continue
                    # Never hide a literal interpretation of the same character.
                    if row['char'] in normal_by_char and normal_by_char[row['char']].get('exact_match'):
                        continue
                    glyph_fuzzy = sorted(set(row.get('corrections', [])) | {physical, physical+1})
                    physical_corrections = sorted({logical_to_physical[i] for i in row.get('corrections', []) if i < len(logical_to_physical)})
                    score = row.get('final_score', row.get('score', .35*math.log1p(dictionary.ranks.get(row['char'], 100000)))) + option['cost']
                    match = dict(row, match_type='joined_stroke', joined_family=option['family'],
                        joined_contacts=[physical], joined_code=code, logical_entered=len(expanded),
                        logical_prefix=prefix, glyph_fuzzy=glyph_fuzzy, corrections=physical_corrections,
                        replacement_corrections=[], cost=row.get('cost', 0.)+option['cost'], final_score=score,
                        joined_distance=option['distance'])
                    old = alternatives.get(row['char'])
                    if old is None or score < old['final_score']:
                        alternatives[row['char']] = match
                    key = (not any(entry[1] == prefix for entry in dictionary.by_character.get(row['char'], [])), score)
                    if option['family'] not in family_best or key < family_best[option['family']][0]:
                        family_best[option['family']] = (key, row['char'])
    if not alternatives:
        return list(normal_chars), list(normal_rows)
    preferred = []
    for _, char in sorted(family_best.values()):
        if char not in preferred:
            preferred.append(char)
    preferred = preferred[:POLICY['reservedSlots']]
    first = normal_chars[:1]
    chars = first + [c for c in preferred if c not in first]
    chars += [c for c in normal_chars[1:] if c not in chars]
    chars += [r['char'] for r in sorted(alternatives.values(), key=lambda r: r['final_score']) if r['char'] not in chars]
    chars = chars[:limit]
    matches = []
    for char in chars:
        # Only the promoted alternative changes a normal character's glyph count.
        if char in alternatives and (char in preferred or char not in normal_by_char):
            match = alternatives[char]
            if char in normal_by_char:
                match = dict(match, single_interpretation=normal_by_char[char])
            matches.append(match)
        elif char in normal_by_char:
            matches.append(normal_by_char[char])
        elif char in alternatives:
            matches.append(alternatives[char])
    return chars, matches
