#!/usr/bin/env python3
"""Evaluate post-orientation-fix stroke labels; extract structure without changing recognition."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import json
import hashlib
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from proximic_ring.stroke_input import StrokeDictionary


def simplify(points, tolerance):
    if len(points) < 3:
        return points
    a, b = points[0], points[-1]
    dx, dy = b[0]-a[0], b[1]-a[1]
    denominator = dx*dx+dy*dy
    def distance(point):
        t = max(0., min(1., ((point[0]-a[0])*dx+(point[1]-a[1])*dy)/denominator)) if denominator else 0.
        return math.hypot(point[0]-a[0]-t*dx, point[1]-a[1]-t*dy)
    largest, index = max((distance(p), i) for i,p in enumerate(points[1:-1],1))
    if largest <= tolerance:
        return [a,b]
    return simplify(points[:index+1],tolerance)[:-1]+simplify(points[index:],tolerance)


def path_features(points):
    clean = []
    for p in points:
        p = tuple(map(float,p))
        if not all(math.isfinite(v) for v in p):
            raise ValueError('Non-finite trajectory')
        if not clean or p != clean[-1]:
            clean.append(p)
    if len(clean)<2:
        return None
    length = sum(math.dist(a,b) for a,b in zip(clean,clean[1:]))
    reduced = simplify(clean, length*.02)
    turns = []
    for a,b,c in zip(reduced,reduced[1:],reduced[2:]):
        u=(b[0]-a[0],b[1]-a[1]); v=(c[0]-b[0],c[1]-b[1])
        norm=math.hypot(*u)*math.hypot(*v)
        if norm:
            turns.append(dict(angle_deg=math.degrees(math.acos(max(-1.,min(1.,(u[0]*v[0]+u[1]*v[1])/norm)))),
                              incoming=u,outgoing=v))
    return dict(points=len(points),length=length,
                endpoint_ratio=math.dist(clean[0],clean[-1])/length,
                width=max(p[0] for p in clean)-min(p[0] for p in clean),
                height=max(p[1] for p in clean)-min(p[1] for p in clean),
                simplified=reduced,turns=turns,
                note='2% path-length simplification is diagnostic only; not a stroke decision rule')


def load_labels(directory):
    accepted, excluded = [], []
    for path in sorted(Path(directory).glob('*.jsonl')):
        rows=[]
        for line in path.read_text(encoding='utf-8').splitlines():
            try:rows.append(json.loads(line))
            except json.JSONDecodeError:continue
        header=rows[0] if rows else {}
        reason=('empty' if not rows else 'synthetic' if header.get('synthetic')
                else 'before_orientation_fix' if header.get('version')!=3 else None)
        if reason:
            excluded.append(dict(file=str(path),reason=reason));continue
        correction_path=path.with_suffix('.labels.json')
        correction=json.loads(correction_path.read_text(encoding='utf-8')) if correction_path.exists() else {}
        for row in rows:
            if row['kind']=='annotation':
                value=dict(row,file=str(path))
                if 'target' in row:
                    target=dict(row['target'])
                    target['shape']=correction.get('shape_aliases',{}).get(target['shape'],target['shape'])
                    target['variant']=correction.get('variant_aliases',{}).get(target['variant'],target['variant'])
                    value.update(target=target,original_target=row['target'])
                accepted.append(value)
    return accepted, excluded


def evaluate(directory):
    labels, excluded=load_labels(directory)
    dictionary=StrokeDictionary()
    groups=defaultdict(lambda:dict(correct=0,wrong=0,ambiguous=0,category_correct=0,confusion=Counter()))
    details=[]
    for row in labels:
        target=row['target']; group=groups[target['shape']]
        group[{1:'correct',2:'wrong',3:'ambiguous'}[row['rating']]]+=1
        if row['rating']==3:
            continue
        value=row['value']; prediction=dictionary.recognize(value['points'])
        if prediction:
            group['category_correct']+=prediction['category']==target['category']
            group['confusion'][prediction['shape']]+=1
        details.append(dict(file=row['file'],trial_index=row['trial_index'],target=target,
                            original_target=row.get('original_target',target),
                            rating=row['rating'],saved_prediction=value['result'],
                            current_prediction=prediction,features=path_features(value['points'])))
    for group in groups.values():
        n=group['correct']+group['wrong']
        group['manual_accuracy']=group['correct']/n if n else None
        group['category_accuracy']=group['category_correct']/n if n else None
    return dict(label_count=len(labels),usable_count=len(details),excluded=excluded,
                shapes=dict(groups),samples=details,
                note='Human ratings concern exact stroke shape. Category accuracy alone hides 撇点/横折 confusion.')


def compare(directory):
    """Fixed 1-7 development / 8-10 holdout comparison on identical paths."""
    labels, excluded = load_labels(directory)
    dictionary = StrokeDictionary()
    groups = {}
    details = []
    for row in labels:
        if row['rating'] == 3:
            continue
        target = row['target']
        partition = 'development' if target['round'] <= 7 else 'holdout'
        key = (partition, target['shape'])
        group = groups.setdefault(key, dict(count=0, baseline_shape=0, new_shape=0,
            baseline_category=0, new_category=0, improved=0, regressed=0))
        baseline = dictionary.recognize_template(row['value']['points'])
        current = dictionary.recognize(row['value']['points'])
        old_ok = bool(baseline and baseline['shape'] == target['shape'])
        new_ok = bool(current and current['shape'] == target['shape'])
        group['count'] += 1
        group['baseline_shape'] += old_ok
        group['new_shape'] += new_ok
        group['baseline_category'] += bool(baseline and baseline['category'] == target['category'])
        group['new_category'] += bool(current and current['category'] == target['category'])
        group['improved'] += new_ok and not old_ok
        group['regressed'] += old_ok and not new_ok
        details.append(dict(file=row['file'], trial_index=row['trial_index'], target=target,
            partition=partition, baseline=baseline, current=current))
    hashes = {name: hashlib.sha256((ROOT/'src/proximic_ring'/name).read_bytes()).hexdigest()
              for name in ('stroke_input.py', 'stroke_structure.py')}
    return dict(protocol='Rules fixed using rounds 1-7 before evaluating rounds 8-10. '
        'Exact target shape and five-stroke category are scored independently; '
        'old manual accept/reject ratings are not applied to new predictions.',
        code_sha256=hashes, excluded=excluded,
        partitions={partition: {shape: group for (part, shape), group in groups.items() if part == partition}
                    for partition in ('development', 'holdout')}, samples=details)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,default=ROOT/'data/stroke-collection')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--compare',action='store_true',help='Compare original and structure recognizers by fixed rounds')
    args=parser.parse_args()
    result=compare(args.directory) if args.compare else evaluate(args.directory)
    path=args.output or args.directory/('recognition-'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.json')
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    if args.compare:
        for partition, shapes in result['partitions'].items():
            print(partition)
            for shape, group in shapes.items():
                print(shape, group)
        print('报告：'+str(path))
        return 0
    print(f"正式标注 {result['label_count']} 笔，可评估 {result['usable_count']} 笔。")
    if not result['usable_count']:
        print('暂无正确佩戴后的有效样本，请运行 scripts/collect-stroke-samples.cmd 重新采集。')
    for shape,group in result['shapes'].items():
        print(shape,group)
    print('报告：'+str(path))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
