#!/usr/bin/env python3
"""Freeze the fragment-completion corpus and compare thinking controls on Ark Responses."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from tools import voice_eval_dataset as corpus
from tools.check_llm_edit_router import configured_settings

PROMPT = ROOT / 'tools/data/fragment_completion_prompt_v1.txt'
SHORT_HINT = '请尽快完成判断，思考控制在 32 个 token 以内，不展开比较多个方案。'
# The order is intentional. The small-cap probe repeats exactly four designated inputs.
CONDITIONS = [
    ('off', '关闭思考', 'disabled', None, '', 2048),
    ('high', '开启思考 · high', 'enabled', 'high', '', 2048),
    ('low', '开启思考 · low', 'enabled', 'low', '', 2048),
    ('hint', 'high 加 32-token 文字提示', 'enabled', 'high', SHORT_HINT, 2048),
    ('cap', 'high 加 64-token 总上限', 'enabled', 'high', '', 64),
]
PROBE = {'fragment-001', 'fragment-011', 'fragment-018', 'fragment-023'}
RUBRIC = ('逐条判断正文能否直接使用、忠实传达口述并满足该例的语义要求。允许自然省略、等义改写和多种可接受回复，'
          '不按参考答案逐字匹配。四组配对另要求明示题目指定的动作或对象，以验证确实消解了指代。'
          '无依据的立场、承诺、具体事实、对象错误或遗漏必要要点判失败。冗长到不像日常回复或附带解释也判失败，'
          '未设置机械字数限制。缺少完整可用正文计作请求未完成，进入该条件通过率分母。')


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze(out, settings):
    master = corpus.load_master()
    subset = corpus.subset_document(master, {'kind': 'single', 'tags': ['fragment_completion']})
    out.mkdir(parents=True, exist_ok=False)
    prompt = PROMPT.read_text().strip()
    (out / 'prompt.txt').write_text(prompt + '\n')
    corpus.write_json(out / 'inputs.json', subset)
    case_ids = [case['id'] for case in subset['cases']]
    conditions = [dict(id=k, name=name, thinking=mode, effort=effort, suffix=suffix, cap=cap,
                       ids=[cid for cid in case_ids if k != 'cap' or cid in PROBE])
                  for k, name, mode, effort, suffix, cap in CONDITIONS]
    corpus.write_json(out / 'plan.json', {
        'created_at': now(), 'provider': settings.provider, 'endpoint': settings.base_url.rstrip('/') + '/responses',
        'model': settings.model, 'concurrency': 3, 'timeout_s': max(60, settings.timeout_s),
        'api': 'Responses, non-streaming', 'conditions': conditions, 'rubric': RUBRIC,
        'planned_calls': sum(len(c['ids']) for c in conditions),
        'source_sha256': {str(p.relative_to(ROOT)): sha(p) for p in (
            Path(__file__).resolve(), PROMPT, ROOT / 'tools/voice_eval_dataset.py', ROOT / 'tools/check_llm_edit_router.py')},
        'prompt_sha256': sha(out / 'prompt.txt'), 'inputs_sha256': sha(out / 'inputs.json'),
    })


def call(case, condition, plan, prompt, key, *, model_input=None):
    if model_input is None:
        model_input = corpus.model_input(case)
    system = prompt if not condition['suffix'] else prompt + '\n' + condition['suffix']
    body = {'model': plan['model'], 'stream': False, 'thinking': {'type': condition['thinking']},
            'max_output_tokens': condition['cap'], 'input': [
                {'role': role, 'content': [{'type': 'input_text', 'text': value}]} for role, value in
                [('system', system), ('user', json.dumps(model_input, ensure_ascii=False))]]}
    if condition['effort']:
        body['reasoning'] = {'effort': condition['effort']}
    request = Request(plan['endpoint'], data=json.dumps(body, ensure_ascii=False).encode(),
                      headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    record = {'case_id': case['id'], 'condition': condition['id'], 'started_at': now(),
              'request': body, 'input_sha256': corpus.fingerprint(model_input)}
    start = time.perf_counter()
    try:
        with urlopen(request, timeout=plan['timeout_s']) as reply:
            raw = json.load(reply)
            record['http_status'] = reply.status
        record['response'] = raw
        record['output'] = ''.join(part['text'] for item in raw.get('output', []) if item.get('type') == 'message'
                                   for part in item.get('content', []) if part.get('type') == 'output_text')
        if raw.get('status') != 'completed' or not record['output'].strip():
            record['completion_error'] = f"{raw.get('status')} / {raw.get('incomplete_details') or '无可用正文'}"
    except (OSError, ValueError) as exc:
        record['transport_error'] = str(exc)
        if isinstance(exc, HTTPError):
            record.update(http_status=exc.code, error_body=exc.read().decode(errors='replace')[:2000])
    record.update(finished_at=now(), latency_s=time.perf_counter() - start)
    return record


def execute(out, settings):
    plan = json.loads((out / 'plan.json').read_text())
    if settings.base_url.rstrip('/') + '/responses' != plan['endpoint'] or settings.model != plan['model']:
        raise ValueError('Configured endpoint or model changed after freeze')
    if sha(out / 'prompt.txt') != plan['prompt_sha256'] or sha(out / 'inputs.json') != plan['inputs_sha256']:
        raise ValueError('Frozen inputs or prompt changed')
    prompt = (out / 'prompt.txt').read_text().strip()
    cases = {c['id']: c for c in json.loads((out / 'inputs.json').read_text())['cases']}
    key = settings.api_key.strip() or os.environ[settings.api_key_env].strip()
    journal = out / 'requests.jsonl'
    done = set()
    if journal.exists():
        done = {(r['condition'], r['case_id']) for line in journal.read_text().splitlines()
                if 'transport_error' not in (r := json.loads(line))}
    with journal.open('a', encoding='utf-8') as log:
        for condition in plan['conditions']:
            pending = [cases[cid] for cid in condition['ids'] if (condition['id'], cid) not in done]
            for offset in range(0, len(pending), plan['concurrency']):
                # Finish and journal the batch before issuing new requests.
                batch = pending[offset:offset + plan['concurrency']]
                with ThreadPoolExecutor(max_workers=len(batch)) as pool:
                    jobs = [pool.submit(call, case, condition, plan, prompt, key) for case in batch]
                    results = [job.result() for job in jobs]
                for r in results:
                    log.write(json.dumps(r, ensure_ascii=False) + '\n')
                    print(r['condition'], r['case_id'], r.get('transport_error') or r.get('completion_error') or 'completed',
                          f"{r['latency_s']:.2f}s", flush=True)
                log.flush()
                if any('transport_error' in r for r in results):
                    raise SystemExit('Transport failure recorded. Resolve and use --resume to retry missing calls.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--prepare-only', action='store_true')
    action.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    settings = configured_settings()
    if not args.resume:
        freeze(args.output_dir, settings)
    if not args.prepare_only:
        execute(args.output_dir, settings)


if __name__ == '__main__':
    main()
