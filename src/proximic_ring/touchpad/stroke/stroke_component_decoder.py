"""Compact component scheduling graphs and prefix decoding.

No character's Cartesian product of component orders is stored. Graph branches
share their suffix; every consumed edge retains its reference stroke index.
"""
from __future__ import annotations

from collections import defaultdict
from functools import lru_cache

from .stroke_components import RULES, ENCLOSURES, VARIANT_COST
from .stroke_fuzzy import match_prefix, DEFAULT_FUZZY_POLICY, far_budget

MAX_PATHS = 32  # Per character, per input position; surfaced in diagnostics.


def _plan(start, end, occurrences):
    parts = []
    i = start
    while i < end:
        choices = [row for row in occurrences if row['scope'][0] == i and row['scope'][-1] < end]
        if not choices:
            parts.append(i)
            i += 1
            continue
        row = max(choices, key=lambda r: (len(r['scope']), r['component'] in ENCLOSURES))
        component, indices, scope = row['component'], row['indices'], row['scope']
        inner_rows = [r for r in occurrences if r is not row and
                      scope[0] <= r['scope'][0] and r['scope'][-1] <= scope[-1]]
        if component in ENCLOSURES:
            inside = [j for j in scope if j not in indices]
            inner = _plan(inside[0], inside[-1]+1, inner_rows) if inside else []
            normal = (inner+list(indices) if component in '辶廴' else
                      [indices[0]]+inner+[indices[1]] if component == '匚' else
                      list(indices[:2])+inner+[indices[2]])
            alternatives = [normal]
            if inner:
                alternatives.append(list(indices)+inner)
        else:
            alternatives = [[indices[j] for j in order] for order in RULES[component][1]]
        parts.append(dict(component=component, indices=tuple(indices), alternatives=alternatives))
        i = scope[-1]+1
    return parts


class ComponentGraph:
    def __init__(self, annotation):
        self.code = annotation['code']
        self.edges = [[]]  # State zero is the shared accepting state.
        self._suffix_cache = {0: ()}
        self._frontier_cache = {0: ()}
        occurrences = annotation['components']
        self._validate(occurrences)
        self.start = self._sequence(_plan(0, len(self.code), occurrences), 0)

    def _validate(self, occurrences):
        scopes = []
        for row in occurrences:
            component, indices, scope = row['component'], row['indices'], row['scope']
            if (component not in RULES or not indices or not scope or
                    any(type(i) is not int for i in indices+scope) or
                    scope != list(range(scope[0], scope[-1]+1)) or
                    not 0 <= scope[0] <= scope[-1] < len(self.code) or
                    indices != sorted(set(indices)) or not set(indices) <= set(scope) or
                    ''.join(self.code[i] for i in indices) != RULES[component][0]):
                raise ValueError('Invalid component occurrence')
            if component not in ENCLOSURES and indices != scope:
                raise ValueError('Local component must be contiguous')
            inside = [i for i in scope if i not in indices]
            if component in ENCLOSURES and inside:
                expected = (scope[-len(indices):] if component in '辶廴' else
                            [scope[0], scope[-1]] if component == '匚' else
                            [scope[0], scope[1], scope[-1]])
                if indices != expected or inside != list(range(inside[0], inside[-1]+1)):
                    raise ValueError('Invalid enclosure schedule')
            for other in scopes:
                overlap = set(scope) & set(other)
                if overlap and (scope == other or not (set(scope) < set(other) or set(other) < set(scope))):
                    raise ValueError('Component scopes must form a tree')
            scopes.append(scope)

    def _node(self, edges):
        self.edges.append(edges)
        return len(self.edges)-1

    def _sequence(self, parts, target):
        for part in reversed(parts):
            if isinstance(part, int):
                target = self._node([(target, part, 0., None)])
            else:
                edges = []
                for variant, sequence in enumerate(part['alternatives']):
                    tag = (part['component'], part['indices'], variant)
                    edges.append((self._sequence(sequence, target), None,
                                  VARIANT_COST if variant else 0., tag))
                target = self._node(edges)
        return target

    def suffix(self, state):
        if state in self._suffix_cache:
            return self._suffix_cache[state]
        # The first alternative is always the reference schedule.
        target, index, _, _ = self.edges[state][0]
        result = (() if index is None else (index,))+self.suffix(target)
        self._suffix_cache[state] = result
        return result

    def frontier(self, state):
        """Next consuming edges with the epsilon branch metadata attached."""
        if state in self._frontier_cache:
            return self._frontier_cache[state]
        result = []
        for target, index, cost, tag in self.edges[state]:
            tags = () if tag is None else (tag,)
            if index is None:
                result.extend((nxt, idx, cost+extra, tags+more)
                              for nxt, idx, extra, more in self.frontier(target))
            else:
                result.append((target, index, cost, tags))
        self._frontier_cache[state] = tuple(result)
        return self._frontier_cache[state]


