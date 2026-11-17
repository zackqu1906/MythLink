#!/usr/bin/env python3
"""Evaluate Bailian SystemOne with the unchanged six-task router and frozen labels.

Credentials stay in the user-supplied CSV; only request bodies and responses are saved.
The historical Ascend endpoint is never called. Run prepare, probe, then run.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import statistics
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib import request as urlrequest
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from proximic_ring.text_processing.intent_router import ClefIntentRouter, _NoRedirect, MAX_RESPONSE_BYTES
from proximic_ring.text_processing.router_prompts import ROUTER_PROMPT_VERSION, routing_schema, joint_router_input
from proximic_ring.text_processing.task_routing import EDIT_TASK_LABELS, router_input
from clef_cases import load_cases, raw_joint_match

BASELINE = ROOT / 'docs/reports/clef-correction-v20-combined-2026-11-06.json'
SOURCES = [ROOT / 'docs/reports/clef-six-tasks-cases-2026-11-04.json',
           ROOT / 'tools/data/clef_correction_boundary_cases.json']
CODE = [Path(__file__), *(ROOT / 'src/proximic_ring/text_processing' / name for name in
        ('intent_router.py', 'router_prompts.py', 'task_routing.py'))]
MODEL = 'decision-model-preview'


def now():
    return datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def timing(values):
    values = sorted(values)
    if not values:
        return {'n': 0}
    return {'n': len(values), 'mean_s': statistics.mean(values),
            'median_s': statistics.median(values),
            **{f'p{p}_s': values[math.ceil(p / 100 * len(values)) - 1] for p in (50, 90, 95, 99)},
            'min_s': values[0], 'max_s': values[-1]}


def metrics(rows):
    good = [r for r in rows if not r.get('error')]
    edits = [r for r in rows if r['expected']['task'] == 'edit']
    dictates = [r for r in rows if r['expected']['task'] == 'dictation']
    result = {'n': len(rows), 'errors': len(rows)-len(good),
              'root_correct': sum(bool(r.get('root_correct')) for r in rows),
              'exact_correct': sum(bool(r.get('exact_match')) for r in rows),
              'raw_exact_correct': sum(bool(r.get('raw_exact_match')) for r in rows),
              'edit_n': len(edits), 'edit_exact_correct': sum(bool(r.get('exact_match')) for r in edits),
              'dictation_n': len(dictates), 'dictation_correct': sum(bool(r.get('exact_match')) for r in dictates),
              'warnings': sum(bool(r.get('trace', {}).get('warning')) for r in rows),
              'latency': timing([r['trace']['latency_s'] for r in good]),
              'per_class': {}}
    for task in EDIT_TASK_LABELS:
        tp = fp = fn = 0
        for r in rows:
            expected = task in r['expected'].get('tasks', [])
            actual = task in r.get('actual', {}).get('tasks', [])
            tp += expected and actual
            fp += not expected and actual
            fn += expected and not actual
        result['per_class'][task] = {'support': tp+fn, 'tp': tp, 'fp': fp, 'fn': fn,
            'precision': tp/(tp+fp) if tp+fp else None,
            'recall': tp/(tp+fn) if tp+fn else None,
            'f1': 2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None}
    sums = {k: sum(x[k] for x in result['per_class'].values()) for k in ('tp', 'fp', 'fn')}
    result['micro_f1'] = 2*sums['tp']/(2*sums['tp']+sums['fp']+sums['fn'])
    result['macro_f1'] = statistics.mean(x['f1'] for x in result['per_class'].values() if x['f1'] is not None)
    return result


def prepare(out):
    if (out / 'manifest.json').exists():
        return json.loads((out / 'manifest.json').read_text())
    out.mkdir(parents=True, exist_ok=True)
    cases = [c for source in SOURCES for c in load_cases(source, require_plan=True)]
    assert len(cases) == len({c['id'] for c in cases}) == 154
    historical = json.loads(BASELINE.read_text())
    lookup = {r['id']: r for r in historical['cases']}
    paired = []
    for case in cases:
        request = {'model': MODEL,
                   'state': joint_router_input(router_input(case['utterance'], case['target'], case['context'])),
                   'questions': routing_schema(target=case['target'])}
        old = lookup[case['id']]
        assert request['state'] == old['trace']['input_state']
        assert request['questions'] == old['trace']['request_schema']
        assert case['expected'] == old['expected']
        assert 1 <= len(request['questions']) <= 16
        assert all(q['type'] == 'choice' and isinstance(q['criteria'], dict) for q in request['questions'].values())
        case['request'] = request
        paired.append(old)
    manifest = {'created_at': now(), 'model': MODEL, 'prompt_version': ROUTER_PROMPT_VERSION,
        'source_sha256': {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in [*SOURCES, BASELINE, *CODE]},
        'case_count': len(cases), 'question_counts': dict(Counter(len(c['request']['questions']) for c in cases)),
        'method': '串行；每例一次；无重试；现有客户端新建 HTTP 连接；先单独做一次接口预检，预检不计分。',
        'baseline': {'file': str(BASELINE.relative_to(ROOT)), 'created_at': historical['created_at'],
                     'same_state_schema_and_labels': True, 'metrics': metrics(paired)},
        'scope': '仅六类可组合任务分类及总体听写/编辑判断，不运行 ASR、OCR 采集、正文生成或写回。',
        'dataset_note': '154 条合成/脱敏开发回归例（102 编辑、52 听写），非独立测试集；排除两条真实交互记录。',
        'cases': cases}
    write_json(out / 'manifest.json', manifest)
    write_json(out / 'baseline-matched.json', {'source': manifest['baseline'], 'cases': paired})
    return manifest


class Capture:
    def __init__(self, secrets):
        self.opener = urlrequest.build_opener(_NoRedirect())
        self.secrets = secrets
        self.evidence = {}

    def __call__(self, request, timeout):
        self.evidence['request_body'] = json.loads(request.data)
        with self.opener.open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            self.evidence['http_status'] = response.status
        # Do not persist an unexpected response that echoes a credential.
        decoded = raw.decode('utf-8', errors='replace')
        if any(secret and secret in decoded for secret in self.secrets):
            raise ValueError('Response unexpectedly contains credentials; response not saved')
        self.evidence['response_text'] = decoded
        try:
            self.evidence['response_json'] = json.loads(raw)
        except (ValueError, UnicodeError):
            pass
        return io.BytesIO(raw)


def run_one(case, router, capture):
    capture.evidence = {}
    row = {k: v for k, v in case.items() if k != 'request'}
    row['started_at'] = now()
    started = time.perf_counter()
    try:
        decision, trace_text = router.classify(case['utterance'], case['target'], case['context'])
        row['trace'] = json.loads(trace_text)
        row['actual'] = {'task': decision.task, 'tasks': list(decision.edit_plan.tasks) if decision.edit_plan else []}
        row['root_correct'] = row['trace']['raw_model_task'] == case['expected']['task']
        row['exact_match'] = (decision.task == case['expected']['task'] and
                             set(row['actual']['tasks']) == set(case['expected'].get('tasks', [])))
        row['raw_exact_match'] = raw_joint_match(row['trace'], case['expected'])
    except Exception as exc:
        row.update(error=str(exc), root_correct=False, exact_match=False, raw_exact_match=False)
        row['trace'] = json.loads(exc.model_output) if getattr(exc, 'model_output', '') else {'latency_s': time.perf_counter()-started}
    row['evidence'] = capture.evidence
    assert capture.evidence.get('request_body') == case['request'], 'Frozen request changed'
    row['finished_at'] = now()
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['prepare', 'probe', 'run'])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--credentials', type=Path)
    args = parser.parse_args()
    manifest = prepare(args.output)
    if args.phase == 'prepare':
        print(json.dumps({k: v for k, v in manifest.items() if k != 'cases'}, ensure_ascii=False))
        return 0
    for path, expected_hash in manifest['source_sha256'].items():
        assert digest((ROOT/path).read_bytes()) == expected_hash, f'Source changed: {path}'
    with args.credentials.open(encoding='utf-8-sig', newline='') as f:
        credentials = dict(csv.reader(f))
    endpoint = credentials['openAiCompatible'].rstrip('/') + '/systemone'
    url = urlsplit(endpoint)
    assert url.scheme == 'https' and url.hostname.endswith('.cn-beijing.maas.aliyuncs.com')
    assert not url.username and not url.password and not url.query and not url.fragment
    capture = Capture([credentials['apiKey']])
    router = ClefIntentRouter(endpoint=endpoint, model=MODEL, api_key=credentials['apiKey'], timeout_s=45, urlopen=capture)
    if args.phase == 'probe':
        path = args.output / 'probe.json'
        if path.exists():
            raise ValueError('Probe already saved; inspect it instead of repeating')
        row = run_one(manifest['cases'][0], router, capture)
        write_json(path, row)
        print(json.dumps({'probe_ok': not row.get('error'), 'error': row.get('error'),
              'latency_s': row['trace']['latency_s'], 'actual': row.get('actual'),
              'response_keys': list(row['evidence'].get('response_json', {}))}, ensure_ascii=False))
        return int(bool(row.get('error')))
    probe = json.loads((args.output/'probe.json').read_text())
    assert not probe.get('error'), 'Successful interface probe required'
    records_path = args.output/'records.jsonl'
    if records_path.exists():
        raise ValueError('Run already exists; no implicit reruns')
    report = {'created_at': now(), 'endpoint': endpoint, 'model': MODEL,
              'prompt_version': manifest['prompt_version'], 'cases': [], 'complete': False}
    try:
        with records_path.open('x') as log:
            for index, case in enumerate(manifest['cases'], 1):
                row = run_one(case, router, capture)
                log.write(json.dumps(row, ensure_ascii=False)+'\n'); log.flush()
                report['cases'].append(row)
                print(json.dumps({'completed': index, 'total': len(manifest['cases']), 'id': row['id'],
                    'exact': row['exact_match'], 'latency_s': round(row['trace']['latency_s'], 4),
                    'error': row.get('error')}, ensure_ascii=False), flush=True)
                if len(report['cases']) >= 3 and all(r.get('error') for r in report['cases'][-3:]):
                    report['stop_reason'] = 'three_consecutive_request_errors'
                    break
    finally:
        router.close()
        report['finished_at'] = now()
        report['complete'] = len(report['cases']) == len(manifest['cases'])
        report['metrics'] = metrics(report['cases'])
        report['sources_unchanged'] = all(digest((ROOT/p).read_bytes()) == h for p,h in manifest['source_sha256'].items())
        write_json(args.output/'results.json', report)
    print(json.dumps({'complete': report['complete'], 'metrics': report['metrics']}, ensure_ascii=False))
    return int(not report['complete'] or bool(report['metrics']['errors']))


if __name__ == '__main__':
    raise SystemExit(main())
