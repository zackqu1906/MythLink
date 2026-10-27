"""Audit cleared compositions against their recorded trajectories and candidates.

Read-only replay. Reference text is used only to assess candidate recall.
No recognition, dictionary, or runtime input policy is modified.
"""
import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from evaluate_stroke_llm import raw_trials
from proximic_ring.touchpad.stroke.stroke_common import common_characters
from proximic_ring.touchpad.stroke.stroke_context import StrokeContext
from proximic_ring.touchpad.stroke.stroke_fuzzy import FuzzyPolicy, stroke_evidence
from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary
from proximic_ring.touchpad.stroke.stroke_word_correction import WholeCharacterCorrector, align_strokes, merge_candidates


PUNCTUATION = str.maketrans('', '', '，。？')


def prefix_experiment(observed, evidence, dictionary, predictor, context, allowed):
    """Unweighted two-edit prefix search, for diagnosis, never runtime use.

    Targets are intentionally absent. All surviving trie prefixes expand to
    characters, including unfinished ones. Rank is only a simple comparator.
    """
    n, budget = len(observed), 2
    trie = {}
    for char, code, *_ in dictionary.entries:
        if allowed is not None and char not in allowed:
            continue
        node = trie
        for letter in code:
            node = node.setdefault(letter, {})
        node.setdefault('', set()).add(char)
    rows = {}

    def weighted(prefix):
        # Diagnostic scores only; these distances are not probabilities.
        prev = [1.3*j for j in range(len(prefix)+1)]
        for i, stroke in enumerate(evidence,1):
            scores = {r['category']:r['distance'] for r in stroke['recognition']['category_scores']}
            best = scores.get(stroke['category'],0.)
            row = [1.3*i]
            for j, expected in enumerate(prefix,1):
                replacement = (0. if expected == stroke['category'] else
                    .45+min(2.,max(0.,scores.get(expected,1.)-best)/(.04+best)))
                row.append(min(row[-1]+1.3,prev[j]+1.3,prev[j-1]+replacement))
            prev = row
        return prev[-1]

    def descendants(node):
        stack = [node]
        while stack:
            child = stack.pop()
            yield from child.get('', ())
            stack.extend(v for k,v in child.items() if k)

    stack = [(trie, '', list(range(n+1)), None, '')]
    while stack:
        node, prefix, previous, before_previous, last = stack.pop()
        for letter, child in node.items():
            if not letter:
                continue
            depth = len(prefix)+1
            row = [depth]
            for j, value in enumerate(observed, 1):
                cost = min(row[-1]+1, previous[j]+1, previous[j-1]+(letter != value))
                if before_previous is not None and j > 1 and letter == observed[j-2] and last == value:
                    cost = min(cost, before_previous[j-2]+1)
                row.append(cost)
            if depth >= max(1,n-budget) and row[n] <= budget:
                trace_cost = weighted(prefix+letter)
                for char in descendants(child):
                    rows[char] = min(rows.get(char, (99,99,99)), (row[n],trace_cost,abs(depth-n)))
            if depth < n+budget and min(row) <= budget:
                stack.append((child,prefix+letter,row,previous,letter))
    import math
    bonuses = predictor.stroke_bonuses(context, rows)
    unweighted = sorted(rows,key=lambda char:(rows[char][0],
        .035*math.log1p(dictionary.ranks.get(char,100000))-.35*bonuses.get(char,0),rows[char][2],char))
    weighted_rows = sorted(rows,key=lambda char:(rows[char][0],rows[char][1]+
        .035*math.log1p(dictionary.ranks.get(char,100000))-.35*bonuses.get(char,0),rows[char][2],char))
    return unweighted,weighted_rows


def joint_experiment(observed, evidence, corrector, dictionary, predictor, context, allowed):
    """A single recorded fuzzy alternative plus existing two-edit repair.

    Deliberately a limited ablation, not a proposed production scoring policy.
    It shows whether individually available evidence is lost between stages.
    """
    import math
    variants = {observed:0.}
    for i, stroke in enumerate(evidence):
        for letter,cost in stroke['costs'].items():
            if letter != observed[i]:
                variants[observed[:i]+letter+observed[i+1:]] = .25*cost
    rows = {}
    for variant, fuzzy_cost in variants.items():
        for row in corrector.candidates(variant,evidence=evidence,context=context,
                                      predictor=predictor,allowed_characters=allowed):
            score = fuzzy_cost+row['cost']+.035*math.log1p(dictionary.ranks.get(row['char'],100000))-.35*row['language_bonus']
            rows[row['char']] = min(rows.get(row['char'],1000.),score)
    return sorted(rows,key=lambda char:(rows[char],char))


