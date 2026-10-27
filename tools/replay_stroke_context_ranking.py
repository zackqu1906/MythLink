"""Paired ranking replay; reference characters are used only after candidates exist.

Uses recorded recognition results (including their historical personalization),
not re-recognized trajectories. Synchronous correction ranks are counterfactual
and must not be presented as historical screen contents or input-speed gains.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from evaluate_stroke_llm import raw_trials
import proximic_ring.touchpad.stroke.stroke_input as stroke_input
import proximic_ring.touchpad.stroke.stroke_word_correction as correction
from proximic_ring.touchpad.stroke.stroke_context import StrokeContext, ranking_snapshot
from proximic_ring.touchpad.stroke.stroke_fuzzy import FuzzyPolicy, stroke_evidence


def candidate_order(case, dictionary, predictor, corrector, allowed, *, baseline):
    previous = (stroke_input.FUZZY_LANGUAGE_WEIGHT, stroke_input.promote_completed_fuzzy,
                correction.CORRECTION_LANGUAGE_WEIGHT, dictionary._fold_orders)
    if baseline:
        dictionary._fold_orders = {}
        stroke_input.FUZZY_LANGUAGE_WEIGHT = 1.
        stroke_input.promote_completed_fuzzy = lambda rows, evidence, predictor, context, limit: rows[:limit]
        correction.CORRECTION_LANGUAGE_WEIGHT = .35
    try:
        if case['fuzzy']:
            normal = dictionary.fuzzy_candidates(case['evidence'], predictor=predictor,
                context=case['context'], allowed_characters=allowed, policy=case['policy'])
            characters = [row['char'] for row in normal]
            reserved = 1 if any(row.get('context_extra_slot') for row in normal) else 2
        else:
            characters = dictionary.candidates(case['code'], allowed_characters=allowed)
            reserved = 2
        repairs = corrector.candidates(case['code'], evidence=case['evidence'],
            context=case['context'], predictor=predictor, allowed_characters=allowed,
            excluded_characters=characters) if case['correction'] else []
        return correction.merge_candidates(characters, repairs, reserved=reserved)
    finally:
        (stroke_input.FUZZY_LANGUAGE_WEIGHT, stroke_input.promote_completed_fuzzy,
         correction.CORRECTION_LANGUAGE_WEIGHT, dictionary._fold_orders) = previous


def cases(report, raw):
    events = [e for e in report['events'] if e['kind']=='stroke']
    if len(events)!=len(raw) or any(e['recognized']!=r['result']['category'] for e,r in zip(events,raw)):
        raise ValueError('recorded recognition/event mismatch')
    text, ink, index = '', [], 0
    for event in report['events']:
        kind = event['kind']
        if kind=='stroke':
            row = raw[index]; index += 1
            policy = FuzzyPolicy(row.get('fuzzy_policy', {}).get('level', 50))
            ink.append(stroke_evidence(event['recognized'], row['result'], policy=policy,
                                      metrics=row.get('kinematics')))
            code = ''.join(item['category'] for item in ink)
            if code!=row['code'] or event['stroke_index']!=len(ink)-1:
                raise ValueError('unreconstructable composition')
            target = None
            if report['target'].startswith(text) and len(text)<len(report['target']):
                target = report['target'][len(text)]
            yield dict(code=code, evidence=list(ink), policy=policy, context=text,
                       elapsed_s=round(event['elapsed_s'], 3), target=target,
                       fuzzy=row.get('fuzzy_matching', True),
                       correction=report.get('whole_correction_enabled', False))
        elif kind=='undo_stroke': ink=ink[:-1]
        elif kind=='clear_strokes': ink=[]
        elif kind=='commit' and event.get('delivered'):
            text += event['character']; ink=[]
        elif kind=='delete_character': text=text[:-1]
    if text!=report['actual']:
        raise ValueError('reconstructed text mismatch')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, action='append')
    parser.add_argument('--all', action='store_true', help='Replay aligned sentence reports, excluding manual input')
    parser.add_argument('--output', type=Path, default=ROOT/'data/diagnostics/context-ranking/replay.json')
    args=parser.parse_args()
    paths=args.report or list((ROOT/'data/stroke-test').glob('*-trial-*.json'))
    if not args.report and not args.all:
        paths=[max(paths,key=lambda path:path.stat().st_mtime)]
    dictionary, predictor = stroke_input.StrokeDictionary(), StrokeContext()
    table=json.loads((ROOT/'src/proximic_ring/assets/stroke-common-characters.json').read_text('utf-8'))['characters']
    cache, sources, trials, excluded = {}, [], [], []
    for path in sorted(paths):
        report=json.loads(path.read_text('utf-8'))
        if (report.get('contains_manual_strokes') or len(report['target'])<2 or not report['stroke_attempts']
                or not report['actual'].startswith(report['target'][:2])):
            excluded.append(dict(file=path.name, reason='not an aligned, automatic sentence trial'))
            continue
        log=Path(report['raw_log'])
        if log not in cache:
            scope=None
            with log.open(encoding='utf-8') as stream:
                for line in stream:
                    record=json.loads(line)
                    if record['kind']=='complete_trial':
                        scope=record.get('candidateScope'); break
            cache[log]=(raw_trials(log),scope)
            sources.append(dict(file=str(log),bytes=log.stat().st_size,
                sha256=hashlib.sha256(log.read_bytes()).hexdigest()))
        logs, scope=cache[log]
        raw=logs.get((report['target'],report['elapsed_s']))
        if raw is None:
            excluded.append(dict(file=path.name,reason='raw trial not present')); continue
        allowed=None
        if report.get('common_only'):
            if scope is None:
                excluded.append(dict(file=path.name,reason='recorded scope unavailable')); continue
            characters=table[:scope['allowedCount']]
            if hashlib.sha256(characters.encode('utf-8')).hexdigest()!=scope['charactersSha256']:
                raise ValueError('recorded character scope mismatch')
            allowed=frozenset(characters)
        corrector=correction.WholeCharacterCorrector(dictionary.entries,dictionary.ranks,allowed_characters=allowed)
        rows=[]
        for case in cases(report,raw):
            old=candidate_order(case,dictionary,predictor,corrector,allowed,baseline=True)
            new=candidate_order(case,dictionary,predictor,corrector,allowed,baseline=False)
            target=case['target']  # Assessment begins after both candidate lists have been generated.
            rank=lambda values:values.index(target)+1 if target in values else None
            rows.append(dict(context=case['context'],code=case['code'],target=target,
                elapsed_s=case['elapsed_s'],before_top6=''.join(old[:6]),after_top6=''.join(new[:6]),
                before_rank=rank(old),after_rank=rank(new)))
        scored=[row for row in rows if row['target']]
        summary=dict(samples=len(scored),before_top6=sum(0<(r['before_rank'] or 999)<=6 for r in scored),
                     after_top6=sum(0<(r['after_rank'] or 999)<=6 for r in scored),
                     gained_top6=sum((r['before_rank'] or 999)>6 and (r['after_rank'] or 999)<=6 for r in scored),
                     lost_top6=sum((r['before_rank'] or 999)<=6 and (r['after_rank'] or 999)>6 for r in scored))
        trials.append(dict(file=path.name,target=report['target'],actual=report['actual'],summary=summary,rows=rows))
        print(json.dumps(dict(file=path.name,**summary),ensure_ascii=False),flush=True)
    totals=Counter()
    for trial in trials: totals.update(trial['summary'])
    result=dict(policy=ranking_snapshot(),note=__doc__,sources=sources,trials=trials,excluded=excluded,totals=dict(totals))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(totals=totals,output=str(args.output)),ensure_ascii=False))


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
