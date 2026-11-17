#!/usr/bin/env python3
"""Freeze inputs, then evaluate the current single-call application entry point."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))
from tools.check_routed_edit_current import (
    AMBIGUITY, NATURAL, REGRESSION_FILES, configured_settings, expectations, load_cases, run_case,
)
from proximic_ring.text_processing.routed_edit_prompts import ROUTED_EDIT_PROMPT_VERSION, single_pass_prompt

EXTRA = ROOT / 'tools/data/manual_routed_edit_v5_combination_cases.json'


def cases_for_run(extra=EXTRA):
    cases = load_cases(NATURAL) + load_cases(AMBIGUITY, regression=False) + load_cases(extra, regression=False)
    if len({c['id'] for c in cases}) != len(cases):
        raise ValueError('Duplicate case IDs across datasets')
    for case in cases:
        case['generation_expected'] = expectations(case)
        case['input_sha256'] = hashlib.sha256(json.dumps(
            {k: case[k] for k in ('utterance', 'target', 'context')},
            ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        single_pass_prompt(case['target'])
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--extra', type=Path, default=EXTRA)
    parser.add_argument('--case', action='append')
    parser.add_argument('--seed', type=int, default=111105)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    cases = cases_for_run(args.extra)
    if args.case:
        if set(args.case) - {c['id'] for c in cases}:
            raise ValueError('Unknown case IDs')
        cases = [c for c in cases if c['id'] in args.case]
    print(json.dumps({'cases': len(cases), 'cohorts': dict(Counter(c['cohort'] for c in cases)),
        'task_counts': dict(Counter(len(c['expected_tasks']) for c in cases)),
        'prompt_version': ROUTED_EDIT_PROMPT_VERSION}, ensure_ascii=False), flush=True)
    if args.validate_only:
        return
    journal = args.output.with_suffix('.jsonl')
    manifest = args.output.with_suffix('.inputs.json')
    if any(p.exists() for p in (args.output, journal, manifest)):
        raise ValueError('Refusing to overwrite a recorded run')
    settings = configured_settings()
    random.Random(args.seed).shuffle(cases)
    sources = [Path(__file__), ROOT / 'tools/check_routed_edit_current.py',
        ROOT / 'tools/check_llm_edit_router.py', ROOT / 'tools/check_automatic_edit_generation.py',
        NATURAL, AMBIGUITY, args.extra, *(ROOT / 'tools/data' / f for f in REGRESSION_FILES),
        *sorted((ROOT / 'src/proximic_ring/text_processing').glob('*.py'))]
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'model': settings.model,
        'prompt_version': ROUTED_EDIT_PROMPT_VERSION, 'timeout_s': settings.timeout_s,
        'seed': args.seed, 'planned_cases': len(cases), 'source_sha256': hashes,
        'scope': '168 条既有开发样例及 48 条新增多类别组合；既有 6 条 guard 单列观察。真实服务单次请求，未测音频、屏幕采集、输入法写回。',
        'prompts': {'nonempty': single_pass_prompt('原文'), 'empty': single_pass_prompt('')},
        'cases': []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({'created_at': report['created_at'], 'cases': cases}, ensure_ascii=False, indent=2) + '\n')
    report['input_manifest'] = {'file': manifest.name, 'sha256': hashlib.sha256(manifest.read_bytes()).hexdigest()}
    consecutive_failures = 0
    try:
        with journal.open('x') as stream:
            for i, case in enumerate(cases, 1):
                result = run_case(case, settings)
                report['cases'].append(result)
                stream.write(json.dumps(result, ensure_ascii=False) + '\n')
                stream.flush()
                print(f'{i}/{len(cases)} {case["id"]} route={result.get("route_exact")} error={result.get("error_type", "none")} {result["latency_s"]:.3f}s', flush=True)
                transport_failure = bool(result.get('error')) and not any('response' in c for c in result['calls'])
                consecutive_failures = consecutive_failures + 1 if transport_failure else 0
                if consecutive_failures >= 3:
                    raise RuntimeError('Stopped after three consecutive pre-response failures')
    finally:
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        report['completed_all'] = len(report['cases']) == len(cases)
        report['sources_unchanged'] = all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == h for p, h in hashes.items())
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
