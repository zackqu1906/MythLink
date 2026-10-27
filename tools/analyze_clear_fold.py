"""Replay confirmed traces without using task labels to alter recognition."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from clear_fold_collection import validate_session, predict_trace
from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary
from proximic_ring.touchpad.stroke.stroke_fuzzy import stroke_evidence, SHORT_TAIL_RESCUES
from proximic_ring.touchpad.stroke.stroke_clear_review import snapshot as clear_review_snapshot
from proximic_ring.touchpad.stroke.stroke_test_gestures import DELETE, DELETION_SOURCE

UNCERTAIN_SHAPES = {'斜钩', '弯钩', '卧钩'}


def analyze(path):
    data = validate_session(json.loads(path.read_text(encoding='utf-8')))
    dictionary = StrokeDictionary(recognizer='dtw')
    rows = []
    for sample in data['samples']:
        task = sample['task']
        points = [(p['x'], p['y']) for p in sample['points']]
        decision = predict_trace(sample)
        action = decision['action']
        row = dict(taskIndex=sample['taskIndex'], label=task['label'], kind=task['kind'],
                   expectedShape=task.get('shape'), expectedCategory=task.get('category'),
                   variant=task.get('variant'), action=action,
                   baselineAction=decision.get('baselineAction',action),
                   recordedAction=sample.get('gesturePrediction',{}).get('action'),
                   clearReview=decision.get('clearReview'),
                   deleteGesture=decision.get('deleteGesture'),
                   labelUncertain=task.get('shape') in UNCERTAIN_SHAPES)
        if task['kind'] == 'stroke':
            result = dictionary.recognize(points)
            row['recognition'] = result
            row['evidence'] = stroke_evidence(result['category'], result) if result else None
        rows.append(row)
    reliable = [r for r in rows if r['kind'] == 'stroke' and not r['labelUncertain']]
    strict = sum(r['recognition'] is not None and
                 r['recognition']['category'] == r['expectedCategory'] for r in reliable)
    covered = sum(r['evidence'] is not None and
                  r['expectedCategory'] in r['evidence']['costs'] for r in reliable)
    actions = Counter((r['kind'], r['action']) for r in rows)
    confusion = Counter((r['expectedShape'], r['recognition']['shape']) for r in reliable
                        if r['recognition'] and r['expectedShape'] != r['recognition']['shape'])
    return dict(source=str(path.resolve()), sourceSha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                sessionId=data['sessionId'], phase=data['phase'],
                confirmed=len(rows), planned=len(data['tasks']), rejected=len(data['rejected']),
                interruptions=len(data['interruptions']),
                interruptionReasons=dict(Counter(r.get('reason') for r in data['interruptions'])),
                uncertainLabels=sorted(UNCERTAIN_SHAPES),
                uncertainSamples=sum(r['labelUncertain'] for r in rows),
                fuzzyRuleSha256=hashlib.sha256((ROOT/'src/proximic_ring/touchpad/stroke/stroke_fuzzy.py').read_bytes()).hexdigest(),
                shortTailRescues=SHORT_TAIL_RESCUES,
                clearReviewPolicy=clear_review_snapshot(),
                deletionPolicy=dict(source=DELETION_SOURCE,parameters=dict(DELETE)),
                clearReviewProtected=[r['taskIndex'] for r in rows
                                      if r['clearReview'] and r['clearReview']['protect']],
                reliableStrokeSamples=len(reliable), strictCategoryCovered=strict,
                fuzzyCategoryCovered=covered,
                coverageScope='Isolated handwriting replay; control gestures may intercept a trace first. '
                              'Provisional labels; single recorded session, not independent accuracy.',
                actionCounts=[dict(kind=k, action=a, count=n) for (k,a),n in actions.items()],
                shapeConfusions=[dict(expected=e, observed=o, count=n) for (e,o),n in confusion.most_common()],
                clearFalsePositives=[dict(taskIndex=r['taskIndex'], shape=r['expectedShape'],
                                         variant=r['variant']) for r in rows
                                    if r['kind']=='stroke' and r['action']=='clear-all'],
                rows=rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('recording', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT/'data/diagnostics/clear-fold/replay.json')
    args = parser.parse_args()
    report = analyze(args.recording)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('rows','shapeConfusions')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
