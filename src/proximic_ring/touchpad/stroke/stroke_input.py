"""Five-stroke composition using the local ring-stroke-ime-demo data.

The recognizer supplements whole-path template distance with sustained turns
and separate part comparisons. Scores are not calibrated confidence values.
"""
from __future__ import annotations

from collections import defaultdict, deque
import gzip
import json
import math
from importlib.resources import files

from .stroke_structure import splits, compare_parts, nearly_straight

SYMBOLS = {"h": "一", "s": "丨", "p": "丿", "n": "丶", "z": "𠃍"}
NAMES = {"h": "横", "s": "竖", "p": "撇", "n": "点／捺", "z": "折"}


def _normalize(points, count=32):
    clean = [tuple(map(float, points[0]))] if points else []
    for point in points[1:]:
        point = tuple(map(float, point))
        if math.dist(point, clean[-1]) > 0:
            clean.append(point)
    if len(clean) < 2:
        return None
    lengths = [0.0]
    for a, b in zip(clean, clean[1:]):
        lengths.append(lengths[-1] + math.dist(a, b))
    total = lengths[-1]
    if total <= 0:
        return None
    trace = []
    j = 1
    for i in range(count):
        target = total * i / (count - 1)
        while j < len(lengths) - 1 and lengths[j] < target:
            j += 1
        ratio = (target - lengths[j - 1]) / (lengths[j] - lengths[j - 1])
        a, b = clean[j - 1], clean[j]
        trace.append(((a[0] + ratio * (b[0] - a[0]) - clean[0][0]) / total,
                      (a[1] + ratio * (b[1] - a[1]) - clean[0][1]) / total))
    return trace


class StrokeDictionary:
    def __init__(self, data=None, *, sample_count=32, recognizer='structure'):
        if recognizer not in ('structure', 'dtw'):
            raise ValueError('Unknown stroke recognizer')
        if recognizer == 'dtw' and sample_count != 32:
            raise ValueError('Runtime DTW uses 32 points')
        self.recognizer = recognizer
        self._dtw = None
        if sample_count not in (32, 64, 96):
            raise ValueError('sample_count must be 32, 64, or 96')
        self.sample_count = sample_count
        if data is None:
            with gzip.open(files("proximic_ring").joinpath("assets/strokes.json.gz"), "rt", encoding="utf-8") as stream:
                data = json.load(stream)
        self.ranks = data["ranks"]
        self.templates = data["templates"]
        if sample_count != 32 and any('raw_trace' not in t for t in self.templates):
            raise ValueError('Higher resolution requires original template recordings')
        self._comparison_traces = [t['trace'] if sample_count == 32 else
            _normalize(t['raw_trace'], sample_count) for t in self.templates]
        self.entries = data["entries"]
        self._template_parts = [splits(t['trace']) if t['shape'] in {'撇点', '横钩'} else {}
                                for t in self.templates]
        self.by_initial = defaultdict(list)
        for entry in self.entries:
            if entry[1]:
                self.by_initial[entry[1][0]].append(entry)

    def recognize_template(self, points):
        """Original whole-path baseline, retained for comparison and rollback."""
        return self._recognize(points, structured=False)

    def recognize(self, points):
        if self.recognizer == 'dtw':
            if self._dtw is None:
                from .stroke_dtw import DTWRecognizer
                self._dtw = DTWRecognizer(self.templates)
            return self._dtw.recognize(points)
        return self._recognize(points, structured=True)

    def _recognize(self, points, *, structured):
        trace = _normalize(points, self.sample_count)
        if trace is None:
            return None
        best = None
        evidence = splits(points) if structured else {}
        straight = structured and not evidence and nearly_straight(points)
        simple = {'横', '提', '竖', '撇', '点', '捺'}
        for index, template in enumerate(self.templates):
            distance = sum((x - tx) ** 2 + (y - ty) ** 2
                           for (x, y), (tx, ty) in zip(trace, self._comparison_traces[index])) / self.sample_count
            if template["shape"] == "卧钩":
                distance *= 2
            whole_distance = distance
            shape = template['shape']
            if shape in evidence and shape in self._template_parts[index]:
                distance = compare_parts(evidence[shape], self._template_parts[index][shape], distance)
            elif evidence and shape in simple | {'竖钩'}:
                distance += .025
            elif straight and shape == '竖钩':
                distance += .025
            if best is None or distance < best[0]:
                best = (distance, template["category"], shape, whole_distance)
        result = {"category": best[1], "shape": best[2], "distance": best[3]}
        if structured:
            result.update(score=best[0], structure=list(evidence), recognizer='structure_v1')
        return result

    def candidates(self, code, limit=30):
        if not code or any(letter not in SYMBOLS for letter in code):
            return []
        rows = {}
        for char, full_code, sources in self.by_initial[code[0]]:
            if not full_code.startswith(code):
                continue
            exact = full_code == code
            current = rows.get(char)
            if current is None or (exact, sources) > (current[0], current[1]):
                rows[char] = (exact, sources, len(full_code))
        ranked = sorted(rows.items(), key=lambda item: (
            self.ranks.get(item[0], 100000) - (3000 if item[1][0] and item[0] in self.ranks else 0),
            -(item[1][1] & 1), item[1][2], item[0]))
        return [char for char, _ in ranked[:limit]]


