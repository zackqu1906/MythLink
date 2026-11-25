"""Audit captures, cross-check rounds, and build a local experimental model."""
from __future__ import annotations

import argparse
from collections import Counter
from functools import lru_cache
import gzip
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from proximic_ring.touchpad.stroke.stroke_dtw import sequence
from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary
from proximic_ring.touchpad.stroke.stroke_common import common_characters
from proximic_ring.touchpad.stroke.stroke_fuzzy import stroke_evidence
from proximic_ring.touchpad.stroke.stroke_joined import (POLICY, JoinedStrokeModel, merge_joined_candidates,
    personal_joined_model_path, sample_family)
from proximic_ring.touchpad.stroke.stroke_joined_components import component_registry
from selection_capture import integration_error, atomic_save
from build_joined_components import build as build_component_registry
import joined_stroke_collection as profile

EXAMPLES = {'fan': ('反', 'ppzn', 0), 'de': ('的', 'pszhhpzn', 0),
            'le': ('了', 'zs', 0), 'zi': ('子', 'zsh', 0),
            'yi-pie-dian': ('补', 'nzspnsn', 3), 'walk': ('过', 'hsnnzn', 4)}


def training_task(task, *, hou_head_confirmed=False):
    target = task.get('target')
    if target == 'silk-head':
        return dict(task, target='silk-whole', expectedCodes=['zzh'], expectedLogicalStrokes=3)
    if target == 'hou-middle':
        # The archived plan really asked for strokes 2–3. Only correct the
        # label when the writer confirms what was drawn in that session.
        if not hou_head_confirmed:
            return None
        return dict(task, target='fan', expectedCodes=['pp'], expectedLogicalStrokes=2)
    return task


def load_samples(paths, *, hou_head_sessions=()):
    sessions, duplicate_paths, samples = {}, [], []
    for path in paths:
        data = profile.validate_session(json.loads(path.read_text(encoding='utf-8')))
        key = data['sessionId']
        if key in sessions:
            previous = sessions[key]
            if data['tasks'] != previous['tasks'] or data['samples'] != previous['samples']:
                raise ValueError('同一session的记录与导出不一致，请明确选择要使用的文件')
            duplicate_paths.append(str(path))
            continue
        if data['phase'] != 'train': continue
        if len(data['samples']) != len(data['tasks']) or data.get('draft') is not None:
            raise ValueError(f'{path}: 采集尚未完成')
        for sample in data['samples']:
            captured = sample['task']
            task = training_task(captured, hou_head_confirmed=key in hou_head_sessions)
            if task is None:
                continue
            if (integration_error(sample) > 1e-6 or sample['pathLength'] <= 0
                    or not sample.get('quality', {}).get('complete') or sequence([(p['x'],p['y']) for p in sample['points']]) is None):
                raise ValueError(f'{path}: 不完整或无效轨迹，不能直接建模')
            row = dict(sample, task=task, sessionId=key, planVersion=data['planVersion'])
            if task is not captured:
                row['capturedTarget'] = captured['target']
            samples.append(row)
        sessions[key] = data
    if set(hou_head_sessions) - sessions.keys():
        raise ValueError('确认过的后字采集编号不在训练记录里')
    if not samples: raise ValueError('没有完整的训练采集记录')
    return samples, sessions, duplicate_paths


@lru_cache(maxsize=1)
def bundled_templates():
    path = ROOT/'src/proximic_ring/assets/stroke-joined-model.json.gz'
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return json.load(stream)['templates']


def payload_for(samples, positions, source):
    # Targeted recaptures can build a working model without replaying the
    # historical sessions. When raw legacy rounds are supplied, keep their
    # bundled equivalents out of held-out evaluation.
    templates = [] if any(s.get('planVersion', 1) <= 2 for s in samples) else list(bundled_templates())
    for s in samples:
        points = [(p['x'], p['y']) for p in s['points']]
        templates.append(dict(family=sample_family(s), features=sequence(points).tolist(),
            sampleId=f"{s['sessionId']}:{s['taskIndex']}"))
    registry = component_registry()
    return dict(schema='ring-joined-stroke-model', version=2, policy=POLICY,
        source=source, templates=templates, positions=positions,
        componentSource=registry['source'], referenceTemplates=registry['referenceTemplates'])


