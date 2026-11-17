#!/usr/bin/env python3
"""Apply explicit, hash-bound offline review decisions and render both versions.

This script assembles judgments authored by a reviewer. It is not a semantic
judge and never calls an inference service.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.eval_report import render, validate, metrics, seconds


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def apply_decision(review, decision):
    result = deepcopy(review)
    if decision:
        if review['output_sha256'] != decision['output_sha256'] or review['status'] != decision['previous_status']:
            raise ValueError('Decision is not bound to the previous output/review')
        for key in ('status', 'note', 'issues', 'assessments'):
            if key in decision:
                result[key] = deepcopy(decision[key])
    result['review_scope'] = decision['action'] if decision else 'previous_unaffected_review_retained'
    result['rubric_version'] = 'correctness-v1-2026-11-12'
    return result


def expected(criteria):
    lines = ['中文姓名：' + '、'.join(criteria['names']), criteria['facts']]
    if criteria['english']:
        lines.append('除保留的中文姓名外，正文译为英文。')
    if criteria['polite']:
        lines.append('礼貌表达，按整段含义判断是否保留要求。')
    if criteria['reminder']:
        lines.append('包含提交前核对数据的提醒。')
    if criteria['numbered']:
        lines.append(f'按事项排成 {criteria["numbered"]} 条编号。')
    return '\n'.join(lines)


def load_run(entry, folder, rubric_hash):
    raw_path = folder / entry['raw_file']
    old_path = folder / entry['previous_reviews_file']
    if sha(raw_path) != entry['raw_sha256'] or sha(old_path) != entry['previous_reviews_sha256']:
        raise ValueError('Historical source changed')
    raw = json.loads(raw_path.read_text())
    old = json.loads(old_path.read_text())
    if old['source_sha256'] != sha(raw_path) or not raw['completed_all'] or raw['changed_sources']:
        raise ValueError('Incomplete or unbound historical run')
    manifest = folder / raw['input_manifest']['file']
    if sha(manifest) != raw['input_manifest']['sha256']:
        raise ValueError('Input manifest changed')
    decisions = {(d['id'], d['step']): d for d in entry['decisions']}
    if len(decisions) != len(entry['decisions']):
        raise ValueError('Duplicate decision')
    visited = set()
    cases, new_reviews, original_cases, step_rows = [], {}, [], []
    is_ablation = entry['family'] == 'ablation'
    version = entry['version']
    for case in sorted(raw['cases'], key=lambda c: c['id']):
        cid = case['id']
        previous = old['cases'][cid]
        originals = previous['steps'] if is_ablation else [previous]
        results = case['results'] if is_ablation else [case]
        if len(originals) != len(results):
            raise ValueError('Step coverage changed')
        revised, details = [], []
        for step, (source, review) in enumerate(zip(results, originals, strict=True), 1):
            if review['output_sha256'] != digest(source['final_text']) or review['input_sha256'] != source['input_sha256']:
                raise ValueError(f'{cid}: review hash mismatch')
            key = (cid, step)
            decision = decisions.get(key)
            if review['status'] in ('fail', 'uncertain', 'pending') and not decision:
                raise ValueError(f'{cid}: nonpassing output was not rechecked')
            if decision:
                visited.add(key)
            current = apply_decision(review, decision)
            revised.append(current)
            if is_ablation:
                dummy = dict(id=cid, group=case['family'], instruction=source['instruction'],
                             source_text=source['source_text'], expected_result=expected(source['criteria']),
                             output=source['final_text'], review=current, assessments=current['assessments'])
                validate({'schema_version': 1, 'evaluation': {'classification': False}, 'cases': [dummy]})
                details.append({'title': f'第 {step} 步 · {current["status"]}',
                                'text': '口述：' + source['instruction'] + '\n原文：' + source['source_text'] +
                                        '\n输出：' + source['final_text'] + '\n复核：' + current['note']})
                if len(results) > 1:
                    step_rows.append([version, cid, step, current['status'], current['note']])
        final, prior = revised[-1], originals[-1]
        if final['output_sha256'] != digest(case['final_text']):
            raise ValueError('Final output changed')
        new_reviews[cid] = {'steps': revised, 'final': deepcopy(final)} if is_ablation else final
        details.append({'title': '旧口径判定（仅供对照，不参与当前评分）',
                        'text': json.dumps(previous, ensure_ascii=False, indent=2)})
        scope = '复查原失败／待确认' if any((cid, i + 1) in decisions for i in range(len(results))) else '沿用未受影响的原判定'
        group = case['family'] if is_ablation else case['cohort']
        item = dict(id=f'{version}/{cid}', group=f'{version}/{group}',
                    instruction='\n\n'.join(f'第 {i} 次请求：{s["instruction"]}' for i, s in enumerate(case['steps'], 1)) if is_ablation else case['utterance'],
                    source_text=case['source_text'] if is_ablation else case['target'],
                    context=case.get('context', {}),
                    expected_result=expected(case['steps'][-1]['criteria']) if is_ablation else '完成本次口述的有效要求；按本报告完整复核规则检查明确错误、遗漏及违令。\n' + case['utterance'],
                    output=case['final_text'], latency_s=case['latency_s'], error=case.get('error', ''),
                    observation=False if is_ablation else case['cohort'] == 'guard', review=final,
                    fields=[{'label': '提示词版本', 'value': version}, {'label': '复核范围', 'value': scope},
                            {'label': '原判定 → 当前判定', 'value': prior['status'] + ' → ' + final['status']}],
                    details=details, features=case.get('features', []),
                    evidence={'historical_raw_file': raw_path.name, 'previous_review': previous, 'current_review': new_reviews[cid],
                              'record': case, 'rubric_sha256': rubric_hash})
        if is_ablation:
            item['assessments'] = final['assessments']
            item['features'] = [case['title'], case['contrast']]
            item['fields'].extend([{'label': '对照目的', 'value': case['comparison']},
                                   {'label': '重复与请求数', 'value': f'第 {case["repeat"]}/3 次；{len(case["steps"])} 个请求'}])
        else:
            item['checks'] = case.get('checks', [])
            item['details'].append({'title': '旧样例验收说明（新口径优先）',
                                    'text': case.get('review_points', '') + '\n' + json.dumps(case.get('generation_expected', {}), ensure_ascii=False)})
        cases.append(item)
        original_cases.append({**item, 'review': prior, 'overall': prior['status']})
    if visited != set(decisions) or set(old['cases']) != set(new_reviews):
        raise ValueError('Decision or review coverage mismatch')
    validate({'schema_version': 1, 'evaluation': {'classification': False}, 'cases': cases})
    return raw, cases, original_cases, new_reviews, step_rows


def build(ledger_path, output_dir):
    ledger = json.loads(ledger_path.read_text())
    rubric = ROOT / ledger['rubric_file']
    if sha(rubric) != ledger['rubric_sha256']:
        raise ValueError('Review rubric changed')
    output_dir.mkdir(parents=True, exist_ok=True)
    # Sources and reports stay together so all historical evidence links resolve.
    if output_dir.resolve() != ledger_path.parent.resolve():
        raise ValueError('Output directory must contain the historical evidence and ledger')
    stems = {'boundary': 'boundary-edit-correctness-review-2026-11-12',
             'ablation': 'edit-ablation-correctness-review-2026-11-12'}
    targets = [output_dir / (stem + suffix) for stem in stems.values()
               for suffix in ('.html', '.report.json', '.metrics.json')]
    targets += [output_dir / f'edit-{r["family"]}-{r["version"]}-correctness-2026-11-12.reviews.json' for r in ledger['runs']]
    rubric_copy = output_dir / 'edit-correctness-review-2026-11-12-rules.txt'
    targets.append(rubric_copy)
    if any(p.exists() for p in targets):
        raise ValueError('Refuse to overwrite an existing report or review artifact')
    bundles = {}
    for entry in ledger['runs']:
        loaded = load_run(entry, output_dir, ledger['rubric_sha256'])
        bundles.setdefault(entry['family'], []).append((entry, loaded))
    rubric_copy.write_bytes(rubric.read_bytes())
    for family, versions in bundles.items():
        stem = stems[family]
        all_cases, rows, changes, multi_steps, condition_rows, links = [], [], [], [], [], []
        per_version, metadata = {}, {}
        for entry, (raw, cases, old_cases, reviews, steps) in versions:
            version = entry['version']
            per_version[version] = cases
            all_cases.extend(cases)
            multi_steps.extend(steps)
            old_stats, new_stats = metrics(old_cases), metrics(cases)
            for label, stats in [('原口径', old_stats), ('当前口径', new_stats)]:
                rows.append([version, label, stats['scored'], stats['body'].get('pass', 0), stats['body'].get('fail', 0),
                             stats['body'].get('uncertain', 0), seconds(stats['latency']['p50']), seconds(stats['latency']['p95'])])
            for c, old_c in zip(cases, old_cases, strict=True):
                if c['review']['status'] != old_c['review']['status']:
                    changes.append([c['id'], old_c['review']['status'], c['review']['status'], c['review']['note']])
            review_path = output_dir / f'edit-{family}-{version}-correctness-2026-11-12.reviews.json'
            write_json(review_path, {'source_sha256': entry['raw_sha256'], 'previous_reviews_sha256': entry['previous_reviews_sha256'],
                                    'rubric_sha256': ledger['rubric_sha256'], 'decision_ledger_sha256': sha(ledger_path),
                                    'method': ledger['method'], 'cases': reviews})
            metadata[version] = {'model': raw['model'], 'prompt_version': raw['prompt_version'], 'prompt_sha256': digest(raw['prompt']),
                                 'historical_run_time': raw['created_at'], 'raw_sha256': entry['raw_sha256'],
                                 'new_reviews_file': review_path.name, 'new_reviews_sha256': sha(review_path),
                                 'rechecked_outputs': len(entry['decisions']), 'metrics': new_stats}
            links.extend([{'label': version + ' 原始请求与响应', 'href': entry['raw_file']},
                          {'label': version + ' 原始输入与条件', 'href': raw['input_manifest']['file']},
                          {'label': version + ' 新复核记录', 'href': review_path.name},
                          {'label': version + ' 编辑提示词', 'href': entry['raw_file'].replace('.raw.json', '-system.txt')}])
        paired = Counter((a['review']['status'], b['review']['status'])
                         for a, b in zip(per_version['v1'], per_version['v2'], strict=True) if not a['observation'])
        if [c['id'].split('/', 1)[1] for c in per_version['v1']] != [c['id'].split('/', 1)[1] for c in per_version['v2']]:
            raise ValueError('Paired case IDs differ')
        v2 = metrics(per_version['v2'])
        summary = [
            '本次只重评已保存的模型输出，新增模型请求为 0；主项目编辑提示词与旧报告均未改动。规则来自用户“只要没改错”的明确验收偏好。',
            ('同一 216 条样例的 v1、v2 各一份结果，共 432 张卡片；每版 210 条计分、6 条旧边界观察。' if family == 'boundary' else
             '同一 28 条消融条件各重复 3 次，每版 84 条试验、99 个历史请求。两版共 168 张卡片，含多次请求步骤明细。'),
            f'v2 当前口径：{v2["body"].get("pass", 0)}/{v2["scored"]} 通过，{v2["body"].get("fail", 0)} 失败，{v2["body"].get("uncertain", 0)} 待确认。通过表示没有发现明确改错，不表示新增现实细节已核实。',
            '重点修正了自然补充、礼貌问句和表达冗余的过严判定。原失败与待确认输出均重新检查；其余原通过、观察沿用。每条显示旧判定和修订原因。',
            '分数变化来自评分口径调整，不能解释为模型能力提升。v1 与 v2 原调用非同期、样本为合成开发数据；复核者为参与提示词设计的 Codex，非独立盲评。',
        ]
        tables = [{'title': '相同输出：原口径与当前口径', 'description': '耗时仍为历史测量；没有重新调用编辑模型。',
                   'columns': ['版本', '口径', '计分', '通过', '失败', '待确认', 'P50', 'P95'], 'rows': rows},
                  {'title': '同一当前口径下的 v1 → v2', 'description': '比较不同历史输出，不将差异归因于单一提示词改动。',
                   'columns': ['v1', 'v2', '数量'], 'rows': [[a, b, n] for (a, b), n in sorted(paired.items())]},
                  {'title': '判定变更及理由', 'description': '输入输出保持不变，逐项说明评分为什么变化。',
                   'columns': ['版本／样例', '旧判定', '当前判定', '原因'], 'rows': changes}]
        if family == 'ablation':
            for condition in sorted({c['evidence']['record']['condition_id'] for c in all_cases}):
                for version in ('v1', 'v2'):
                    items = [c for c in per_version[version] if c['evidence']['record']['condition_id'] == condition]
                    stats = metrics(items)
                    name_pass = sum(next(a for a in c['assessments'] if a['key'] == 'names')['status'] == 'pass' for c in items)
                    condition_rows.append([condition, version, name_pass, stats['body'].get('pass', 0), stats['body'].get('fail', 0), stats['body'].get('uncertain', 0)])
            tables.insert(1, {'title': '各条件重新计分', 'description': '姓名正确与完整要求分别计分，每条件 3 次。',
                              'columns': ['条件', '版本', '姓名满足', '全部通过', '失败', '待确认'], 'rows': condition_rows})
            tables.append({'title': '多次请求的逐步结果', 'description': '保留每步真实输入输出，不修复后再传给下一步。',
                           'columns': ['版本', '试验', '步骤', '当前判定', '依据'], 'rows': multi_steps})
        other = stems['ablation' if family == 'boundary' else 'boundary'] + '.html'
        data = {'schema_version': 1, 'evaluation': {'classification': False},
                'title': ('完整编辑套件' if family == 'boundary' else '组合指令拆分') + '：按“没有改错”重新复核 v1 / v2',
                'groups': {g: g for g in sorted({c['group'] for c in all_cases})}, 'cases': all_cases,
                'summary': summary, 'methodology': ledger['method'], 'analysis_tables': tables,
                'links': [{'label': '完整复核规则', 'href': rubric_copy.name}, {'label': '逐项复核决策', 'href': ledger_path.name},
                          {'label': '另一组重评报告', 'href': other}, {'label': '规范化报告数据', 'href': stem + '.report.json'}] + links,
                'metadata': {'review_date': ledger['review_date'], 'new_model_requests': 0, 'rubric_sha256': sha(rubric),
                             'rubric': rubric.read_text(), 'decision_ledger_sha256': sha(ledger_path), 'runs': metadata,
                             'builder_sha256': sha(Path(__file__)), 'renderer_sha256': sha(ROOT / 'skills/llm-eval-report/scripts/render_report.py')}}
        if family == 'ablation':
            data['latency_label'] = '历史整条试验耗时 P50 / P95'
        page, stats = render(data)
        write_json(output_dir / (stem + '.report.json'), data)
        write_json(output_dir / (stem + '.metrics.json'), stats)
        (output_dir / (stem + '.html')).write_text(page)
        print(json.dumps({'family': family, 'v2': v2, 'review_status_changes': len(changes)}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('decisions', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    build(args.decisions, args.output_dir)
