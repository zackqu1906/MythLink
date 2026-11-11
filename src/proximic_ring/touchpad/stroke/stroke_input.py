"""Five-stroke composition using the local ring-stroke-ime-demo data.

The recognizer supplements whole-path template distance with sustained turns
and separate part comparisons. Scores are not calibrated confidence values.
"""
from __future__ import annotations

from collections import defaultdict, deque
import gzip
import json
import math
import heapq
from importlib.resources import files

from .stroke_structure import splits, compare_parts, nearly_straight
from .stroke_context import FUZZY_LANGUAGE_WEIGHT, early_bonuses, promote_completed_fuzzy
from .stroke_fuzzy import FAR_POLICY, far_budget

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
    def __init__(self, data=None, *, sample_count=32, recognizer='structure', left_dots=None, personal_model=None,
                 fold_orders=None, components=None):
        self.personal_model = personal_model
        # Optional StrokeVectorModel; None keeps the five-class ranking unchanged.
        self.stroke_vectors = None
        if recognizer not in ('structure', 'dtw'):
            raise ValueError('Unknown stroke recognizer')
        if recognizer == 'dtw' and sample_count != 32:
            raise ValueError('Runtime DTW uses 32 points')
        self.recognizer = recognizer
        self._dtw = None
        if sample_count not in (32, 64, 96):
            raise ValueError('sample_count must be 32, 64, or 96')
        self.sample_count = sample_count
        builtin = data is None
        if builtin:
            with gzip.open(files("proximic_ring").joinpath("assets/strokes.json.gz"), "rt", encoding="utf-8") as stream:
                data = json.load(stream)
        self.ranks = data["ranks"]
        self.templates = data["templates"]
        if sample_count != 32 and any('raw_trace' not in t for t in self.templates):
            raise ValueError('Higher resolution requires original template recordings')
        self._comparison_traces = [t['trace'] if sample_count == 32 else
            _normalize(t['raw_trace'], sample_count) for t in self.templates]
        from .stroke_dictionary_orders import filter_order_variants
        self.entries = filter_order_variants(data["entries"]) if builtin else data["entries"]
        from .stroke_folds import fold_orders as builtin_fold_orders
        self._fold_orders = builtin_fold_orders() if builtin and fold_orders is None else (fold_orders or {})
        from .stroke_left_dots import left_dot_annotations, validate_annotations
        self._left_dots = (left_dot_annotations() if builtin and left_dots is None else
                           validate_annotations(left_dots or []))
        if builtin:
            entry_codes = {(character, code) for character, code, *_ in self.entries}
            self._left_dots = {key: positions for key, positions in self._left_dots.items()
                               if key in entry_codes}
        self._fuzzy_priors = {char: .35*math.log1p(self.ranks.get(char, 100000))
                              for char, _, _ in self.entries}
        self._template_parts = [splits(t['trace']) if t['shape'] in {'撇点', '横钩'} else {}
                                for t in self.templates]
        self.by_initial = defaultdict(list)
        self.by_character = defaultdict(list)
        self._left_dot_initial = []
        for entry in self.entries:
            self.by_character[entry[0]].append(entry)
            if entry[1]:
                self.by_initial[entry[1][0]].append(entry)
                if 0 in self._left_dots.get((entry[0], entry[1]), ()):
                    self._left_dot_initial.append(entry)
        from .stroke_component_decoder import ComponentDecoder, builtin_component_decoder
        self.component_decoder = (builtin_component_decoder() if builtin and components is None else
                                  ComponentDecoder(components) if components else None)
        self.components_enabled = self.component_decoder is not None
        self._component_initial_index = self.by_initial
        self._component_characters = frozenset(char for char, graph in
            (self.component_decoder.graphs.items() if self.component_decoder else ())
            if any(entry[1] == graph.code for entry in self.by_character.get(char, ())))

    def _scoped_groups(self, allowed):
        """Dictionary groups filtered to the allowed scope, cached per view and scope."""
        if allowed is None:
            return self.by_initial, self._left_dot_initial
        # Groups may be replaced in place (restricted views, tests): fingerprint them.
        key = (tuple((k, id(v), len(v)) for k, v in self.by_initial.items()),
               id(self._left_dot_initial), len(self._left_dot_initial), frozenset(allowed))
        cache = self.__dict__.setdefault('_scope_cache', {})
        if key not in cache:
            if len(cache) > 8:
                cache.clear()
            scope = key[-1]
            cache[key] = ({initial: [e for e in group if e[0] in scope] for initial, group in self.by_initial.items()},
                          [e for e in self._left_dot_initial if e[0] in scope])
        return cache[key]

    def recognize_template(self, points):
        """Original whole-path baseline, retained for comparison and rollback."""
        return self._recognize(points, structured=False)

    def recognize(self, points):
        if self.recognizer == 'dtw':
            if self._dtw is None:
                from .stroke_dtw import DTWRecognizer
                self._dtw = DTWRecognizer(self.templates)
            result = self._dtw.recognize(points)
            return self.personal_model.adapt(points, result) if self.personal_model is not None else result
        return self._recognize(points, structured=True)

    def _recognize(self, points, *, structured):
        trace = _normalize(points, self.sample_count)
        if trace is None:
            return None
        best = None
        by_class = {}
        by_shape = {}
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
            old = by_class.get(template['category'])
            if old is None or distance < old['distance']:
                by_class[template['category']] = dict(category=template['category'], shape=shape, distance=distance)
            old_shape = by_shape.get(shape)
            if old_shape is None or distance < old_shape['distance']:
                by_shape[shape] = dict(category=template['category'], shape=shape, distance=distance)
        result = {"category": best[1], "shape": best[2], "distance": best[3]}
        result['category_scores'] = sorted(by_class.values(), key=lambda item: item['distance'])
        result['shape_scores'] = sorted(by_shape.values(), key=lambda item: item['distance'])
        if structured:
            result.update(score=best[0], structure=list(evidence), recognizer='structure_v1')
        return result

    def candidates(self, code, limit=30, *, allowed_characters=None):
        if self.components_enabled:
            from .stroke_fuzzy import stroke_evidence
            if not code or any(letter not in SYMBOLS for letter in code):
                return []
            return [row['char'] for row in self.encoding_candidates([stroke_evidence(c) for c in code],
                limit, fuzzy=False, allowed_characters=allowed_characters)]
        return self._literal_candidates(code, limit, allowed_characters=allowed_characters)

    def _literal_candidates(self, code, limit=30, *, allowed_characters=None):
        if not code or any(letter not in SYMBOLS for letter in code):
            return []
        rows = {}
        for char, full_code, sources in self.by_initial[code[0]]:
            if allowed_characters is not None and char not in allowed_characters:
                continue
            if not full_code.startswith(code):
                continue
            exact = full_code == code
            current = rows.get(char)
            if current is None or (exact, sources) > (current[0], current[1]):
                rows[char] = (exact, sources, len(full_code))
        ranked = sorted(rows.items(), key=lambda item: (
            not item[1][0], self.ranks.get(item[0], 100000),
            -(item[1][1] & 1), item[1][2], item[0]))
        return [char for char, _ in ranked[:limit]]

    def fuzzy_candidates(self, evidence, limit=30, *, predictor=None, context='', allowed_characters=None,
                         policy=None):
        return self.encoding_candidates(evidence, limit, predictor=predictor, context=context,
            allowed_characters=allowed_characters, policy=policy)

    def encoding_candidates(self, evidence, limit=30, *, fuzzy=True, predictor=None, context='',
                            allowed_characters=None, policy=None):
        """Ordinary encoding paths, including components, independent of repair."""
        from .stroke_component_ranking import encoding_candidates
        return encoding_candidates(self, evidence, limit, fuzzy=fuzzy, predictor=predictor,
            context=context, allowed_characters=allowed_characters, policy=policy)

    def _stroke_candidates(self, evidence, limit=30, *, predictor=None, context='', allowed_characters=None,
                           policy=None, defer_ranking=False):
        """Rescore the entire prefix; never discard an earlier interpretation."""
        from .stroke_fuzzy import match_prefix, DEFAULT_FUZZY_POLICY
        from .stroke_folds import fold_evidence, fold_adjustment
        policy = policy or DEFAULT_FUZZY_POLICY
        if not evidence:
            return []
        folds = fold_evidence(evidence, policy) if self._fold_orders else {}
        # Preserve the original ordering when every stroke was explicitly entered.
        use_left_dots = bool(self._left_dots) and any(
            stroke['category'] in 'ps' and not stroke.get('manual', True) for stroke in evidence)
        prefix_vectors = self.stroke_vectors is not None and self.stroke_vectors.scope == 'prefix'
        # The literal shortcut truncates by frequency, so prefix vectors take the full path.
        if (not defer_ranking and not prefix_vectors and not folds and not use_left_dots
                and all(len(stroke['costs']) == 1 for stroke in evidence)
                and not (far_budget(policy, evidence) and any(stroke.get('far') for stroke in evidence))):
            code = ''.join(stroke['category'] for stroke in evidence)
            exact_chars = {char for char, full_code, _ in self.by_initial[code[0]] if full_code == code}
            matches = [dict(char=char, code=code, cost=0., corrections=[], exact_match=char in exact_chars)
                       for char in self._literal_candidates(code, limit, allowed_characters=allowed_characters)]
            if matches and self.stroke_vectors is not None:
                matches = self.stroke_vectors.rerank(matches, evidence, self._fuzzy_priors)
            return matches or self._replacement_candidates(evidence, limit, predictor=predictor,
                context=context, allowed_characters=allowed_characters, policy=policy)
        rows, prefix_matches = {}, {}
        initials = set(evidence[0]['costs']) | (set(evidence[0].get('far', ())) if far_budget(policy, evidence) else set())
        by_initial, left_dot_initial = self._scoped_groups(allowed_characters)
        groups = [by_initial.get(initial, ()) for initial in sorted(initials)]
        if (use_left_dots and evidence[0]['category'] in 'ps'
                and not evidence[0].get('manual', True) and 'n' not in evidence[0]['costs']):
            groups.append(left_dot_initial)
        for group in groups:
            for char, code, sources in group:
                prefix = code[:len(evidence)]
                dots = tuple(index for index in self._left_dots.get((char, code), ()) if index < len(evidence))
                cache_key = (prefix, dots)
                if cache_key not in prefix_matches:
                    prefix_matches[cache_key] = match_prefix(prefix, evidence, left_dots=dots, policy=policy)
                match = prefix_matches[cache_key]
                if match is None:
                    continue
                rank = self.ranks.get(char, 100000)
                fine = fold_adjustment(char, code, self._fold_orders, folds) if folds else {}
                score = match['cost'] + self._fuzzy_priors[char] + fine.get('fold_score', 0.)
                if len(code) == len(evidence) and rank < 100000:
                    score -= .35
                exact_match = len(code) == len(evidence) and not match['corrections']
                complete_variant = (len(code) == len(evidence) and bool(match['left_dot_corrections'])
                                    and match['corrections'] == match['left_dot_corrections'])
                # A complete five-category code is insufficient when the ink
                # favors another fold (e.g. first 撇点 versus 乙). Only a clearly
                # different fold also gives up the lead over unfinished prefixes.
                complete = exact_match or complete_variant
                if complete and fine.get('fold_compatible', True):
                    priority = 0 if exact_match else 1
                else:
                    priority = 2 if complete and fine.get('fold_tolerable', True) else 3
                key = (priority, score, -(sources & 1), len(code), code)
                old = rows.get(char)
                if old is None or key < old['key']:
                    rows[char] = dict(char=char, code=code, key=key, score=score,
                                      exact_match=exact_match, **match, **fine)
        far_rows = [row for row in rows.values() if row.get('far_corrections')]
        if len(far_rows) > FAR_POLICY['maxRows']:
            for row in heapq.nsmallest(len(far_rows)-FAR_POLICY['maxRows'], far_rows,
                                       key=lambda row: (-row['key'][0], -row['score'], row['char'])):
                del rows[row['char']]
        if defer_ranking:
            return list(rows.values())
        if not rows:
            return self._replacement_candidates(evidence, limit, predictor=predictor,
                context=context, allowed_characters=allowed_characters, policy=policy)
        bonuses = early_bonuses(predictor, predictor.stroke_bonuses(context, rows), len(evidence)) if predictor else {}
        if prefix_vectors:
            # Score the whole pool before truncation so unfinished characters can rise.
            pool = [dict(item, language_bonus=bonuses.get(item['char'], 0.),
                         final_score=item['score']-FUZZY_LANGUAGE_WEIGHT*bonuses.get(item['char'], 0.))
                    for item in rows.values()]
            self.stroke_vectors.score_rows(pool, evidence)
            results = heapq.nsmallest(max(limit, 30), pool, key=lambda item: (
                item['key'][0], item.get('vector_score', item['final_score']), *item['key'][2:], item['char']))
            return promote_completed_fuzzy(results, evidence, predictor, context, limit)
        results = heapq.nsmallest(max(limit, 30), rows.values(), key=lambda item: (
            item['key'][0], item['score']-FUZZY_LANGUAGE_WEIGHT*bonuses.get(item['char'], 0.),
            *item['key'][2:], item['char']))
        results = [dict(item, language_bonus=bonuses.get(item['char'], 0.),
                     final_score=item['score']-FUZZY_LANGUAGE_WEIGHT*bonuses.get(item['char'], 0.)) for item in results]
        if self.stroke_vectors is not None:
            results = self.stroke_vectors.rerank(results, evidence, self._fuzzy_priors)
        return promote_completed_fuzzy(results, evidence, predictor, context, limit)

    def _replacement_candidates(self, evidence, limit, *, predictor, context, allowed_characters, policy):
        """Recover an empty match by changing one nearby automatic stroke only."""
        from .stroke_fuzzy import replacement_options
        observed = ''.join(stroke['category'] for stroke in evidence)
        prefixes = {}
        for index, stroke in enumerate(evidence):
            for expected, cost in replacement_options(stroke, policy=policy).items():
                prefix = observed[:index] + expected + observed[index+1:]
                prefixes[prefix] = (index, cost)
        rows = {}
        for initial in sorted({prefix[0] for prefix in prefixes}):
            for char, code, sources in self.by_initial[initial]:
                if allowed_characters is not None and char not in allowed_characters:
                    continue
                replacement = prefixes.get(code[:len(observed)])
                if replacement is None:
                    continue
                index, cost = replacement
                complete = len(code) == len(observed)
                score = cost + self._fuzzy_priors[char]
                key = (not complete, score, -(sources & 1), len(code), code)
                old = rows.get(char)
                if old is None or key < old['key']:
                    rows[char] = dict(char=char, code=code, cost=cost, score=score, key=key,
                        exact_match=False, rescues=1, corrections=[index], left_dot_corrections=[],
                        replacement_corrections=[index], match_type='single_replacement')
        bonuses = predictor.stroke_bonuses(context, rows) if predictor else {}
        results = heapq.nsmallest(limit, rows.values(), key=lambda item: (
            item['key'][0], item['score']-FUZZY_LANGUAGE_WEIGHT*bonuses.get(item['char'], 0.),
            *item['key'][2:], item['char']))
        return [dict(item, language_bonus=bonuses.get(item['char'], 0.),
                     final_score=item['score']-FUZZY_LANGUAGE_WEIGHT*bonuses.get(item['char'], 0.)) for item in results]


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
