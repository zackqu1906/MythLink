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
from .stroke_joined_components import component_rules, component_registry

FAMILIES = {rule['family']: tuple(rule['codes']) for rule in component_rules()['rules']}
LEGACY_POLICY = dict(version=1, maxSplits=1, maxFamilies=2, distanceLimits={
    'fan': .04, 'de': .04, 'le-zi': .085, 'yi-pie-dian': .04},
    controlRatio=2., controlFloor=.02, splitCost=.65, distanceScale=.04,
    reservedSlots=2, normalFirst=True)
POLICY = dict(LEGACY_POLICY, version=2, maxSplits=3, maxPaths=24,
    distanceLimits=dict(LEGACY_POLICY['distanceLimits'], walk=.09), walkControlFloor=.045)


def sample_family(sample):
    target = sample['task'].get('target')
    return ('le-zi' if target in ('le', 'zi') else target) if sample['task']['kind'] == 'joined' else 'single'


class JoinedStrokeModel:
    def __init__(self, payload):
        if payload.get('schema') != 'ring-joined-stroke-model' or payload.get('version') not in (1, 2):
            raise ValueError('不支持的连笔模型')
        self.source = deepcopy(payload['source'])
        if payload['policy'] != POLICY and payload['policy'] != LEGACY_POLICY:
            raise ValueError('连笔模型参数与程序版本不匹配')
        self.policy = deepcopy(POLICY)
        templates = payload['templates'] + payload.get('referenceTemplates', [])
        self.labels = [t['family'] for t in templates]
        self.bank = np.asarray([t['features'] for t in templates], dtype=np.float32)
        required = set(FAMILIES)-{'walk'} | {'single'}
        if (self.bank.shape != (len(self.labels), 4, 32) or not len(self.labels)
                or not np.isfinite(self.bank).all() or not required <= set(self.labels)
                or set(self.labels)-set(FAMILIES)-{'single'}):
            raise ValueError('连笔模板不完整或无效')
        self.reference_only = {family for family in set(self.labels) if all(
            t.get('source') == 'reference-component' for t in templates if t['family'] == family)}
        self.component_source = payload.get('componentSource', {})
        self.positions = payload['positions']
        self.entries_by_pair = defaultdict(list)
        self.schedule_cache = {}
        for char, variants in self.positions.items():
            for full, annotations in variants.items():
                for row in annotations:
                    width = row.get('length', 2)
                    self.entries_by_pair[(row['family'], row['start'], full[row['start']:row['start']+width])].append((char, full))

    @classmethod
    def load(cls, path):
        with gzip.open(Path(path), 'rt', encoding='utf-8') as stream:
            payload = json.load(stream)
        # Component coverage must update independently of a wearer's old model.
        # Loading the existing 70-sample model now also covers 斤 and 辶.
        registry = component_registry()
        payload.update(positions=registry['positions'], referenceTemplates=registry['referenceTemplates'],
                       componentSource=registry['source'])
        return cls(payload)

    def snapshot(self):
        return dict(source=self.source, policy=self.policy, templates=len(self.labels),
                    annotatedCharacters=len(self.positions), componentSource=self.component_source,
                    referenceOnlyFamilies=sorted(self.reference_only))

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
            score = scores.get(family, math.inf)
            floor = POLICY['walkControlFloor'] if family == 'walk' else POLICY['controlFloor']
            if score <= POLICY['distanceLimits'][family] and score <= max(floor, POLICY['controlRatio']*control):
                options.append(dict(family=family, codes=list(codes), distance=score,
                    controlDistance=control, cost=POLICY['splitCost']+score/POLICY['distanceScale'],
                    source='reference-component' if family in self.reference_only else 'collected'))
        return sorted(options, key=lambda row: row['distance'])[:POLICY['maxFamilies']]

    def permits(self, dictionary, char, prefix, family, start, code, mapping=None, reference_code=None):
        for full, annotations in self.positions.get(char, {}).items():
            def matches(row):
                if row['family'] != family or row.get('length', 2) != len(code):
                    return False
                if mapping is not None:
                    return (reference_code == full and tuple(mapping[start:start+len(code)]) ==
                            tuple(range(row['start'], row['start']+len(code))) and
                            full[row['start']:row['start']+len(code)] == code)
                return full.startswith(prefix) and row['start'] == start and full[start:start+len(code)] == code
            if any(matches(row) for row in annotations):
                # The active dictionary may be replaced or have different variants.
                if any(entry[1] == full for entry in dictionary.by_character.get(char, [])):
                    return True
        return False

    def candidate_view(self, dictionary, family, start, code, allowed):
        # Scan the verified component positions before running fuzzy matching.
        # A joined hypothesis never rescans the entire 100k-character dictionary.
        entries = self.candidate_entries(dictionary, family, start, code, allowed)
        return self.view_for_entries(dictionary, entries)

    def candidate_entries(self, dictionary, family, start, code, allowed):
        decoder = getattr(dictionary, 'component_decoder', None) if getattr(dictionary, 'components_enabled', False) else None
        key = (id(decoder), family, start, code)
        if key not in self.schedule_cache:
            found = set(self.entries_by_pair.get((family, start, code), []))
            if decoder is not None:
                for (candidate_family, reference_start, candidate_code), entries in self.entries_by_pair.items():
                    if candidate_family != family or candidate_code != code:
                        continue
                    for char, full in entries:
                        graph = decoder.graphs.get(char)
                        if graph is not None and graph.code == full and start in self._span_starts(graph, reference_start, len(code)):
                            found.add((char, full))
            self.schedule_cache[key] = frozenset(found)
        entries = set()
        for char, full in self.schedule_cache[key]:
            if allowed is not None and char not in allowed:
                continue
            for entry in dictionary.by_character.get(char, []):
                if entry[1] == full:
                    entries.add((char, full))
        return frozenset(entries)

    @staticmethod
    def _span_starts(graph, reference_start, width):
        """Use existing component schedules without enumerating permutations."""
        starts = set()
        active = {graph.start}
        for depth in range(len(graph.code)):
            following = set()
            for state in active:
                following.update(target for target, _, _, _ in graph.frontier(state))
                probe = {state}
                for expected in range(reference_start,reference_start+width):
                    probe = {target for current in probe for target,index,_,_ in graph.frontier(current) if index == expected}
                if probe:
                    starts.add(depth)
            active = following
            if not active:
                break
        return starts

    @staticmethod
    def view_for_entries(dictionary, entries):
        view = copy(dictionary)
        view.by_initial = defaultdict(list)
        for char, full in sorted(entries):
            for entry in dictionary.by_character.get(char, []):
                if entry[1] == full:
                    view.by_initial[full[0]].append(entry)
        chars = {entry[0] for rows in view.by_initial.values() for entry in rows}
        view._left_dot_initial = [entry for entry in dictionary._left_dot_initial if entry[0] in chars]
        return view


