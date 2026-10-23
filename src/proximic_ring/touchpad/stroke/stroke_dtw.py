"""32-point direction-preserving DTW on the PC, after confirmed stroke completion."""
import gzip
import hashlib
from importlib.resources import files
import json

import numpy as np


def sequence(points):
    from .stroke_input import _normalize
    xy = _normalize(points, 32)
    if xy is None:
        return None
    xy = np.asarray(xy, dtype=np.float32)
    if not np.isfinite(xy).all():
        return None
    delta = np.diff(xy, axis=0, prepend=xy[:1])
    direction = delta/np.maximum(np.linalg.norm(delta, axis=1, keepdims=True), 1e-6)
    return np.concatenate([xy, direction], axis=1).T


def distances(query, templates, weight=.15, radius=6):
    previous = np.full((len(templates), 33), np.inf, dtype=np.float32)
    previous[:, 0] = 0
    for i in range(1, 33):
        current = np.full_like(previous, np.inf)
        for j in range(max(1, i-radius), min(32, i+radius)+1):
            diff = query[:, i-1]-templates[:, :, j-1]
            cost = (diff[:, :2]**2).sum(axis=1)+weight*(diff[:, 2:]**2).sum(axis=1)
            current[:, j] = cost+np.minimum(np.minimum(previous[:, j], current[:, j-1]), previous[:, j-1])
        previous = current
    return previous[:, -1]/32


class DTWRecognizer:
    def __init__(self, templates, personal=None, *, weight=.15):
        if personal is None:
            path = files('proximic_ring').joinpath('assets/stroke-personal.json.gz')
            if path.is_file():
                with gzip.open(path, 'rt', encoding='utf-8') as stream:
                    payload = json.load(stream)
                personal = payload['templates']
                self.profile = payload['source_sha256']
            else:
                personal = []
                self.profile = 'none'
        else:
            self.profile = hashlib.sha256(json.dumps(personal, sort_keys=True).encode()).hexdigest()
        self.weight = weight
        self.templates = list(templates)+list(personal)
        traces = [sequence(t.get('raw_trace', t['trace'])) for t in self.templates]
        if not traces or any(t is None for t in traces):
            raise ValueError('DTW templates must contain valid moving paths')
        self.bank = np.stack(traces)

    def recognize(self, points):
        query = sequence(points)
        if query is None:
            return None
        values = distances(query, self.bank, self.weight)
        shapes = {}
        for template, value in zip(self.templates, values):
            shape = template['shape']
            if shape not in shapes or value < shapes[shape]['distance']:
                shapes[shape] = dict(shape=shape, category=template['category'], distance=float(value))
        ranked = sorted(shapes.values(), key=lambda item: item['distance'])
        codes = []
        for item in ranked:
            if not any(code['category'] == item['category'] for code in codes):
                codes.append(dict(item))
        result = dict(ranked[0])
        result.update(recognizer='dtw_v1', sample_count=32, direction_weight=self.weight,
            template_profile=self.profile, alternatives=ranked[:2], category_alternatives=codes[:2])
        return result
