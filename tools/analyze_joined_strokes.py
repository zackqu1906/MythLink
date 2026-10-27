"""Audit joined-stroke recordings without fitting a model or changing labels."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from selection_capture import atomic_save, integration_error
import joined_stroke_collection as profile


def summarize(paths):
    sessions, seen = [], set()
    groups = defaultdict(list)
    for path in paths:
        session = profile.validate_session(json.loads(Path(path).read_text(encoding='utf-8')))
        if session['sessionId'] in seen:
            raise ValueError('同一个sessionId出现多次；请只选择原记录或导出副本中的一份。')
        seen.add(session['sessionId'])
        sessions.append(dict(path=str(Path(path).resolve()), sessionId=session['sessionId'],
            participant=session['participant'], phase=session['phase'], planVersion=session['planVersion'],
            saved=len(session['samples']), planned=len(session['tasks']),
            draft=session.get('draft') is not None, rejected=len(session.get('rejected', [])),
            interruptions=len(session.get('interruptions', []))))
        for sample in session['samples']:
            if integration_error(sample) > 1e-6:
                raise ValueError(f'{path}: 轨迹与原始位移不一致。')
            task = sample['task']
            key = (session['phase'], task['kind'], task.get('target', task['label']))
            groups[key].append(sample)
    rows = []
    for (phase, kind, target), samples in sorted(groups.items()):
        predictions = [s.get('singleStrokeBaseline') for s in samples]
        measured = [s for s in samples if 'singleStrokeBaseline' in s]
        row = dict(phase=phase, kind=kind, target=target, count=len(samples),
            byVariant=dict(Counter(s['task'].get('variant', 'natural') for s in samples)),
            medianDurationMs=statistics.median(s['durationMs'] for s in samples),
            medianPathLength=statistics.median(s['pathLength'] for s in samples),
            stationary=sum(s['pathLength'] == 0 for s in samples),
            sdkClickVerdicts=sum(s.get('sdkClickVerdict') is True for s in samples),
            baselineMeasured=len(measured),
            baselineShapes=dict(Counter(p['shape'] if p else 'unrecognized' for p in predictions)))
        if kind == 'joined':
            row['note'] = '现有单笔模型每条轨迹最多输出一笔；本报告不宣称已实现两笔恢复。'
        else:
            row['baselineCategoryCorrect'] = sum(
                bool(s['singleStrokeBaseline']) and s['singleStrokeBaseline']['category'] in s['task']['expectedCodes']
                for s in measured)
        rows.append(row)
    return dict(schema='ring-joined-stroke-audit', version=1, sessions=sessions, groups=rows,
        note='训练与独立验证分开统计；仅检查采集质量和已保存的单笔基线，不训练、不调阈值。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('paths', nargs='*', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT/'data'/'diagnostics'/'joined-strokes'/'audit.json')
    args = parser.parse_args()
    paths = args.paths or sorted((ROOT/'data'/'recordings'/'joined-strokes').glob('joined-strokes-*.json'))
    if not paths:
        parser.error('尚未找到采集记录；请先运行collect-joined-strokes.cmd。')
    try:
        report = summarize(paths)
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.error(str(error))
    atomic_save(args.output, report)
    print(f"Audited {len(report['sessions'])} sessions; report: {args.output}")


if __name__ == '__main__':
    main()