def component_paths(dictionary, evidence, model, allowed=None):
    """Bounded contact-to-component paths with explicit logical offsets.

    All groups in a path must belong to the same dictionary variant. Keeping
    the eligible-entry intersection prevents substring-based combinations.
    """
    paths = [dict(expanded=[], mapping=[], groups=[], eligible=None, cost=0.)]
    entry_cache = {}
    for physical, stroke in enumerate(evidence):
        next_paths = []
        for path in paths:
            next_paths.append(dict(path, expanded=path['expanded']+[stroke], mapping=path['mapping']+[physical]))
            if stroke.get('manual', True) or len(path['groups']) >= POLICY['maxSplits']:
                continue
            start = len(path['expanded'])
            for option in stroke.get('joined_options', []):
                for code in option['codes']:
                    if code not in FAMILIES.get(option['family'], ()):
                        continue
                    key = (option['family'], start, code)
                    if key not in entry_cache:
                        entry_cache[key] = model.candidate_entries(dictionary, *key, allowed)
                    entries = entry_cache[key]
                    if path['eligible'] is not None:
                        entries = entries & path['eligible']
                    if not entries:
                        continue
                    group = dict(physical=physical, start=start, code=code, family=option['family'],
                                 distance=option['distance'], cost=option['cost'], source=option.get('source', 'collected'))
                    next_paths.append(dict(expanded=path['expanded']+[stroke_evidence(c) for c in code],
                        mapping=path['mapping']+[physical]*len(code), groups=path['groups']+[group],
                        eligible=entries, cost=path['cost']+option['cost']))
        # The ordinary path is always retained; no global expansion is written
        # back into the raw ink. The beam is over interpretation paths only.
        paths = sorted(next_paths, key=lambda p: (p['cost'], len(p['groups'])))[:POLICY['maxPaths']]
    return [path for path in paths if path['groups']]