class StrokeCollector:
    """Collect only between frame boundaries confirmed by the SDK detector.

    The detector confirms a transition after its boundary frame. Keep recent
    movement to recover the start, and trim already received lift movement at
    the end. Contact events can also precede movement in the same BLE batch.
    """
    def __init__(self, *, min_length=1.5, max_points=800):
        self.min_length, self.max_points = min_length, max_points
        self.reset()

    def reset(self):
        self.points = []
        self.length = 0.0
        self.x = self.y = 0.0
        self.history = deque(maxlen=64)
        self.start_step = self.end_step = self.last_step = None
        self.moves = []

    @property
    def touching(self):
        return self.start_step is not None and self.end_step is None

    def _add(self, event):
        if self.moves and event.step <= self.moves[-1].step: return
        if not self.moves and event.step != self.start_step:
            self._discard()
            return
        if len(self.moves) >= self.max_points:
            self._discard()
            return
        self.moves.append(event)
        if len(self.moves) == 1:
            self.points = [(0., 0.)]
        else:
            self.x += float(event.dx); self.y += float(event.dy)
            self.length += math.hypot(event.dx, event.dy)
            self.points.append((self.x, self.y))

    def _discard(self):
        self.start_step = self.end_step = None
        self.moves = []; self.points = []; self.length = 0.
        self.x = self.y = 0.

    def _finish_if_ready(self):
        if self.end_step is None or self.last_step is None or self.last_step < self.end_step-1:
            return None
        points = self.points
        valid = self.length >= self.min_length and len(points) >= 5
        self._discard()
        return points if valid else None

    def feed(self, event):
        if event.kind == 'contact':
            if event.state == 'reset':
                self.reset()
            elif event.state == 'down':
                self._discard()
                self.start_step = event.step
                for previous in self.history:
                    if previous.step >= event.step: self._add(previous)
            elif event.state == 'up' and self.start_step is not None:
                self.end_step = event.step
                # Remove frames past the SDK's actual release boundary, not
                # merely frames received after its notification was delivered.
                kept = [move for move in self.moves if move.step < event.step]
                self.moves = []; self.points = []; self.length = 0.
                self.x = self.y = 0.
                for previous in kept: self._add(previous)
                return self._finish_if_ready()
            return None
        if event.kind != 'move': return None
        if self.last_step is not None and event.step <= self.last_step: return None
        if self.last_step is not None and event.step != self.last_step+1:
            self.reset()
        if not all(math.isfinite(float(v)) for v in (event.dx, event.dy)):
            self.reset()
            return None
        self.last_step = event.step
        self.history.append(event)
        if self.start_step is not None and event.step >= self.start_step:
            if self.end_step is None or event.step < self.end_step: self._add(event)
        return self._finish_if_ready()
