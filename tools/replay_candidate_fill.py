"""Replay sentence trials: how often is the first page short, and does filling help?

Each state is a written prefix of a committed character, ranked as the complete
test program does (fuzzy matching, components, prefix vectors, whole-character
repair merged in), then topped up by stroke_tolerant.fill_page. The target is
used only to score finished lists. Retrospective ranking, not input speed.
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from evaluate_stroke_llm import load_cases
from proximic_ring.touchpad.stroke.stroke_common import common_characters
from proximic_ring.touchpad.stroke.stroke_context import StrokeContext
from proximic_ring.touchpad.stroke.stroke_fuzzy import FuzzyPolicy
from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary
from proximic_ring.touchpad.stroke.stroke_tolerant import DEFAULT_TOLERANT_POLICY, fill_page, index_for
from proximic_ring.touchpad.stroke.stroke_vectors import StrokeVectorModel
from proximic_ring.touchpad.stroke.stroke_word_correction import WholeCharacterCorrector, merge_candidates


def bucket(k):
    return '1-3' if k <= 3 else '4-6' if k <= 6 else '7+'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--records', type=Path, default=ROOT/'data/stroke-test')
    parser.add_argument('--max-strokes', type=int, default=40)
    parser.add_argument('--output', type=Path, default=ROOT/'data/diagnostics/candidate-fill/replay.json')
    args = parser.parse_args()
    cases, trials, _ = load_cases(args.records, max_strokes=args.max_strokes, vectors=True)
    cases = [case for case in cases if case['fuzzy']]
    dictionary, predictor, allowed = StrokeDictionary(recognizer='dtw'), StrokeContext(), common_characters()
    dictionary.stroke_vectors = StrokeVectorModel()
    corrector = WholeCharacterCorrector(dictionary.entries, dictionary.ranks, allowed_characters=allowed)
    index = index_for(dictionary, allowed)
    stats, timings, rows, changed = defaultdict(Counter), [], [], 0
    for case in cases:
        matches = dictionary.fuzzy_candidates(case['evidence'], predictor=predictor, context=case['context'],
            allowed_characters=allowed, policy=FuzzyPolicy(case['level']))
        chars = [row['char'] for row in matches]
        repairs = corrector.candidates(case['code'], evidence=case['evidence'], context=case['context'],
            predictor=predictor, allowed_characters=allowed, excluded_characters=chars)
        chars = merge_candidates(chars, repairs, reserved=1 if any(r.get('context_extra_slot') for r in matches) else 2)
        start = time.perf_counter()
        filled, filled_rows = fill_page(chars, matches+repairs, index=index, evidence=case['evidence'],
            predictor=predictor, context=case['context'], allowed=allowed, priors=dictionary._fuzzy_priors)
        timings.append((time.perf_counter()-start)*1000)
        changed += filled[:len(chars)] != chars  # existing order must never change
        target, k = case['target'], len(case['code'])
        guess = next((r for r in filled_rows if r['char'] == target and r.get('match_type') == 'tolerant'), None)
        s = stats[bucket(k)]
        s['n'] += 1
        s['short_before'] += len(chars) < 6
        s['empty_before'] += not chars
        s['short_after'] += len(filled) < 6
        s['page_before'] += target in chars[:6]
        s['page_after'] += target in filled[:6]
        s['by_guess'] += guess is not None
        rows.append(dict(trial=case['trial'], target=target, code=case['code'], before=''.join(chars[:6]),
                         after=''.join(filled[:6]), guessed=guess is not None))
    summary = {b: {key: (value if key == 'n' else round(value/s['n'], 4)) for key, value in s.items()}
               for b, s in sorted(stats.items())}
    total = Counter()
    for s in stats.values():
        total.update(s)
    summary['all'] = {key: (value if key == 'n' else round(value/total['n'], 4)) for key, value in total.items()}
    fill_ms = [t for t, r in zip(timings, rows) if r['before'] != r['after']]
    result = dict(description=__doc__, policy=DEFAULT_TOLERANT_POLICY.snapshot(), trials=len(trials),
                  summary=summary, existingOrderChanged=changed,
                  fillMs=dict(median=statistics.median(fill_ms) if fill_ms else None, max=max(fill_ms, default=None),
                              filledStates=len(fill_ms)), rows=rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(dict(summary=summary, existingOrderChanged=changed, fillMs=result['fillMs']), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