def merge_joined_candidates(dictionary, evidence, normal_chars, normal_rows, model, *,
                            enabled=True, fuzzy=True, predictor=None, context='', allowed=None, policy=None, limit=30):
    """Combine verified component paths while preserving ordinary choices."""
    if not enabled or model is None or not any(s.get('joined_options') for s in evidence):
        return list(normal_chars), list(normal_rows)
    alternatives = {}
    normal_by_char = {row['char']: row for row in normal_rows}
    family_best = {}
    for path in component_paths(dictionary, evidence, model, allowed):
        expanded, groups = path['expanded'], path['groups']
        view = model.view_for_entries(dictionary, path['eligible'])
        if fuzzy:
            rows = view.fuzzy_candidates(expanded, limit=limit, predictor=predictor, context=context,
                allowed_characters=allowed, **({'policy': policy} if policy is not None else {}))
        else:
            prefix = ''.join(s['category'] for s in expanded)
            rows = [dict(char=c, code=prefix, cost=0., corrections=[], exact_match=False)
                    for c in view.candidates(prefix, limit, allowed_characters=allowed)]
        for row in rows:
            prefix = row['code'][:len(expanded)]
            mapping = row.get('observed_to_expected')
            if not all(model.permits(dictionary, row['char'], prefix, g['family'], g['start'], g['code'],
                    mapping=mapping, reference_code=row.get('reference_code')) for g in groups):
                continue
            if row['char'] in normal_by_char and normal_by_char[row['char']].get('exact_match'):
                continue
            glyph_fuzzy = sorted(set(row.get('corrections', [])) | {
                i for group in groups for i in range(group['start'],group['start']+len(group['code']))})
            physical_corrections = sorted({path['mapping'][i] for i in row.get('corrections', []) if i < len(path['mapping'])})
            score = row.get('final_score', row.get('score', .35*math.log1p(dictionary.ranks.get(row['char'], 100000)))) + path['cost']
            label = f"连笔 1→{len(groups[0]['code'])}" if len(groups)==1 else f'连笔 {len(groups)}处'
            match = dict(row, match_type='joined_stroke', joined_family=groups[0]['family'],
                joined_contacts=[g['physical'] for g in groups], joined_code=groups[0]['code'],
                joined_groups=groups, joined_label=label, logical_to_physical=path['mapping'],
                logical_entered=len(expanded), logical_prefix=prefix, glyph_fuzzy=glyph_fuzzy,
                corrections=physical_corrections, replacement_corrections=[],
                cost=row.get('cost', 0.)+path['cost'], final_score=score, joined_distance=groups[0]['distance'])
            complete = row.get('is_complete', False) or any(entry[1] == prefix for entry in dictionary.by_character.get(row['char'], []))
            key = (not complete, score)
            old = alternatives.get(row['char'])
            if old is None or key < old['_joined_key']:
                alternatives[row['char']] = dict(match, _joined_key=key)
            # A shared shape family can encode distinct component forms (pp/hp).
            # Do not let the more frequent 厅 consume the only slot for 斤.
            family_key = tuple((g['family'], g['code']) for g in groups)
            if family_key not in family_best or key < family_best[family_key][0]:
                family_best[family_key] = (key, row['char'])
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
