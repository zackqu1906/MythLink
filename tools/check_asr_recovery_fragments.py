#!/usr/bin/env python3
"""Evaluate the joint ASR prompt and its specialist baseline at the same thinking setting."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from tools import voice_eval_dataset as corpus
from tools.check_fragment_completion import PROMPT as FRAGMENT_PROMPT
from tools.check_fragment_completion import RUBRIC as FRAGMENT_RUBRIC
from tools.check_fragment_completion import call, configured_settings, now, sha

DATA = ROOT / 'tools/data'
PROMPTS = {'joint': DATA / 'asr_recovery_fragment_candidates_v2.txt', 'recovery': DATA / 'asr_recovery_prompt_v2.txt',
           'fragment': FRAGMENT_PROMPT}
RUBRIC = ('沿用各专项冻结判据。修复要恢复关键对象或动作及其连带含义，保留限制和指令形态；部分修复不通过。'
          '保护按语义评分并另记逐字率。不可辨识的两项只观察。补全沿用原专项的逐例要求。'
          '分别记录两个条件都通过、仅合并通过、仅专项通过、两个都失败；配对差异不自动归因为功能干扰。')


def inputs_for(case):
    model = corpus.model_input(case)
    screen = model['screen_context']
    if 'asr_intent_recovery' in case['special_annotations']:
        scene, context = screen['scene'], screen['context']
    else:
        scene = '随手记录' if screen['application'] == 'com.apple.Notes' else '对话'
        lines = [screen['conversation_title']] if screen.get('conversation_title') else []
        lines.extend(m['speaker'] + '：' + m['text'] for m in screen['messages'])
        context = '\n'.join(lines)
    return {'asr_final': model['utterance'], 'scene': scene, 'context': context, 'before_cursor': model['editable_text']}


def trials_for(subset):
    rows = []
    for case in subset['cases']:
        annotations = case['special_annotations']
        shared = {'master_id': case['id'], 'input': inputs_for(case)}
        if 'asr_intent_recovery' in annotations:
            for contract in annotations['asr_intent_recovery']:
                rows.append({**shared, 'id': 'asr-v2-' + contract['id'], 'specialist': 'recovery',
                             'group': contract['kind'] + ' · ' + contract['mode'], 'reference': contract['gold'],
                             'requirement': contract['criterion'], 'observation': contract['kind'] == '观察'})
        else:
            contract = annotations['fragment_completion']
            rows.append({**shared, 'id': case['id'], 'specialist': 'fragment',
                         'group': '补全 · ' + contract['group'], 'reference': contract['reference'],
                         'requirement': contract['requirement'], 'observation': False})
    return rows


def freeze(out, settings, joint_prompt, joint_only, thinking='low'):
    master = corpus.load_master()
    filters = json.loads(corpus.DEFAULT_SUBSETS.read_text())['subsets']['asr_recovery_and_fragments']
    subset = corpus.subset_document(master, filters)
    rows = trials_for(subset)
    out.mkdir(parents=True, exist_ok=False)
    prompt_paths = {**PROMPTS, 'joint': joint_prompt}
    for name, path in prompt_paths.items():
        prompt = path.read_text()
        if name == 'fragment':
            # Change only the two input field names, preserving the specialist's instructions.
            prompt = prompt.replace('utterance', 'asr_final').replace('screen_context', 'context')
        (out / (name + '.txt')).write_text(prompt)
    corpus.write_json(out / 'inputs.json', subset)
    corpus.write_json(out / 'trials.json', rows)
    order = list(rows)
    random.Random(1119).shuffle(order)
    schedule = []
    for i, row in enumerate(order):
        pair = ['joint'] if joint_only else ['joint', row['specialist']]
        for name in pair if i % 2 == 0 else reversed(pair):
            schedule.append([row['id'], name])
    files = ['trials.json', 'inputs.json', *(k + '.txt' for k in PROMPTS)]
    candidate_mode = joint_prompt in {PROMPTS['joint'], DATA / 'asr_recovery_fragment_candidates.txt'}
    if candidate_mode:
        (out / 'rubric.txt').write_bytes((DATA / 'asr_fragment_candidate_rubric.txt').read_bytes())
        files.append('rubric.txt')
    corpus.write_json(out / 'plan.json', {
        'candidate_mode': candidate_mode,
        'created_at': now(), 'model': settings.model, 'provider': settings.provider,
        'endpoint': settings.base_url.rstrip('/') + '/responses', 'timeout_s': max(60, settings.timeout_s),
        'concurrency': 4, 'schedule': schedule, 'seed': 1119,
        'thinking': 'disabled' if thinking == 'off' else 'enabled',
        'effort': None if thinking == 'off' else 'low', 'cap': 2048,
        'rubric': RUBRIC, 'fragment_rubric': FRAGMENT_RUBRIC,
        'frozen_sha256': {name: sha(out / name) for name in files},
        'source_sha256': {str(f.relative_to(ROOT)) if f.is_relative_to(ROOT) else str(f): sha(f) for f in [Path(__file__).resolve(),
            ROOT / 'tools/check_fragment_completion.py', ROOT / 'tools/voice_eval_dataset.py',
            ROOT / 'tools/check_llm_edit_router.py', corpus.DEFAULT_SUBSETS, *prompt_paths.values()]},
    })


def run(out, settings):
    plan = json.loads((out / 'plan.json').read_text())
    if settings.model != plan['model'] or settings.base_url.rstrip('/') + '/responses' != plan['endpoint']:
        raise ValueError('Configured destination or model changed')
    if any(sha(out / name) != expected for name, expected in plan['frozen_sha256'].items()):
        raise ValueError('Frozen evidence changed')
    rows = {r['id']: r for r in json.loads((out / 'trials.json').read_text())}
    prompts = {k: (out / (k + '.txt')).read_text() for k in PROMPTS}
    key = settings.api_key.strip() or os.environ[settings.api_key_env].strip()
    journal = out / 'requests.jsonl'
    completed = set()
    if journal.exists():
        completed = {(r['case_id'], r['condition']) for line in journal.read_text().splitlines()
                     if 'transport_error' not in (r := json.loads(line))}
    pending = [(cid, name) for cid, name in plan['schedule'] if (cid, name) not in completed]

    def request(pair):
        cid, name = pair
        condition = {'id': name, 'thinking': plan['thinking'], 'effort': plan['effort'], 'cap': plan['cap'], 'suffix': ''}
        return call(rows[cid], condition, plan, prompts[name], key, model_input=rows[cid]['input'])

    with journal.open('a') as log, ThreadPoolExecutor(max_workers=plan['concurrency']) as pool:
        for i in range(0, len(pending), plan['concurrency']):
            batch = list(pool.map(request, pending[i:i + plan['concurrency']]))
            for r in batch:
                log.write(json.dumps(r, ensure_ascii=False) + '\n')
            log.flush()
            print(f'{min(i + len(batch), len(pending))}/{len(pending)}', flush=True)
            if any('transport_error' in r for r in batch):
                raise SystemExit('Transport failure recorded. Use --resume after resolving it.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--joint-prompt', type=Path, default=PROMPTS['joint'])
    parser.add_argument('--thinking', choices=('off', 'low'), default='low', help='Thinking setting for a new run')
    parser.add_argument('--joint-only', action='store_true', help='Reuse existing specialist runs for a prompt iteration')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--prepare-only', action='store_true')
    mode.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    settings = configured_settings()
    if not args.resume:
        freeze(args.output_dir, settings, args.joint_prompt.resolve(), args.joint_only, args.thinking)
    if not args.prepare_only:
        run(args.output_dir, settings)


if __name__ == '__main__':
    main()
