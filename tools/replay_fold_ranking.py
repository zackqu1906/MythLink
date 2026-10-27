"""Paired 1–3 stroke replay isolating fine fold ranking, without model calls.

Uses completed automatic trials and their recorded recognition distances.
The reference target is used only to score already-generated candidate lists.
This measures retrospective ranking, not human speed or recognition accuracy.
"""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from evaluate_stroke_llm import load_cases, metrics
from proximic_ring.touchpad.stroke.stroke_common import common_characters
from proximic_ring.touchpad.stroke.stroke_context import StrokeContext
from proximic_ring.touchpad.stroke.stroke_folds import FOLD_POLICY, fold_evidence
from proximic_ring.touchpad.stroke.stroke_fuzzy import FuzzyPolicy
from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary


def replay(records):
    cases, trials, excluded = load_cases(records)
    before, after = StrokeDictionary(fold_orders={}), StrokeDictionary()
    predictor, allowed = StrokeContext(), common_characters()
    rows, timings = [], []
    eligible = [case for case in cases if case['fuzzy'] and
                fold_evidence(case['evidence'], FuzzyPolicy(case['level']))]
    for case in eligible:
        lists = []
        for dictionary in (before, after):
            start = time.perf_counter()
            matches = dictionary.fuzzy_candidates(case['evidence'], predictor=predictor,
                context=case['context'], allowed_characters=allowed, policy=FuzzyPolicy(case['level']))
            elapsed = (time.perf_counter()-start)*1000
            if dictionary is after:
                timings.append(elapsed)
            lists.append([match['char'] for match in matches])
        old, new = lists
        target = case['target']
        rank = lambda chars: chars.index(target)+1 if target in chars else None
        rows.append(dict(trial=case['trial'], code=case['code'], target=target,
            before_rank=rank(old), after_rank=rank(new),
            before_top6=''.join(old[:6]), after_top6=''.join(new[:6])))
    summary = dict(automatic_completed_trials=len(trials), all_short_prefixes=len(cases),
        fold_prefixes=len(rows), scope=len(allowed), before=metrics(rows, 'before_rank'),
        after=metrics(rows, 'after_rank'),
        improved=sum((r['after_rank'] or 999) < (r['before_rank'] or 999) for r in rows),
        worsened=sum((r['after_rank'] or 999) > (r['before_rank'] or 999) for r in rows),
        median_query_ms=statistics.median(timings) if timings else None,
        max_query_ms=max(timings, default=None))
    return dict(description=__doc__, policy=FOLD_POLICY,
        asset_sha256=hashlib.sha256((ROOT/'src/proximic_ring/assets/stroke-folds.json.gz').read_bytes()).hexdigest(),
        summary=summary, trials=trials, excluded=excluded, rows=rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--records', type=Path, default=ROOT/'data/stroke-test')
    parser.add_argument('--output', type=Path, default=ROOT/'data/diagnostics/fold-ranking/replay.json')
    args = parser.parse_args()
    result = replay(args.records)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result['summary'], ensure_ascii=True))