class ComponentDecoder:
    def __init__(self, annotations):
        self.annotations = annotations
        self.graphs = {char: ComponentGraph(row) for char, row in annotations.items()}
        self.by_initial = defaultdict(list)
        for char, graph in self.graphs.items():
            for letter in {graph.code[index] for _, index, _, _ in graph.frontier(graph.start)}:
                self.by_initial[letter].append(char)

    def snapshot(self):
        return dict(version=1, components=list(RULES), characters=len(self.graphs),
                    maxPaths=MAX_PATHS, variantCost=VARIANT_COST,
                    nodes=sum(len(graph.edges) for graph in self.graphs.values()))

    def match(self, evidence, *, allowed=None, left_dots=None, policy=DEFAULT_FUZZY_POLICY):
        if not evidence:
            return []
        budget = far_budget(policy, evidence)
        initials = set(evidence[0]['costs']) | (set(evidence[0].get('far', ())) if budget else set())
        if evidence[0]['category'] in 'ps' and not evidence[0].get('manual', True):
            initials.add('n')
        chars = sorted({char for initial in initials for char in self.by_initial[initial]})
        rows, one_stroke_cache = [], {}
        for char in chars:
            if allowed is not None and char not in allowed:
                continue
            graph = self.graphs[char]
            if len(evidence) > len(graph.code):
                continue
            dots = set((left_dots or {}).get((char, graph.code), ()))
            # state, observed->expected, component path, variant cost, ink cost,
            # rescue count, corrections, left-dot alternatives, far-class strokes
            active = [(graph.start, (), (), 0., 0., 0, (), (), ())]
            truncated = False
            for position, stroke in enumerate(evidence):
                following = {}
                for state, mapping, tags, component_cost, ink_cost, rescues, corrections, dot_changes, far in active:
                    for target, index, extra, new_tags in graph.frontier(state):
                        letter = graph.code[index]
                        cache_key = (position, letter, index in dots)
                        if cache_key not in one_stroke_cache:
                            one_stroke_cache[cache_key] = match_prefix(letter, [stroke],
                                left_dots=(0,) if index in dots else (), policy=policy, far_limit=budget)
                        ink = one_stroke_cache[cache_key]
                        if (ink is None or rescues+ink['rescues'] > policy.max_rescues
                                or len(far)+len(ink['far_corrections']) > budget):
                            continue
                        path = (target, mapping+(index,), tags+new_tags, component_cost+extra,
                                ink_cost+ink['cost'], rescues+ink['rescues'],
                                corrections+((position,) if ink['corrections'] else ()),
                                dot_changes+((position,) if ink['left_dot_corrections'] else ()),
                                far+((position,) if ink['far_corrections'] else ()))
                        key = (target, path[1])
                        old = following.get(key)
                        if old is None or path[3]+path[4] < old[3]+old[4]:
                            following[key] = path
                truncated |= len(following) > MAX_PATHS
                active = sorted(following.values(), key=lambda p: (p[3]+p[4], p[1]))[:MAX_PATHS]
                if not active:
                    break
            for state, mapping, tags, component_cost, ink_cost, rescues, corrections, dot_changes, far in active:
                full_mapping = mapping+graph.suffix(state)
                variant = any(tag[2] for tag in tags)
                # Reference paths are included to unify evidence for annotated
                # characters already recalled by legacy whole-character codes.
                rows.append(dict(char=char, reference_code=graph.code,
                    code=''.join(graph.code[i] for i in full_mapping),
                    observed_to_expected=full_mapping,
                    component_path=[dict(component=c, indices=list(indices), variant=v) for c, indices, v in tags],
                    component_cost=component_cost, order_variant=variant, cost=ink_cost,
                    rescues=rescues, corrections=list(corrections), left_dot_corrections=list(dot_changes),
                    far_corrections=list(far), **({'replacement_corrections': list(far)} if far else {}),
                    is_complete=len(mapping) == len(graph.code), encoding_valid=not corrections,
                    exact_match=len(mapping) == len(graph.code) and not corrections,
                    match_type='component', search_truncated=truncated))
        return rows


@lru_cache(maxsize=1)
def builtin_component_decoder():
    from .stroke_components import component_annotations
    return ComponentDecoder(component_annotations())
