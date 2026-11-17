#!/usr/bin/env python3
"""Offline adapter for frozen saved-context runs and output-bound reviews."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.voice_eval_dataset import fingerprint, write_json


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build(runs, output):
    cases, rows, links, metadata = [], [], [], []
    latest_by_cohort = {}
    groups = {}
    source_labels = {'saved': '保存资料', 'screen': '当前屏幕', 'both': '两者分别提供素材与回复对象', 'ignore': '当前稿件；忽略无关保存资料'}
    for index, directory in enumerate(runs, 1):
        raw = json.loads((directory / 'raw.json').read_text())
        frozen = json.loads((directory / 'inputs.json').read_text())
        reviews = json.loads((directory / 'reviews.json').read_text())
        prompt = (directory / 'prompt.txt').read_text()
        assert reviews['source_sha256'] == sha(directory / 'raw.json')
        assert raw['inputs_sha256'] == sha(directory / 'inputs.json')
        assert raw['prompt_sha256'] == hashlib.sha256(prompt.encode()).hexdigest()
        assert raw['completed_all'] and not raw['changed_sources']
        assert {r['id'] for r in raw['cases']} == set(reviews['cases']) == {c['id'] for c in frozen['cases']}
        lookup = {c['id']: c for c in frozen['cases']}
        group = f'round-{index}'
        news = all('news_summary' in c['special_tags'] for c in frozen['cases'])
        cohort = 'news' if news else 'general'
        cohort_label = '新闻分享邮件' if news else '通用来源选择与编辑'
        groups[group] = f'第 {index} 轮 · {cohort_label} · {raw["prompt_version"]}'
        source_pass = body_pass = requests = 0
        usage = Counter()
        for result in raw['cases']:
            case = lookup[result['id']]
            review = reviews['cases'][result['id']]
            assert review['input_sha256'] == result['input_sha256'] == fingerprint(result['model_input'])
            assert review['output_sha256'] == result['output_sha256'] == hashlib.sha256(result['output'].encode()).hexdigest()
            assert len(result['calls']) == 1
            wire = result['calls'][0]['request']
            assert wire['input'][0]['content'][0]['text'] == prompt
            assert json.loads(wire['input'][1]['content'][0]['text']) == result['model_input']
            assert set(result['model_input']) == {'utterance', 'editable_text', 'screen_context', 'saved_context'}
            assert result['model_input']['saved_context'] == case['input']['saved_context']
            assert result['model_input']['screen_context'] == case['input']['context']
            requests += len(result['calls'])
            source_pass += review['source_selection']['status'] == 'pass' and not result.get('error')
            body_pass += review['status'] == 'pass' and not result.get('error')
            response_usage = result['calls'][0].get('response', {}).get('usage', {})
            for field in ('input_tokens', 'output_tokens', 'total_tokens'):
                usage[field] += response_usage.get(field, 0)
            usage['cached_input_tokens'] += response_usage.get('input_tokens_details', {}).get('cached_tokens', 0)
            annotation = case['special_annotations']['saved_context']
            cases.append({'id': group + '-' + result['id'], 'group': group,
                'features': [annotation['group'], source_labels[annotation['expected_source']]],
                'instruction': case['input']['utterances'][0], 'source_text': case['input']['target'],
                'context': {'screen_context': case['input']['context'], 'saved_context': case['input']['saved_context']},
                'expected_result': '\n'.join(case['editing_expectations'][0]['requirements']),
                'output': result['output'], 'error': result.get('error', ''), 'latency_s': result['latency_s'],
                'checks': result.get('checks', []), 'review': review,
                'fields': [{'label': '主数据 ID', 'value': result['id']},
                           {'label': '预期来源', 'value': source_labels[annotation['expected_source']]}],
                'assessments': [{'label': '来源选择', 'status': review['source_selection']['status'],
                    'expected': source_labels[annotation['expected_source']], 'actual': review['source_selection']['note']}],
                'evidence': result})
        rows.append([groups[group], str(len(raw['cases'])), f'{source_pass}/{len(raw["cases"])}',
                     f'{body_pass}/{len(raw["cases"])}', str(requests)])
        latest_by_cohort[cohort] = rows[-1]
        metadata.append({**{k:v for k,v in raw.items() if k != 'cases'}, 'usage': dict(usage),
                         'review_method': reviews['method'],
                         'wire_parameters': {k:v for k,v in raw['cases'][0]['calls'][0]['request'].items() if k != 'input'}})
        for filename, label in [('raw.json', '原始请求与响应'), ('inputs.json', '冻结测试集'),
                                ('reviews.json', '逐例复核'), ('prompt.txt', '完整提示词')]:
            links.append({'label': f'第{index}轮 · {label}', 'href': os.path.relpath(directory / filename, output.parent)})
    summaries = []
    if 'news' in latest_by_cohort:
        latest = latest_by_cohort['news']
        summaries.append(f'优先新闻分享：{latest[1]} 条口述均为“我写一封邮件，把刚才的摘要分享给团队”。最新一轮来源选择通过 {latest[2]}，完整邮件要求通过 {latest[3]}。摘要包含三条或四条虚构新闻，屏幕放置无关或同主题冲突资料；检查新闻是否实际进入正文，以及数字、研究范围、授权和计划状态是否保留。')
    if 'general' in latest_by_cohort:
        latest = latest_by_cohort['general']
        summaries.append(f'通用专项最新一轮共 {latest[1]} 条：来源选择通过 {latest[2]}，完整编辑要求通过 {latest[3]}。原始20条覆盖15条引用保存资料、4条引用当前屏幕、1条忽略无关保存资料；4组成对用例仅改变口述指代。两条翻译仍未保留英文活动名，其中一条还漏译普通英文句子。')
    summaries.extend([
        f'用例直接写入统一主数据：修订版3加入20条通用用例，修订版4加入4条新闻用例，均是合成开发数据，非独立留出集。此报告保留 {len(runs)} 轮、{len(cases)} 次实际请求；历史轮与最新轮分别统计，不能把多轮总分当成当前提示词通过率。',
        '首轮发现“这里的清单”误合并了保存清单；随后加强来源范围限定并用完全相同的20条输入复测。首轮还遗漏英文名称保留和两句话要求，原始失败输出全部保留。新闻专项使用相同的v8提示词，未为这4条额外更改提示词。',
        '结果按冻结的任务要求逐条复核，来源选择与正文完成度分开计分，不以字符串检查或HTTP成功代替正确性。样例与提示词由Codex设计，复核也由Codex执行；不是独立人工验收。每轮每例只调用一次，不能据此保证稳定性或推断模型内部注意力。'])
    rubric = ROOT / 'tools/data/edit_review_correctness_v1.txt'
    rubric_snapshot = output.parent / 'grading-rules.txt'
    output.parent.mkdir(parents=True, exist_ok=True)
    if rubric_snapshot.exists():
        assert rubric_snapshot.read_bytes() == rubric.read_bytes()
    else:
        rubric_snapshot.write_bytes(rubric.read_bytes())
    links.append({'label': '完整语义复核规则', 'href': 'grading-rules.txt'})
    # Show the newly requested priority cases first; retain chronology in labels.
    cases = [case for group in reversed(groups) for case in cases if case['group'] == group]
    groups = dict(reversed(list(groups.items())))
    normalized = {'schema_version': 1, 'evaluation': {'classification': False},
        'title': '保存资料来源选择与新闻分享邮件', 'groups': groups,
        'summary': summaries,
        'methodology': '调用火山方舟已配置的DeepSeek生产编辑入口，关闭思考，串行且不自动重试。saved_context以独立纯字符串传入，screen_context单独传入；预期来源与答案不传给模型。仅测试模型文字编辑，不测试截图OCR、剪贴板监听、ASR或写回。真实请求参数与缓存token记录在证据中，未发送temperature参数。',
        'analysis_tables': [{'title': '分轮实测', 'columns': ['轮次', '样例', '来源选择通过', '完整要求通过', '实际请求数'], 'rows': rows}],
        'links': links, 'metadata': {'runs': metadata, 'rubric_version': 'correctness-v1-2026-11-12',
                                    'rubric_sha256': sha(rubric_snapshot)}, 'cases': cases}
    write_json(output, normalized)
    return normalized


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    data = build(args.run, args.output)
    print(json.dumps({'cases': len(data['cases']), 'runs': len(args.run)}))