def inspect(report, raw, name, dictionary, predictor, corrector, allowed, scope):
    measured = [e for e in report['events'] if e['kind'] == 'stroke' and e.get('recognized')]
    assert len(measured) == len(raw), (name, len(measured), len(raw))
    assert all(e['recognized'] == r['result']['category'] for e, r in zip(measured, raw))
    text, ink, history, compositions, index = '', [], [], [], 0
    start = 0.
    for event_number, event in enumerate(report['events']):
        kind = event['kind']
        if kind == 'stroke' and event.get('recognized'):
            record = raw[index]
            index += 1
            if not ink:
                start = event['elapsed_s']
                history = []
            assert event['stroke_index'] == len(ink)
            if len(report['target']) > 1:
                assert event['position'] == len(text)
            policy = FuzzyPolicy(record.get('fuzzy_policy', {}).get('level', 50))
            ink.append(stroke_evidence(event['recognized'], record['result'], policy=policy,
                                       metrics=record.get('kinematics')))
            code = ''.join(s['category'] for s in ink)
            assert code == record['code']
            history.append(dict(time=record['time'], elapsed_s=event['elapsed_s'], code=code,
                                logged_normal=''.join(r['char'] for r in record['fuzzy_candidates']),
                                normal_empty=not record['fuzzy_candidates']))
        elif kind == 'undo_stroke':
            assert event['code'] == ''.join(s['category'] for s in ink)
            ink = ink[:-1]
        elif kind == 'commit' and event.get('delivered'):
            ink, history = [], []
            text += event['character']
        elif kind == 'delete_character':
            text = text[:-1]
        elif kind == 'clear_strokes':
            code = ''.join(s['category'] for s in ink)
            assert code == event['code'], (name, code, event['code'])
            if len(code) >= 5:
                target = (report['target'][len(text)] if len(report['target']) > 1
                          and report['target'].startswith(text) and len(text) < len(report['target']) else None)
                target_source = 'reference-prefix' if target else None
                if target is None:
                    plain_text = text.translate(PUNCTUATION)
                    plain_target = report['target'].translate(PUNCTUATION)
                    if plain_target.startswith(plain_text) and len(plain_text) < len(plain_target):
                        target, target_source = plain_target[len(plain_text)], 'reference-ignore-punctuation'
                next_commit = next((e for e in report['events'][event_number+1:]
                                    if e['kind'] == 'commit' and e.get('delivered')), None)
                if target is None and next_commit and next_commit['position'] == len(text):
                    target, target_source = next_commit['character'], 'inferred-next-commit'
                normal = dictionary.fuzzy_candidates(ink, predictor=predictor, context=text,
                    allowed_characters=allowed, policy=policy)
                repairs = corrector.candidates(code, evidence=ink, context=text, predictor=predictor,
                    allowed_characters=allowed, excluded_characters=[r['char'] for r in normal])
                merged = merge_candidates([r['char'] for r in normal], repairs)
                logged_repairs = [e for e in report['events'] if e['kind'] == 'whole_correction_candidates'
                                  and start <= e['elapsed_s'] <= event['elapsed_s'] and e['code'] == code]
                row = dict(trial=name, context=text, target=target, target_source=target_source,
                    recorded_scope=scope, start_s=start,
                    clear_s=event['elapsed_s'], duration_s=event['elapsed_s']-start,
                    code=code, strokes=len(code), attempts=len(history), history=history,
                    normal=''.join(r['char'] for r in normal), repair=''.join(r['char'] for r in repairs),
                    merged=''.join(merged), target_rank=merged.index(target)+1 if target in merged else None,
                    correction_enabled=report.get('whole_correction_enabled', False),
                    logged_repairs=[dict(elapsed_s=e['elapsed_s'], count=e['candidate_count'],
                                        chars=''.join(r['char'] for r in e['candidates'])) for e in logged_repairs],
                    poor_score_strokes=[i+1 for i,s in enumerate(ink)
                        if min((r['distance'] for r in s['recognition']['category_scores']), default=0) > .08])
                if target:
                    codes = sorted(set(c for ch,c,*_ in dictionary.entries if ch == target))
                    best_code = min(codes, key=lambda c: align_strokes(code,c)['distance'])
                    row.update(target_orders=codes, alignment=align_strokes(code,best_code),
                               target_in_scope=allowed is None or target in allowed)
                    prefix = []
                    for n in range(1, len(ink)+1):
                        best = min((align_strokes(code[:n], c[:m])['distance'], c[:m], c)
                                   for c in codes for m in range(max(1,n-2), min(len(c),n+2)+1))
                        prefix.append(dict(n=n, distance=best[0], prefix=best[1]))
                    row['prefix_distances'] = prefix
                    row['expected_stroke_evidence'] = [dict(n=i+1, observed=s['category'],
                        expected=best_code[i] if i < len(best_code) else None, costs=s['costs'],
                        scores=s['recognition']['category_scores']) for i,s in enumerate(ink)]
                    wider = WholeCharacterCorrector(dictionary.entries, dictionary.ranks,
                        replace(corrector.policy,max_edits=3,limit=10000),allowed_characters=allowed)
                    wide_rows = wider.candidates(code,evidence=ink,context=text,predictor=predictor,
                        allowed_characters=allowed)
                    prefixes,weighted_prefixes = prefix_experiment(code,ink,dictionary,predictor,text,allowed)
                    joined = joint_experiment(code,ink,corrector,dictionary,predictor,text,allowed)
                    expanded = dictionary.fuzzy_candidates(ink,predictor=predictor,context=text,
                        allowed_characters=common_characters(),policy=policy)
                    row['experiments'] = dict(
                        three_edit_count=len(wide_rows),three_edits=''.join(r['char'] for r in wide_rows[:18]),
                        three_edit_target_rank=next((i+1 for i,r in enumerate(wide_rows) if r['char']==target),None),
                        two_edit_prefix_count=len(prefixes),two_edit_prefix_top18=''.join(prefixes[:18]),
                        two_edit_prefix_target_rank=prefixes.index(target)+1 if target in prefixes else None,
                        weighted_prefix_top18=''.join(weighted_prefixes[:18]),
                        weighted_prefix_target_rank=weighted_prefixes.index(target)+1 if target in weighted_prefixes else None,
                        joint_top18=''.join(joined[:18]),
                        joint_target_rank=joined.index(target)+1 if target in joined else None,
                        scope6500_target_rank=next((i+1 for i,r in enumerate(expanded) if r['char']==target),None))
                compositions.append(row)
            ink, history = [], []
    assert text == report['actual'], (name, text, report['actual'])
    return compositions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'data/diagnostics/candidate-stalls/replay.json')
    args = parser.parse_args()
    dictionary, predictor = StrokeDictionary(recognizer='dtw'), StrokeContext()
    corrector = WholeCharacterCorrector(dictionary.entries, dictionary.ranks,
                                        allowed_characters=common_characters())
    cache, rows, trials, excluded = {}, [], [], []
    table = json.loads((ROOT/'src/proximic_ring/assets/stroke-common-characters.json').read_text('utf-8'))['characters']
    for path in sorted((ROOT/'data/stroke-test').glob('2026101[79]*-trial-*.json')):
        report = json.loads(path.read_text('utf-8'))
        # A one-character placeholder or unrelated free writing cannot provide
        # reference labels. The isolated scope-toggle probe is also not a trial.
        if (report.get('contains_manual_strokes') or not report['stroke_attempts']
                or report['target'] == '啊' or report['actual'] == '袅'
                or (not report['completed'] and not report['actual'].startswith(report['target'][:2]))):
            excluded.append(path.name)
            continue
        log = Path(report['raw_log'])
        if log not in cache:
            cache[log] = raw_trials(log)
        scope = None
        with log.open(encoding='utf-8') as stream:
            for line in stream:
                row = json.loads(line)
                if row['kind'] == 'complete_trial':
                    scope = row['candidateScope']
                    break
        assert scope is not None
        allowed = frozenset(table[:scope['allowedCount']]) if report['common_only'] else None
        if report['common_only']:
            assert hashlib.sha256(table[:scope['allowedCount']].encode('utf-8')).hexdigest() == scope['charactersSha256']
        raw = cache[log][report['target'], report['elapsed_s']]
        rows.extend(inspect(report, raw, path.name, dictionary, predictor, corrector, allowed, scope))
        trials.append(dict(file=path.name, target=report['target'], actual=report['actual'],
            strokes=report['stroke_attempts'], elapsed_s=report['elapsed_s'],
            reference_aligned=report['target']==report['actual'],
            correction_enabled=report.get('whole_correction_enabled',False)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    sources = [dict(file=str(p),bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in cache]
    args.output.write_text(json.dumps(dict(trials=trials, excluded=excluded, sources=sources,
        replay_note='Current code and annotations, with verified recorded character scope. Logged candidate snapshots are retained separately; replay is not historical UI ground truth.',
        clear_compositions=rows),ensure_ascii=False,indent=2),encoding='utf-8')
    for r in rows:
        print(json.dumps({k:v for k,v in r.items() if k not in ('history','expected_stroke_evidence','prefix_distances')},ensure_ascii=False))
    print('trials',len(trials),'clears>=5',len(rows))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
