#!/usr/bin/env python3
"""Pair current six-task editing with flat fine-task editing, one HTTP call each.

Run: .runtime/venv/bin/python tools/check_fine_edit_router.py --output docs/reports/fine-edit-run.json
Only existing local fixtures are sent to the application's configured Ark model.
No ASR, live OCR, application settings changes, or input-method writeback.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import random
import sys
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))

from tools.check_routed_edit_current import NATURAL, AMBIGUITY, expectations
from tools.check_llm_edit_router import configured_settings, load_cases, latency_stats, REGRESSION_FILES
from tools.check_automatic_edit_generation import check_output, RecordedResponse
from tools.fine_edit_experiment import TASKS, VERSION, TAXONOMY_PATH, fine_prompt, process_fine
from proximic_ring.text_processing.llm import OpenAICompatibleTextProcessor
from proximic_ring.text_processing.routed_edit_prompts import ROUTED_EDIT_PROMPT_VERSION, single_pass_prompt

LABELS_PATH = ROOT / 'tools/data/fine_edit_expected_tasks.json'


def input_digest(case):
    payload = {k: case[k] for k in ('utterance', 'target', 'context')}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def experiment_cases():
    cases = load_cases(NATURAL) + load_cases(AMBIGUITY, regression=False)
    labels = json.loads(LABELS_PATH.read_text())
    assert labels['taxonomy_version'] == VERSION
    assert len(cases) == len({c['id'] for c in cases})
    assert {c['id'] for c in cases} == set(labels['cases'])
    for case in cases:
        annotation = labels['cases'][case['id']]
        assert annotation['input_sha256'] == input_digest(case), case['id']
        fine = annotation['expected_fine_tasks']
        assert fine is None or (fine and len(set(fine)) == len(fine) and set(fine) <= TASKS.keys())
        assert {TASKS[t]['parent'] for t in (fine or [])} == set(case['expected_tasks'])
        case['expected_fine_tasks'] = fine
        case['annotation_note'] = annotation.get('note', '')
        case['generation_expected'] = expectations(case)
    return cases


def run_case(case, arm, settings):
    record = {'id': case['id'], 'arm': arm, 'calls': [], 'started_at': datetime.now(timezone.utc).isoformat()}
    expected_input = {'utterance': case['utterance'].strip(), 'editable_text': case['target'],
                      'screen_context': case['context']}
    expected_prompt = fine_prompt(case['target']) if arm == 'fine' else single_pass_prompt(case['target'])

    def capture(request, **kwargs):
        if record['calls']:
            raise RuntimeError('A second HTTP request is forbidden')
        payload = json.loads(request.data)
        assert payload['input'][0]['content'][0]['text'] == expected_prompt
        assert json.loads(payload['input'][1]['content'][0]['text']) == expected_input
        assert payload['stream'] is False and payload['thinking'] == {'type': 'disabled'}
        assert 'tools' not in payload
        # Headers and credentials are deliberately never serialized.
        call = {'endpoint': request.full_url, 'request': payload}
        record['calls'].append(call)
        started = time.perf_counter()
        try:
            response = urlopen(request, **kwargs)
            call['http_status'] = response.status
            return RecordedResponse(response, call, started)
        except Exception as exc:
            call.update(http_latency_s=time.perf_counter() - started, error_type=type(exc).__name__)
            raise

    processor = OpenAICompatibleTextProcessor(urlopen=capture)
    started = time.perf_counter()
    try:
        if arm == 'fine':
            final, raw, model_input, tasks = process_fine(processor, case, settings)
            parents = sorted({TASKS[t]['parent'] for t in tasks})
            record['fine_exact'] = set(tasks) == set(case['expected_fine_tasks']) if case['expected_fine_tasks'] else None
        else:
            final, raw, model_input, plan = processor.process_routed_edit_with_trace(
                case['utterance'], settings, case['target'], case['context'])
            tasks = list(plan.tasks)
            parents = tasks
        record.update(final_text=final, raw_response=raw, model_input=model_input, actual_tasks=tasks,
                      mapped_parent_tasks=parents,
                      coarse_exact=set(parents) == set(case['expected_tasks']) if case['cohort'] != 'guard' else None)
        checks = check_output(case['generation_expected'], final)
        record.update(checks=checks, constraint_passed=all(c['passed'] for c in checks) if checks else None,
                      unchanged=final == case['target'])
    except Exception as exc:
        record.update(error=str(exc), error_type=type(exc).__name__,
                      raw_response=str(getattr(exc, 'model_output', '')),
                      coarse_exact=False if case['cohort'] != 'guard' else None)
        if arm == 'fine':
            record['fine_exact'] = False if case['expected_fine_tasks'] else None
    finally:
        record['latency_s'] = time.perf_counter() - started
        processor.close()
    return record


def usage(record):
    return record['calls'][0].get('response', {}).get('usage', {}) if record['calls'] else {}


def score(records):
    valid = [r for r in records if not r.get('error')]
    cached = lambda r: usage(r).get('input_tokens_details', {}).get('cached_tokens', 0) > 0
    return {'n': len(records), 'valid': len(valid), 'errors': len(records) - len(valid),
        'coarse_exact': sum(r.get('coarse_exact') is True for r in records),
        'coarse_scored': sum(r.get('coarse_exact') is not None for r in records),
        'fine_exact': sum(r.get('fine_exact') is True for r in records),
        'fine_scored': sum(r.get('fine_exact') is not None for r in records),
        'constraint_passed': sum(r.get('constraint_passed') is True for r in records),
        'constraint_failed': sum(r.get('constraint_passed') is False for r in records),
        'constraint_unchecked': sum(r.get('constraint_passed') is None for r in records),
        'semantic_review': dict(Counter(r.get('review', {}).get('status', 'pending') for r in records)),
        'latency': latency_stats([r['latency_s'] for r in valid]),
        'latency_cached': latency_stats([r['latency_s'] for r in valid if cached(r)]),
        'latency_uncached': latency_stats([r['latency_s'] for r in valid if not cached(r)]),
        'usage': {'requests': sum(len(r['calls']) for r in records),
                  'input_tokens': sum(usage(r).get('input_tokens', 0) for r in records),
                  'output_tokens': sum(usage(r).get('output_tokens', 0) for r in records),
                  'cached_requests': sum(cached(r) for r in records),
                  'cached_tokens': sum(usage(r).get('input_tokens_details', {}).get('cached_tokens', 0) for r in records)}}


def write_report(report, path, reviews=None):
    pairs = report['pairs']
    if reviews:
        judgments = json.loads(reviews.read_text())
        for pair in pairs:
            for slot, arm in pair['review_slots'].items():
                if pair['case']['id'] in judgments and slot in judgments[pair['case']['id']]:
                    pair['results'][arm]['review'] = judgments[pair['case']['id']][slot]
        report['review_policy'] = 'Codex 本轮离线逐条复核，展示时隐藏方案名称、分类和延迟；非独立人工盲评，非用户满意度。pass/fail/uncertain/observation 分开。'
    groups = {'edit_162': lambda c: c['cohort'] != 'guard',
              'existing_150': lambda c: c['cohort'] in ('regression', 'fresh'),
              **{g: lambda c, g=g: c['cohort'] == g for g in ('regression', 'fresh', 'ambiguity', 'guard')}}
    report['metrics'] = {g: {arm: score([p['results'][arm] for p in pairs if select(p['case']) and arm in p['results']])
                            for arm in ('coarse', 'fine')} for g, select in groups.items()}
    transitions = Counter()
    for pair in pairs:
        if pair['case']['cohort'] == 'guard':
            continue
        a, b = [pair['results'].get(arm, {}).get('review', {}).get('status', 'pending') for arm in ('coarse', 'fine')]
        transitions[a + ' -> ' + b] += 1
    report['semantic_transitions'] = dict(transitions)
    report['fine_task_coverage'] = {task: {
        'expected_cases': sum(task in (p['case']['expected_fine_tasks'] or []) for p in pairs),
        'tp': sum(task in (p['case']['expected_fine_tasks'] or []) and task in p['results'].get('fine', {}).get('actual_tasks', []) for p in pairs),
        'fp': sum(p['case']['expected_fine_tasks'] is not None and task not in p['case']['expected_fine_tasks'] and task in p['results'].get('fine', {}).get('actual_tasks', []) for p in pairs),
        'fn': sum(task in (p['case']['expected_fine_tasks'] or []) and task not in p['results'].get('fine', {}).get('actual_tasks', []) for p in pairs)} for task in TASKS}
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    blind = []
    for pair in pairs:
        c = pair['case']
        blind.append({'id': c['id'], 'cohort': c['cohort'], 'utterance': c['utterance'], 'target': c['target'],
            'context': c['context'], 'expected': c['generation_expected'], 'annotation_note': c['annotation_note'],
            **{slot: {'text': pair['results'][arm].get('final_text'), 'error': pair['results'][arm].get('error')}
               for slot, arm in pair['review_slots'].items() if arm in pair['results']}})
    path.with_suffix('.blind.jsonl').write_text(''.join(json.dumps(c, ensure_ascii=False) + '\n' for c in blind))
    e = lambda x: html.escape(str(x))
    body = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>扁平细分类配对实验</title>',
        '<style>body{font:15px/1.65 system-ui;max-width:1280px;margin:30px auto;padding:0 20px;color:#172438}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f5f8;padding:12px}article{border:1px solid #cdd6df;border-radius:10px;padding:20px;margin:18px 0}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.bad{border-left:5px solid #cd493b}summary{cursor:pointer}button{padding:8px;margin:6px}td,th{border:1px solid #ddd;padding:8px}table{border-collapse:collapse}@media(max-width:700px){.grid{grid-template-columns:1fr}}</style>',
        '<h1>六类单次调用 vs 22 个平级任务单次调用</h1><p>' + e(report['scope']) + '</p>',
        '<p>模型：' + e(report['model']) + '。两组同一输入、参数和输出预算，顺序执行、配对交替，无重试。延迟包含网络与完整返回，非首 token 延迟。</p>',
        '<p>' + e(report.get('review_policy', '语义复核尚未完成。')) + '</p>',
        '<p>细分类定义和标签在本轮请求前冻结，但根据已有开发样例设计，不是全新留出测试。分类精确率与正文正确率分开；旧 guard 不计编辑正确率。此实验比较整套提示词，未隔离类别数量与规则文字的影响。</p>',
        '<p><a href="' + e(path.name) + '">完整请求、响应和指标 JSON</a></p>',
        '<details open><summary>指标与修复／退化</summary><pre>' + e(json.dumps({'metrics': report['metrics'], 'semantic_transitions': report['semantic_transitions'], 'fine_task_coverage': report['fine_task_coverage']}, ensure_ascii=False, indent=2)) + '</pre></details>',
        '<button onclick="document.querySelectorAll(\'article\').forEach(x=>x.hidden=false)">全部</button><button onclick="document.querySelectorAll(\'article\').forEach(x=>x.hidden=!x.classList.contains(\'bad\'))">仅失败／待确认／分类不匹配</button>']
    for p in pairs:
        c = p['case']
        bad = any(r.get('error') or r.get('coarse_exact') is False or r.get('fine_exact') is False or r.get('review', {}).get('status') in ('fail', 'uncertain') for r in p['results'].values())
        body.append('<article id="' + e(c['id']) + '" class="' + ('bad' if bad else '') + '"><h3>' + e(c['id']) + '</h3><p>' + e(c['utterance']) + '</p><strong>原文</strong><pre>' + e(c['target'] or '【空】') + '</pre><div class="grid">')
        for arm, title in (('coarse', '当前六类'), ('fine', '扁平细分类')):
            r = p['results'].get(arm, {})
            body.append('<div><strong>' + title + '</strong><p>' + e(r.get('actual_tasks', [])) + ' · ' + e(round(r.get('latency_s', 0), 3)) + 's</p><pre>' + e(r.get('final_text', r.get('error', '未完成'))) + '</pre><p>' + e(r.get('review', {'status': 'pending'})) + '</p></div>')
        body.append('</div><details><summary>完整输入、分类预期、约束和记录</summary><pre>' + e(json.dumps(p, ensure_ascii=False, indent=2)) + '</pre></details></article>')
    body.append('</html>')
    path.with_suffix('.html').write_text(''.join(body))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--case', action='append')
    parser.add_argument('--seed', type=int, default=1111)
    parser.add_argument('--validate-only', action='store_true')
    parser.add_argument('--report-only', action='store_true')
    parser.add_argument('--reviews', type=Path)
    args = parser.parse_args()
    if args.report_only:
        write_report(json.loads(args.output.read_text()), args.output, args.reviews)
        return
    cases = experiment_cases()
    if args.case:
        assert set(args.case) <= {c['id'] for c in cases}
        cases = [c for c in cases if c['id'] in args.case]
    for case in cases:
        fine_prompt(case['target'])
    if args.validate_only:
        print('VALIDATED', len(cases), 'cases;', len(TASKS), 'flat tasks;', VERSION)
        return
    if args.output.exists() or args.output.with_suffix('.jsonl').exists():
        raise ValueError('Refusing to overwrite an existing run')
    settings = configured_settings()
    rng = random.Random(args.seed)
    rng.shuffle(cases)
    sources = [Path(__file__), ROOT / 'tools/fine_edit_experiment.py', TAXONOMY_PATH, LABELS_PATH,
               ROOT / 'tools/check_routed_edit_current.py', ROOT / 'tools/check_llm_edit_router.py',
               ROOT / 'tools/check_automatic_edit_generation.py', NATURAL, AMBIGUITY,
               *(ROOT / 'tools/data' / f for f in REGRESSION_FILES),
               *sorted((ROOT / 'src/proximic_ring/text_processing').glob('*.py'))]
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'model': settings.model,
        'seed': args.seed, 'timeout_s': settings.timeout_s, 'source_sha256': hashes,
        'versions': {'coarse': ROUTED_EDIT_PROMPT_VERSION, 'fine': VERSION},
        'scope': '既有 150 条编辑回归＋12 条省略／含糊指令＋6 条旧边界观察；离线合成文本，经当前配置的真实服务生成。未测 ASR、实时 OCR、输入法写回或新用户分布。',
        'prompts': {arm: {kind: fn(target) for kind, target in [('nonempty', '原文'), ('empty', '')]}
                    for arm, fn in [('coarse', single_pass_prompt), ('fine', fine_prompt)]}, 'pairs': []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    consecutive_transport_failures = 0
    try:
        with args.output.with_suffix('.jsonl').open('x') as journal:
            for i, case in enumerate(cases):
                arms = ['coarse', 'fine'] if i % 2 == 0 else ['fine', 'coarse']
                slots = ['coarse', 'fine']
                rng.shuffle(slots)
                pair = {'case': case, 'order': arms, 'review_slots': dict(zip(('A', 'B'), slots)), 'results': {}}
                report['pairs'].append(pair)
                for arm in arms:
                    result = run_case(case, arm, settings)
                    pair['results'][arm] = result
                    journal.write(json.dumps(result, ensure_ascii=False) + '\n')
                    journal.flush()
                    print(f'{i + 1}/{len(cases)} {arm} {case["id"]} coarse={result.get("coarse_exact")} fine={result.get("fine_exact")} error={result.get("error_type", "none")} {result["latency_s"]:.3f}s', flush=True)
                    failed = result.get('error') and not any('response' in call for call in result['calls'])
                    consecutive_transport_failures = consecutive_transport_failures + 1 if failed else 0
                    if consecutive_transport_failures >= 3:
                        raise RuntimeError('Stopped after three consecutive transport/pre-request failures')
    finally:
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        report['completed_all'] = len(report['pairs']) == len(cases) and all(len(p['results']) == 2 for p in report['pairs'])
        report['sources_unchanged'] = all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == digest for p, digest in hashes.items())
        write_report(report, args.output)


if __name__ == '__main__':
    main()