def evaluate(samples, positions, dictionary):
    details = []
    allowed = common_characters()
    folds = sorted({(s['sessionId'], s['task']['round']) for s in samples})
    if len(folds) < 2: raise ValueError('至少需要两轮数据才能做逐轮留出检查')
    for session, rnd in folds:
        training = [s for s in samples if (s['sessionId'],s['task']['round']) != (session,rnd)]
        model = JoinedStrokeModel(payload_for(training, positions, dict(evaluation='held-out-round')))
        for sample in samples:
            if (sample['sessionId'],sample['task']['round']) != (session,rnd): continue
            options = model.recognize([(p['x'],p['y']) for p in sample['points']])
            truth = sample_family(sample)
            row = dict(sessionId=session, round=rnd, label=sample['task']['label'], family=truth,
                options=options, correctFamilyAvailable=any(o['family'] == truth for o in options))
            if truth != 'single':
                char, code, start = replay_example(sample['task'], truth, positions)
                original = sample['singleStrokeBaseline']
                ink = stroke_evidence(original['category'], original)
                ink['joined_options'] = options
                width = sample['task']['expectedLogicalStrokes']
                evidence = [stroke_evidence(c) for c in code[:start]]+[ink]+[stroke_evidence(c) for c in code[start+width:]]
                normal = dictionary.fuzzy_candidates(evidence, allowed_characters=allowed)
                chars = [r['char'] for r in normal]
                merged, matches = merge_joined_candidates(dictionary,evidence,chars,normal,model,allowed=allowed)
                row.update(replayTarget=char, normalTop5=chars[:5], joinedTop5=merged[:5],
                    top5Before=char in chars[:5], top5After=char in merged[:5],
                    logicalComplete=any(m['char'] == char and m.get('logical_entered') == len(code) for m in matches))
                if truth == 'silk-whole':
                    normal = dictionary.fuzzy_candidates([ink], allowed_characters=allowed)
                    _, direct = merge_joined_candidates(dictionary, [ink], [r['char'] for r in normal], normal,
                        model, allowed=allowed)
                    row['silkStandaloneTop5'] = any(r['char'] == '纟' and r.get('joined_code') == 'zzh'
                                                   for r in direct[:5])
            details.append(row)
    joined = [r for r in details if r['family'] != 'single']
    controls = [r for r in details if r['family'] == 'single']
    return dict(method='Leave one complete round out; same wearer/session, not independent validation. Whole-character replay uses the real joined contact and ideal manual remaining strokes; no language context.',
        joined=len(joined), familyAvailable=sum(r['correctFamilyAvailable'] for r in joined),
        wholeCharacterTop5Before=sum(r['top5Before'] for r in joined),
        wholeCharacterTop5After=sum(r['top5After'] for r in joined),
        silkStandaloneTop5=sum(r.get('silkStandaloneTop5', False) for r in joined),
        controls=len(controls), controlsWithOptionalAlternatives=sum(bool(r['options']) for r in controls),
        controlsWithSilkAlternatives=sum(any(o['family'] == 'silk-whole' for o in r['options']) for r in controls),
        forcedSplits=0, details=details)


