#!/usr/bin/env python3
"""Matched, randomized fine-rule ablation through the actual application entry.

Freeze once, then journal every request. Resume only the uncompleted request IDs;
never replace a failed or completed generation with a more convenient result.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import sys
import threading
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from tools.check_boundary_only_edit import frozen_cases, MANIFEST, model_input
from tools.check_llm_edit_router import configured_settings
from tools.check_automatic_edit_generation import RecordedResponse, check_output
from proximic_ring.text_processing.general_edit_prompt import (
    GENERAL_EDIT_CORE, GENERAL_EDIT_VARIANTS, FINE_EDIT_RULES, FINE_EDIT_LABELS,
    GENERAL_EDIT_PROMPT_VERSION, general_edit_prompt)
from proximic_ring.text_processing.llm import OpenAICompatibleTextProcessor

SEED = 20261116


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def json_digest(value):
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True))


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def now():
    return datetime.now(timezone.utc).isoformat()


def repeated_subset(cases):
    """Select before inference: six per priority stratum, no result-based selection."""
    rng = random.Random(SEED)
    eligible = [c for c in cases if c['cohort'] != 'guard']
    rng.shuffle(eligible)
    predicates = [
        lambda c: any(s in c['utterance'] for s in ('字旁', '拼写', '的榆', '科学的科')),
        lambda c: 'polish' in c['expected_tasks'],
        lambda c: 'format' in c['expected_tasks'],
        lambda c: True,
    ]
    chosen = []
    for predicate in predicates:
        candidates = [c for c in eligible if c not in chosen and predicate(c)]
        chosen.extend(candidates[:6])
    assert len(chosen) == 24
    return [c['id'] for c in chosen]


def freeze(out, workers):
    cases = frozen_cases()
    repeats = repeated_subset(cases)
    rng = random.Random(SEED)
    blocks = []
    for repeat in range(1, 4):
        selected = [c for c in cases if repeat == 1 or c['id'] in repeats]
        rng.shuffle(selected)
        for case in selected:
            variants = list(GENERAL_EDIT_VARIANTS)
            rng.shuffle(variants)
            blocks.append({'case_id': case['id'], 'repeat': repeat, 'variants': variants})
    sources = [Path(__file__), *sorted((ROOT/'src/proximic_ring/text_processing').glob('*.py')),
               ROOT/'tools/data/edit_review_correctness_v1.txt',
               ROOT/'tools/check_boundary_only_edit.py', ROOT/'tools/check_llm_edit_router.py',
               ROOT/'tools/check_automatic_edit_generation.py']
    manifest = {
        'schema_version': 1, 'created_at': now(), 'version': GENERAL_EDIT_PROMPT_VERSION,
        'dataset_source': str(MANIFEST.relative_to(ROOT)), 'dataset_sha256': sha(MANIFEST),
        'dataset_note': '既有合成开发集；fresh/holdout 名称不代表本次未见数据。guard 为观察例。',
        'cases': cases, 'repeated_subset': repeats, 'blocks': blocks, 'workers': workers,
        'seed': SEED, 'seed_scope': 'case/variant order only, not sent to model',
        'planned_requests': sum(len(b['variants']) for b in blocks),
        'core': GENERAL_EDIT_CORE, 'fine_rules': FINE_EDIT_RULES, 'rule_labels': FINE_EDIT_LABELS,
        'variants': {v: {'label': label, 'missing': list(missing), 'prompt': general_edit_prompt(v),
                         'prompt_sha256': digest(general_edit_prompt(v))}
                     for v, (label, missing) in GENERAL_EDIT_VARIANTS.items()},
        'rubric': (ROOT/'tools/data/edit_review_correctness_v1.txt').read_text(),
        'source_sha256': {str(p.relative_to(ROOT)): sha(p) for p in sources},
        'design': '同期按样例配对，各 block 内随机排列 7 版本串行；固定 4 workers（或显式 workers 参数）并行 block。'
                  '主表仅第 1 次的全量 210 个计分案例；6 guard 观察例不计准确率。24 条额外 2 次仅作稳定性附表。'
                  '对照 general_full 是共同通用底座加全部 7 组精细块，不是历史 v3 原文。'
                  '所有删减组仅删除完整块；不改口述、不做链式编辑、无预处理和自动重试。'
                  '延迟为应用入口到正文校验结束的墙钟时间；不含 ASR/OCR/写回，不控制服务端缓存。',
    }
    out.mkdir(parents=True, exist_ok=True)
    save(out/'manifest.json', manifest)
    for v, spec in manifest['variants'].items():
        (out/(v+'.txt')).write_text(spec['prompt'])
    (out/'general-core.txt').write_text(GENERAL_EDIT_CORE)
    return manifest


def trial_id(case_id, repeat, variant):
    return f'{case_id}__r{repeat}__{variant}'


def run_one(case, variant, repeat, settings):
    expected = model_input(case)
    record = {'id': trial_id(case['id'], repeat, variant), 'case_id': case['id'],
              'variant': variant, 'repeat': repeat, 'started_at': now(),
              'input_sha256': json_digest(expected), 'model_input': expected, 'calls': []}

    def capture(request, **kwargs):
        if record['calls']:
            raise RuntimeError('Multiple calls forbidden')
        payload = json.loads(request.data)
        assert payload['input'][0]['content'][0]['text'] == general_edit_prompt(variant)
        assert json.loads(payload['input'][1]['content'][0]['text']) == expected
        assert payload['model'] == settings.model
        call = {'endpoint': request.full_url, 'request': payload}
        record['calls'].append(call)
        start = time.perf_counter()
        try:
            response = urlopen(request, **kwargs)
            call['http_status'] = response.status
            return RecordedResponse(response, call, start)
        except Exception as exc:
            call.update(http_latency_s=time.perf_counter()-start, error_type=type(exc).__name__)
            raise

    processor = OpenAICompatibleTextProcessor(urlopen=capture)
    start = time.perf_counter()
    try:
        body, raw, actual, plan = processor.process_routed_edit_with_trace(
            case['utterance'], settings, case['target'], case['context'], variant=variant)
        assert actual == expected and plan is None and len(record['calls']) == 1
        record.update(final_text=body, raw_response=raw, output_sha256=digest(body),
                      checks=check_output(case['generation_expected'], body))
    except Exception as exc:
        record.update(error=str(exc) or type(exc).__name__, error_type=type(exc).__name__,
                      final_text='', output_sha256=digest(''),
                      raw_response=str(getattr(exc, 'model_output', '')))
    finally:
        record['latency_s'] = time.perf_counter()-start
        record['finished_at'] = now()
        processor.close()
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--validate-only', action='store_true')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--block-limit', type=int)
    args = parser.parse_args()
    out = args.output_dir
    if (out/'manifest.json').exists():
        if not args.resume:
            raise ValueError('Existing run: use --resume, never overwrite')
        manifest = json.loads((out/'manifest.json').read_text())
        assert manifest['workers'] == args.workers
        assert all(sha(ROOT/p) == value for p, value in manifest['source_sha256'].items())
    else:
        manifest = freeze(out, args.workers)
    print(json.dumps({'cases': len(manifest['cases']), 'variants': len(manifest['variants']),
                      'requests': manifest['planned_requests'], 'repeat_cases': 24}), flush=True)
    if args.validate_only:
        return
    settings = configured_settings()
    journal = out/'generations.jsonl'
    records = [json.loads(s) for s in journal.read_text().splitlines()] if journal.exists() else []
    done = {r['id'] for r in records}
    assert len(done) == len(records)
    cases = {c['id']: c for c in manifest['cases']}
    blocks = [b for b in manifest['blocks'] if any(trial_id(b['case_id'], b['repeat'], v) not in done for v in b['variants'])]
    if args.block_limit:
        blocks = blocks[:args.block_limit]
    lock = threading.Lock()
    stop = threading.Event()
    errors_in_row = 0
    session = {'started_at': now(), 'model': settings.model, 'base_url': settings.base_url,
               'workers': args.workers, 'timeout_s': settings.timeout_s, 'initial_completed': len(done)}
    with (out/'sessions.jsonl').open('a') as f:
        f.write(json.dumps(session)+'\n')
    with journal.open('a') as stream:
        def run_block(block):
            nonlocal errors_in_row
            for v in block['variants']:
                if stop.is_set():
                    return
                if trial_id(block['case_id'], block['repeat'], v) in done:
                    continue
                result = run_one(cases[block['case_id']], v, block['repeat'], settings)
                with lock:
                    stream.write(json.dumps(result, ensure_ascii=False)+'\n')
                    stream.flush()
                    records.append(result)
                    errors_in_row = errors_in_row+1 if result.get('error') and not any(c.get('response') for c in result['calls']) else 0
                    if errors_in_row >= 3:
                        stop.set()
                    if len(records) % 28 == 0 or result.get('error'):
                        print(f'{len(records)}/{manifest["planned_requests"]} {result["id"]} {result["latency_s"]:.3f}s error={result.get("error_type", "none")}', flush=True)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for future in as_completed([pool.submit(run_block, b) for b in blocks]):
                future.result()
    save(out/'run-status.json', {'finished_at': now(), 'completed': len(records),
         'complete': len(records)==manifest['planned_requests'], 'errors': sum(bool(r.get('error')) for r in records),
         'source_drift': [p for p, value in manifest['source_sha256'].items() if sha(ROOT/p)!=value]})


if __name__ == '__main__':
    main()
