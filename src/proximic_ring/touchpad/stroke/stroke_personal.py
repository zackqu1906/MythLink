"""Bounded, reversible writer adaptation from acknowledged character selections.

Dictionary labels supervise five categories only. A model prediction never creates
a training sample. Distances remain heuristic costs, not probabilities.
"""
from __future__ import annotations

import hashlib
import json
import math
import uuid

import numpy as np

from .stroke_dtw import sequence, distances

CATEGORIES = 'hspnz'
NAMES = dict(zip(CATEGORIES, ('横', '竖', '撇', '点／捺', '折')))
MAX_COMMITS = 64
PER_CATEGORY = 16
MAX_STROKES = 64


def profile_key(name):
    return hashlib.sha256(name.strip().encode('utf-8')).hexdigest()[:24]


def qt_text_index(text, offset):
    """Translate Qt's UTF-16 offsets before indexing Python Unicode strings."""
    return len(text.encode('utf-16-le')[:max(0, offset)*2].decode('utf-16-le', errors='ignore'))


def capture_ink(points):
    """Retain direction and proportions; never learn a display/canonical glyph."""
    try:
        if not 2 <= len(points) <= 4096:
            return None
        raw = np.asarray(points, dtype=float)
        if raw.shape != (len(points), 2) or not np.isfinite(raw).all():
            return None
        value = sequence(points)
        return value[:2].T.tolist() if value is not None else None
    except (ValueError, TypeError, OverflowError, IndexError):
        return None


def confirmed_samples(dictionary, character, evidence):
    """Use only physically plausible variants; learn their per-position consensus.

    No edit-distance alignment: extra/missing/reordered strokes are not inferred.
    Base-model gates prevent the adaptive model from certifying its own predictions.
    """
    if not evidence or len(evidence) > MAX_STROKES:
        return []
    plausible = []
    for _, code, *_ in dictionary.by_character.get(character, []):
        if len(code) < len(evidence):
            continue
        prefix = code[:len(evidence)]
        valid = True
        for label, item in zip(prefix, evidence):
            if item.get('manual', True):
                valid = label == item['category']
            else:
                recognition = item.get('recognition', {})
                scores = {row['category']: row['distance'] for row in
                          recognition.get('base_category_scores', recognition.get('category_scores', []))
                          if row.get('category') in CATEGORIES and
                          isinstance(row.get('distance'), (int, float)) and
                          math.isfinite(row['distance']) and row['distance'] >= 0}
                best = min(scores.values(), default=math.inf)
                valid = best <= .08 and scores.get(label, math.inf) <= min(.12, best + .08)
            if not valid:
                break
        if valid:
            plausible.append(prefix)
    samples = []
    for index, item in enumerate(evidence):
        labels = {code[index] for code in plausible}
        if item.get('manual', True) or len(labels) != 1 or not item.get('ink'):
            continue
        # A left-falling dot is an accepted input convention, not evidence that
        # all future left-falling strokes should become canonical dots.
        label = next(iter(labels))
        if label == 'n' and item['category'] in 'ps' and all(
                index in dictionary._left_dots.get((character, code), ())
                for _, code, *_ in dictionary.by_character.get(character, [])
                if code[:len(evidence)] in plausible):
            continue
        samples.append(dict(category=label, trace=item['ink']))
    return samples


