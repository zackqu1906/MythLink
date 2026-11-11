"""Merge component and stroke encoding paths before ranking or truncation."""
from __future__ import annotations

from .stroke_folds import fold_evidence, fold_adjustment
from .stroke_fuzzy import DEFAULT_FUZZY_POLICY, FAR_POLICY, stroke_evidence


def _tier(complete, corrections, dots, fine):
    exact = complete and not corrections
    dot_variant = complete and bool(dots) and corrections == dots
    if (exact or dot_variant) and fine.get('fold_compatible', True):
        return 0 if exact else 1
    return 2 if (exact or dot_variant) and fine.get('fold_tolerable', True) else 3


def _mapped_folds(row, folds, orders):
    mapping = row['observed_to_expected']
    fine = fold_adjustment(row['char'], row['reference_code'], orders,
                           {mapping[i]: ink for i, ink in folds.items()})
    inverse = {expected: observed for observed, expected in enumerate(mapping)}
    fine['fold_matches'] = [dict(item, reference_index=item['index'], index=inverse[item['index']])
                            for item in fine.get('fold_matches', ())]
    fine['fold_corrections'] = [inverse[i] for i in fine.get('fold_corrections', ())]
    return fine


def encoding_candidates(dictionary, evidence, limit=30, *, fuzzy=True, predictor=None, context='',
                        allowed_characters=None, policy=None):
    # Keep one live ranking-policy seam for literal and component paths.
    from . import stroke_input as runtime
    if not evidence or limit <= 0:
        return []
    policy = policy or DEFAULT_FUZZY_POLICY
    ink = evidence if fuzzy else [stroke_evidence(s['category']) for s in evidence]
    if not dictionary.components_enabled or dictionary.component_decoder is None:
        if fuzzy:
            return dictionary._stroke_candidates(ink, limit, predictor=predictor, context=context,
                allowed_characters=allowed_characters, policy=policy)
        code = ''.join(s['category'] for s in ink)
        return [dict(char=c, code=code, corrections=[], cost=0., exact_match=any(
            entry[1] == code for entry in dictionary.by_character[c]))
            for c in dictionary._literal_candidates(code, limit, allowed_characters=allowed_characters)]
    normal = dictionary._stroke_candidates(ink, limit, predictor=predictor, context=context,
        allowed_characters=allowed_characters, policy=policy, defer_ranking=True)
    # Respect restricted dictionary views used by joined-stroke/LLM searches.
    if dictionary.by_initial is dictionary._component_initial_index:
        allowed = dictionary._component_characters
    else:
        active = {(char, code) for group in dictionary.by_initial.values() for char, code, _ in group}
        allowed = {char for char, graph in dictionary.component_decoder.graphs.items()
                   if (char, graph.code) in active}
    if allowed_characters is not None:
        allowed = allowed.intersection(allowed_characters)
    components = dictionary.component_decoder.match(ink, allowed=allowed,
        left_dots=dictionary._left_dots if fuzzy else {}, policy=policy)
    folds = fold_evidence(ink, policy) if fuzzy and dictionary._fold_orders else {}
    # Prefer the explicit component interpretation of a legacy spelling. This
    # supplies alignment and avoids granting the legacy route a zero variant prior.
    represented = {(r['char'], r['code']) for r in components}
    paths = []
    for old in normal:
        if (old['char'], old['code']) in represented:
            continue
        row = dict(old, match_type='fuzzy' if old['corrections'] else 'literal',
                   component_cost=0., encoding_valid=not old['corrections'],
                   is_complete=len(old['code']) == len(ink))
        paths.append(row)
    for match in components:
        fine = _mapped_folds(match, folds, dictionary._fold_orders) if folds else {}
        score = match['cost']+match['component_cost']+dictionary._fuzzy_priors[match['char']]+fine.get('fold_score', 0.)
        if match['is_complete'] and dictionary.ranks.get(match['char'], 100000) < 100000:
            score -= .35
        tier = _tier(match['is_complete'], match['corrections'], match['left_dot_corrections'], fine)
        sources = max((entry[2] for entry in dictionary.by_character[match['char']]
                       if entry[1] == match['reference_code']), default=0)
        paths.append(dict(match, **fine, score=score,
                          key=(tier, score, -(sources & 1), len(match['code']), match['code'])))
    far = [row for row in paths if row.get('far_corrections')]
    if len(far) > FAR_POLICY['maxRows']:
        # Keep only the best far-class paths before vector and context scoring.
        drop = {id(row) for row in sorted(far, key=lambda row: (row['key'][0], row['score'], row['char']))
                [FAR_POLICY['maxRows']:]}
        paths = [row for row in paths if id(row) not in drop]
    if not paths:
        return dictionary._replacement_candidates(ink, limit, predictor=predictor, context=context,
            allowed_characters=allowed_characters, policy=policy) if fuzzy else []
    from .stroke_context import early_bonuses
    bonuses = (early_bonuses(predictor, predictor.stroke_bonuses(context, {r['char'] for r in paths}), len(ink))
               if predictor else {})
    model = dictionary.stroke_vectors if fuzzy else None
    known_vectors = {}
    for row in paths:
        row['language_bonus'] = bonuses.get(row['char'], 0.)
        row['final_score'] = row['score']-runtime.FUZZY_LANGUAGE_WEIGHT*row['language_bonus']
    if model is not None and model.scope == 'prefix':
        # Component rows carry observed_to_expected, so reordered writing still aligns.
        model.score_rows(paths, ink)
        model = None
    for row in paths:
        if model is not None and row['exact_match'] and len(row['code']) == len(ink):
            mapping = row.get('observed_to_expected')
            aligned = ink
            if mapping:
                aligned = [None]*len(mapping)
                for observed, expected in enumerate(mapping):
                    aligned[expected] = ink[observed]
            cost = model.cost(row['char'], row.get('reference_code', row['code']), aligned)
            row['vector_cost'] = cost
            if cost is not None:
                known = known_vectors.setdefault(row['key'][0], {})
                known[row['char']] = min(cost, known.get(row['char'], float('inf')))
    for row in paths:
        if 'vector_cost' in row and len(known_vectors.get(row['key'][0], ())) >= 2:
            known = list(known_vectors[row['key'][0]].values())
            cost = row['vector_cost'] if row['vector_cost'] is not None else sum(known)/len(known)
            row['vector_score'] = row['final_score']+model.weight*cost
    best = {}
    for row in paths:
        key = (row['key'][0], row.get('vector_score', row['final_score']), *row['key'][2:], row['char'])
        if row['char'] not in best or key < best[row['char']][0]:
            best[row['char']] = (key, row)
    rows = [row for _, row in sorted(best.values(), key=lambda item: item[0])]
    return runtime.promote_completed_fuzzy(rows, ink, predictor, context, limit) if fuzzy else rows[:limit]