def replay_example(task, family, positions):
    if 'contextCharacter' not in task:
        return EXAMPLES[task['target']]
    char = task['contextCharacter']
    for code, spans in positions.get(char, {}).items():
        for span in spans:
            start = span['start']
            if span['family'] == family and code[start:start+span['length']] in task['expectedCodes']:
                return char, code, start
    raise ValueError(f"未找到 {char} 对应 {task['label']} 的连笔位置")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('paths', nargs='*', type=Path)
    parser.add_argument('--confirmed-hou-head-session', action='append', default=[],
                        help='Session ID whose old strokes 2–3 prompts were written as strokes 1–2')
    parser.add_argument('--cache', type=Path, default=ROOT/'tmp'/'left-dot-research')
    output = parser.add_mutually_exclusive_group()
    output.add_argument('--output', type=Path)
    output.add_argument('--activate', action='store_true',
                        help='Write the personal model used by the input method on its next launch')
    parser.add_argument('--report', type=Path, default=ROOT/'data'/'diagnostics'/'joined-strokes'/'model-review.json')
    parser.add_argument('--bundled-output', type=Path,
                        help='Also export normalized templates without session metadata or sample identifiers')
    args = parser.parse_args()
    default_output = ROOT/'data'/'models'/'joined-strokes'/'model.json.gz'
    output_path = personal_joined_model_path() if args.activate else args.output or default_output
    paths = args.paths
    if not paths:
        for path in sorted((ROOT/'data'/'recordings'/'joined-strokes').glob('joined-strokes-*.json')):
            data = json.loads(path.read_text(encoding='utf-8'))
            if data['phase'] == 'train' and len(data['samples']) == len(data['tasks']) and data.get('draft') is None:
                paths.append(path)
    samples, sessions, duplicates = load_samples(paths, hou_head_sessions=args.confirmed_hou_head_session)
    dictionary = StrokeDictionary()
    positions = build_component_registry(args.cache, dictionary)['positions']
    digest = hashlib.sha256(json.dumps(samples, sort_keys=True).encode()).hexdigest()
    source = dict(fingerprint=digest, sessions=list(sessions), rounds=len({(s['sessionId'],s['task']['round']) for s in samples}),
        sampleCount=len(samples), familyCounts=dict(Counter(sample_family(s) for s in samples)),
        labelCorrections=dict(Counter(f"{s['capturedTarget']} → {sample_family(s)}" for s in samples
                                      if 'capturedTarget' in s)),
        dictionarySha256=hashlib.sha256((ROOT/'src/proximic_ring/assets/strokes.json.gz').read_bytes()).hexdigest(),
        positionSources={f:hashlib.sha256((args.cache/f).read_bytes()).hexdigest()
                         for f in ('dictionary.txt','cnchar-orders.json','cnchar-stroke-table.json')})
    report = dict(source=source, duplicatesIgnored=duplicates,
        quality=dict(integrationMaxError=max(integration_error(s) for s in samples),
            saved=len(samples), rejected=sum(len(s['rejected']) for s in sessions.values()),
            interruptions=sum(len(s['interruptions']) for s in sessions.values())),
        annotatedCharacters=len(positions), evaluation=evaluate(samples, positions, dictionary))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = payload_for(samples, positions, source)
    data = gzip.compress(json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8'), mtime=0)
    with tempfile.NamedTemporaryFile(dir=output_path.parent, suffix='.tmp', delete=False) as stream:
        staged = Path(stream.name)
        stream.write(data)
    try:
        staged.replace(output_path)
    finally:
        staged.unlink(missing_ok=True)
    if args.bundled_output is not None:
        bundled = {key: payload[key] for key in ('schema', 'version', 'policy')}
        bundled['source'] = dict(kind='bundled', sampleCount=len(payload['templates']),
            familyCounts=dict(Counter(row['family'] for row in payload['templates'])))
        bundled['templates'] = [dict(family=row['family'], features=row['features']) for row in payload['templates']]
        args.bundled_output.parent.mkdir(parents=True, exist_ok=True)
        args.bundled_output.write_bytes(gzip.compress(json.dumps(bundled, separators=(',', ':'),
            allow_nan=False).encode('utf-8'), mtime=0))
    atomic_save(args.report, report)
    print(json.dumps(dict(source=source, quality=report['quality'], annotatedCharacters=len(positions),
        evaluation={k:v for k,v in report['evaluation'].items() if k != 'details'}), ensure_ascii=True))


if __name__ == '__main__': main()