class PersonalStrokeModel:
    def __init__(self, payload=None):
        self.commits = []
        self.error = ''
        self._bank = None
        self._labels = []
        self._supports = {}
        if payload:
            try:
                data = json.loads(payload) if isinstance(payload, str) else payload
                if data['version'] != 1 or len(data['commits']) > MAX_COMMITS:
                    raise ValueError('unsupported or oversized profile')
                ids = set()
                for commit in data['commits']:
                    if not isinstance(commit['id'], str) or commit['id'] in ids:
                        raise ValueError('duplicate commit')
                    ids.add(commit['id'])
                    if not 1 <= len(commit['samples']) <= MAX_STROKES:
                        raise ValueError('invalid sample count')
                    samples = []
                    for sample in commit['samples']:
                        trace = np.asarray(sample['trace'], dtype=float)
                        if (sample['category'] not in CATEGORIES or len(sample['category']) != 1
                                or trace.shape != (32, 2) or not np.isfinite(trace).all()
                                or np.abs(trace).max() > 1.001 or sequence(trace.tolist()) is None):
                            raise ValueError('invalid sample')
                        samples.append(dict(category=sample['category'], trace=trace.tolist()))
                    self.commits.append(dict(id=commit['id'], samples=samples))
            except (ValueError, TypeError, KeyError, IndexError, OverflowError):
                self.commits = []
                self.error = '个人档案损坏，已使用通用识别；清空档案后可重新学习'

    def dumps(self):
        return json.dumps(dict(version=1, commits=self.commits), separators=(',', ':'), allow_nan=False)

    def snapshot(self):
        self._build()
        return dict(version=1, confirmations=len(self.commits), samples=len(self._labels),
                    support=dict(self._supports), error=self.error,
                    fingerprint=hashlib.sha256(self.dumps().encode('utf-8')).hexdigest()[:16])

    def learn(self, dictionary, character, evidence):
        if self.error:
            return None
        samples = confirmed_samples(dictionary, character, evidence)
        if not samples:
            return None
        token = uuid.uuid4().hex
        self.commits.append(dict(id=token, samples=samples))
        self.commits = self.commits[-MAX_COMMITS:]
        self._bank = None
        return token

    def retract(self, token=None):
        if not self.commits:
            return False
        token = token or self.commits[-1]['id']
        before = len(self.commits)
        self.commits = [row for row in self.commits if row['id'] != token]
        self._bank = None
        return len(self.commits) != before

    def clear(self):
        self.commits = []
        self.error = ''
        self._bank = None

    def _build(self):
        if self._bank is not None:
            return
        selected = {label: [] for label in CATEGORIES}
        supports = {label: set() for label in CATEGORIES}
        for commit in reversed(self.commits):
            per_commit = {label: 0 for label in CATEGORIES}
            for sample in commit['samples']:
                label = sample['category']
                if len(selected[label]) < PER_CATEGORY and per_commit[label] < 4:
                    selected[label].append(sample['trace'])
                    supports[label].add(commit['id'])
                    per_commit[label] += 1
        self._labels, bank = [], []
        self._supports = {label: len(ids) for label, ids in supports.items()}
        for label, traces in selected.items():
            for trace in traces:
                self._labels.append(label)
                bank.append(sequence(trace))
        self._bank = np.stack(bank) if bank else np.empty((0, 4, 32), dtype=np.float32)

    def adapt(self, points, result):
        self._build()
        if result is None or not self._labels:
            return result
        query = sequence(points)
        if query is None:
            return result
        personal = {}
        for label, distance in zip(self._labels, distances(query, self._bank)):
            personal[label] = min(personal.get(label, math.inf), float(distance))
        base = result.get('category_scores', [])
        scores, weights = [], {}
        for row in base:
            label, value = row['category'], row['distance']
            count = self._supports.get(label, 0)
            weight = min(.65, count / (count + 8)) if count >= 2 else 0.
            # Local support only. Distant ink cannot activate a writing habit.
            if personal.get(label, math.inf) > .08:
                weight = 0.
            adjusted = min(value, (1-weight)*value + weight*personal.get(label, value))
            scores.append(dict(row, distance=adjusted))
            weights[label] = weight
        if not scores or not any(weights.values()):
            return result
        scores.sort(key=lambda row: row['distance'])
        winner = scores[0]
        changed = winner['category'] != result['category']
        shape = NAMES[winner['category']] if changed else result['shape']
        shapes = list(result.get('shape_scores', []))
        if changed:
            shapes.insert(0, dict(category=winner['category'], shape=shape, distance=winner['distance']))
        return dict(result, category=winner['category'], shape=shape, distance=winner['distance'],
                    base_category_scores=[dict(row) for row in base], category_scores=scores,
                    category_alternatives=scores[:2], shape_scores=shapes,
                    personal_adaptation=dict(weights=weights, changed=changed, confirmations=len(self.commits)))


class TextLearningHistory:
    """Conservatively retract labels when the locally observed text is edited.

Text exists in RAM only. Removing/replacing a learned prefix invalidates it;
arbitrary cursor edits may retract extra samples rather than keep a wrong label.
"""
    def __init__(self):
        self.entries = []

    def track(self, token, text, cursor):
        if token:
            self.entries.append((token, text[:cursor]))
            self.entries = self.entries[-MAX_COMMITS:]

    def reconcile(self, model, text):
        changed = False
        retained = []
        for token, prefix in self.entries:
            if text.startswith(prefix):
                retained.append((token, prefix))
            else:
                changed = model.retract(token) or changed
        self.entries = retained
        return changed
