"""Compare v4d with shared general rules plus its unchanged clarification boundary."""
from __future__ import annotations

import argparse
from collections import Counter
import difflib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from tools import character_clarification_experiment as runner

SOURCE = ROOT / 'docs/reports/character-clarification-2026-11-16'
OUT = ROOT / 'docs/reports/general-character-boundary-2026-11-16'
UNCERTAIN = ['clarify-hol-003', 'clarify-hol-044', 'ocr-modify-ambiguous',
             'synthetic-correction-noisy-spelling']


def freeze():
    if (OUT / 'freeze.json').exists():
        return
    OUT.mkdir(parents=True, exist_ok=True)
    baseline = (SOURCE / 'v4d.txt').read_text()
    from proximic_ring.text_processing.boundary_edit_prompt import BOUNDARY_EDIT_PROMPT
    assert baseline == BOUNDARY_EDIT_PROMPT, 'Baseline must match the current production prompt'
    start = baseline.index('【解释正确写法：统一处理释字、偏旁部首、拆字和拼读】')
    end = baseline.index('【理解目标与相邻行为的边界】')
    context_start = baseline.index('输入 JSON 中 utterance')
    context_end = baseline.index('【最终交付】')
    output_start = baseline.index('只返回已经完成所有有效要求的完整正文')
    candidate = baseline[:end] + baseline[context_start:context_end] + baseline[output_start:]
    dataset_bytes = (SOURCE / 'dataset.json').read_bytes()
    (OUT / 'dataset.json').write_bytes(dataset_bytes)
    (OUT / 'v4d.txt').write_text(baseline)
    (OUT / 'general_char_v1.txt').write_text(candidate)
    (OUT / 'v4d-to-general_char_v1.diff').write_text(''.join(difflib.unified_diff(
        baseline.splitlines(True), candidate.splitlines(True), fromfile='v4d', tofile='general_char_v1')))
    cases = json.loads(dataset_bytes)['cases']
    runner.save(OUT / 'freeze.json', {
        'created_at': runner.now(), 'dataset_sha256': runner.digest(dataset_bytes.decode()),
        'cases': len(cases), 'splits': dict(Counter(c['split'] for c in cases)),
        'repeats': 3, 'planned_generation_requests': len(cases) * 3 * 2,
        'baseline': {'version': 'v4d', 'sha256': runner.digest(baseline), 'characters': len(baseline)},
        'candidate': {'version': 'general_char_v1', 'sha256': runner.digest(candidate), 'characters': len(candidate)},
        'unchanged_clarification_sha256': runner.digest(baseline[start:end]),
        'design': 'Only remove non-clarification detailed sections and the detailed final checklist. '
                  'Retain the exact general rules, clarification section, OCR contract, and output contract. '
                  'Run both arms contemporaneously with paired, randomized order, three repetitions, four workers.',
        'omitted_details': ['相邻行为边界', '自主完成细则', '多项要求组合细则', '撤销细则', '整体与局部保护细则',
                            '定向修改与删除细则', '插入细则', '润色与篇幅细则', '结构与格式细则', '翻译细则',
                            '新内容与创作细则', '分操作最终核对细则'],
        'general_fallback': 'The common general editing instructions remain. Missing means missing detailed rules, not missing permission or task support.',
        'uncertain_case_ids': UNCERTAIN,
        'scoring': 'Freeze the existing correctness-v1 rubric; use fresh blind review with explicit hash-bound Codex audit. '
                   'Known ambiguous cases stay uncertain for both arms; guard cases remain observations.',
        'dataset_limit': 'All cases have been used in earlier experiments. Historical holdout is an existing reserved subset, not a fresh unseen holdout.',
        'production_prompt_changed': False,
    })


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--validate-only', action='store_true')
    args = p.parse_args()
    freeze()
    runner.OUT = OUT
    runner.run(argparse.Namespace(run='paired', suite='all', variants=['v4d', 'general_char_v1'],
                                  repeats=3, workers=4, ids=None, validate_only=args.validate_only))


if __name__ == '__main__':
    main()
