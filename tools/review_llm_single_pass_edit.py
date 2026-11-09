#!/usr/bin/env python3
"""Review frozen single-pass responses offline; never makes a model request."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.check_llm_single_pass_edit import (
    BASELINE, check_output, latency_stats, parse_labels, write_report,
)


def split_response(raw):
    """Accept body on the header line or next line, preserving body contents."""
    match = re.fullmatch(r'分类：([^\r\n]+)\r?\n正文：([\s\S]*)', raw)
    if not match:
        raise ValueError('Missing classification line or body header')
    body = match.group(2)
    if body.startswith('\r\n'):
        body = body[2:]
    elif body.startswith('\n'):
        body = body[1:]
    return match.group(1), body


def parse_compatible_response(raw, target):
    labels, body = split_response(raw)
    plan = parse_labels(labels, target)
    if plan is None and body != target:
        raise ValueError('No-task classification changed the source')
    return labels, plan, body


def review_frozen_report(raw, reviews):
    report = deepcopy(raw)
    if set(reviews) != {c['id'] for c in raw['cases']}:
        raise ValueError('Reviews must match the complete frozen case set')
    report['reviewed_at'] = datetime.now(timezone.utc).isoformat()
    report['strict_metrics'] = deepcopy(raw['metrics'])
    report['offline_review'] = {
        'parser': 'same-line-or-next-line-body-v1',
        'method': '只对冻结响应做离线解析和 Codex 逐条语义复核；非独立人工盲评。没有重新请求模型或修改模型文字。',
        'compatibility': '仅接受正文紧接“正文：”或换行开始；未知类别仍报错，不删除或更名。原严格结果另存，不覆盖。',
        'semantic_scope': '口述、原文、给定 OCR 与完整正文对照；固定字符串检查不替代语义复核。语言边界或标签缺少唯一标准时保留 needs_review。',
        'layout_recovered_ids': [],
    }
    for c in report['cases']:
        if len(c['calls']) != 1:
            raise ValueError('Expected one frozen request: ' + c['id'])
        user_messages = [m for m in c['calls'][0]['request']['input'] if m['role'] == 'user']
        expected_input = {'utterance':c['utterance'], 'editable_text':c['target'], 'screen_context':c['context']}
        if len(user_messages) != 1 or json.loads(user_messages[0]['content'][0]['text']) != expected_input:
            raise ValueError('Unexpected model input fields or contents: ' + c['id'])
        c['strict_result'] = {k: c.get(k) for k in ('error', 'route_exact', 'raw_labels', 'actual_tasks')}
        try:
            labels, plan, body = parse_compatible_response(c['raw_response'], c['target'])
            if c.get('error'):
                report['offline_review']['layout_recovered_ids'].append(c['id'])
                c['layout_note'] = '原返回把正文放在“正文：”同一行。离线兼容解析，文字未改动，原严格错误保留于 strict_result。'
            c.pop('error', None)
            c.update(raw_labels=labels, actual_tasks=plan.as_dict()['tasks'] if plan else [], final_text=body)
            c['route_exact'] = set(c['actual_tasks']) == set(c['expected_tasks'])
            c['checks'] = check_output(c['generation_expected'], body)
            c['constraint_passed'] = all(x['passed'] for x in c['checks']) if c['checks'] else None
        except ValueError as exc:
            c.update(error=str(exc), route_exact=False)
            labels, body = split_response(c['raw_response'])
            c.update(raw_labels=labels, unvalidated_labels=labels, unvalidated_body=body)
        c['generation_review'] = reviews[c['id']]
        if c['generation_review']['status'] not in {'pass', 'fail', 'needs_review', 'unavailable'}:
            raise ValueError('Missing completed review: ' + c['id'])
        if c.get('error') and c['generation_review']['status'] != 'unavailable':
            raise ValueError('Invalid model output cannot be counted as an accepted body')
        if c['id'] == 'fidelity-authorized-condition-change':
            c['routing_review'] = {'note': '冻结预期为“润色”，模型为“修改、润色”，仍计分类不匹配。口述明确改变交付条件，两种任务边界有解释空间；正文采用了用户授权的新条件。'}
        elif c['id'] == 'ocr-modify-ambiguous':
            c['routing_review'] = {'note': '冻结预期为“修改”，模型为“无可执行编辑”，仍计分类不匹配。OCR 存在多个候选，返回原文避免猜测，正文语义复核通过。'}
    report['completion_latency_all_responses'] = {
        cohort: latency_stats([c['latency_s'] for c in report['cases'] if cohort == 'all' or (c['cohort'] != 'guard' if cohort == 'effective_edit_150' else c['cohort'] == cohort)])
        for cohort in ('all', 'effective_edit_150', 'regression', 'fresh', 'guard')
    }
    report['strict_output_errors'] = [{'id':c['id'], 'error':c['error'], 'raw_response':c['raw_response']} for c in raw['cases'] if c.get('error')]
    report['http_evidence'] = {
        'cases':len(report['cases']),
        'exactly_one_request_per_case':all(len(c['calls']) == 1 for c in report['cases']),
        'completed_responses':sum(c['calls'][0]['response'].get('status') == 'completed' for c in report['cases']),
        'expected_labels_in_model_input':False,
    }
    baseline = {c['id']:c for c in json.loads(BASELINE.read_text())['cases']}
    paired = [c for c in report['cases'] if 'final_text' in baseline[c['id']]]
    def cache_stats(calls):
        return {'requests':len(calls), 'cache_hit_requests':sum(call['response'].get('usage',{}).get('input_tokens_details',{}).get('cached_tokens',0)>0 for call in calls)}
    report['cache_comparison_same_24'] = {
        'baseline':cache_stats([call for c in paired for call in baseline[c['id']]['calls']]),
        'single_pass':cache_stats([call for c in paired for call in c['calls']]),
        'limitation':'缓存命中不同，且未交错随机测试；不能将全部延迟差归因于请求合并。',
    }
    return report


def add_review_explanation(report, path):
    e = html.escape
    strict = report['strict_metrics']['effective_edit_150']
    current = report['metrics']['effective_edit_150']
    text = f'''<h2>评分口径与返回格式</h2><div class="panel">
<p>原始严格解析：分类匹配 {strict['route_exact']}/150，返回格式错误 {strict['response_errors']}/150。
兼容正文同一行／下一行后：分类匹配 {current['route_exact']}/150，仍有 {current['response_errors']} 条未知类别错误。
另有 1 条边界样例同样恢复解析。只改变解析器对换行位置的容忍，未修改任何模型文字，未重新调用模型。</p>
<p>正文复核为 Codex 对照输入输出逐条判断，非独立人工盲评；needs_review 与 unavailable 都不计通过。
表内延迟为有效解析样例的完整回复时间；含所有格式错误样例的耗时另见复现信息。
本轮全部 156 次命中部分输入缓存；同组 24 条旧方案为 31/48 次命中，新方案为 24/24 次命中。
缓存条件不同，不能将全部提速归因于请求合并；延迟也不包含 ASR、截图 OCR 和输入法写回。</p>
<details><summary>4 条严格格式错误的原始输出</summary><pre>{e(json.dumps(report['strict_output_errors'],ensure_ascii=False,indent=2))}</pre></details></div>'''
    content = path.with_suffix('.html').read_text()
    content = content.replace('<h2>与之前两次请求对照</h2>', text + '<h2>与之前两次请求对照</h2>', 1)
    path.with_suffix('.html').write_text(content)


def write_summary(report, details_path, summary_path):
    from tools.build_llm_edit_review_report import CSS, table
    from proximic_ring.text_processing.task_routing import EDIT_TASK_LABELS
    e = html.escape
    comparison = report['comparison']
    before, after = comparison['baseline_two_call_latency'], comparison['single_call_latency_same_cases']
    cases = {c['id']:c for c in report['cases']}
    rows = [
        ('分类匹配（150 条）','145/150 · 96.7%','147/150 · 98.0%（兼容换行）'),
        ('分类匹配（24 条同组）','24/24','24/24'),
        ('正文语义复核（24 条同组）','20 通过 / 4 失败','20 通过 / 4 失败'),
        ('完整回复 P50（24 条同组）',f"{before['p50_s']:.3f} 秒",f"{after['p50_s']:.3f} 秒 · 降低 {1-after['p50_s']/before['p50_s']:.1%}"),
        ('完整回复 P95（24 条同组）',f"{before['p95_s']:.3f} 秒",f"{after['p95_s']:.3f} 秒 · 降低 {1-after['p95_s']/before['p95_s']:.1%}"),
        ('平均完整回复（24 条同组）',f"{before['mean_s']:.3f} 秒",f"{after['mean_s']:.3f} 秒"),
    ]
    body = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
            '<title>单次分类＋编辑：测试结论与流程代码</title><style>'+CSS+'</style><main>',
            '<header><div class="eyebrow">MYTHLINK · DEEPSEEK · 2026-11-09</div><h1>一次请求完成分类与编辑</h1><p>本轮同组样例耗时约减半；正文质量通过数没有提升。</p></header>',
            '<div class="panel">'+table(['指标','之前：先路由，再编辑','现在：一次返回分类与正文'],rows)+'</div>',
            '<p>模型：'+e(report['model'])+'。类别规则 '+e(str(report['routing_definitions']))+'，正文规则 '+e(str(report['generation_rules']))+'。150 条有效编辑和 6 条边界样例，每条一次请求，无重试。</p>',
            '<div class="callout">同日保存基线对照，非随机交错 A/B；新方案 156/156 请求均命中部分输入缓存。同组 24 条：旧方案 31/48 次请求命中缓存，新方案 24/24 次命中，不能将全部提速归因于请求合并。耗时从请求处理开始到完整回复收到并解析，非首字延迟，不含 ASR、实时截图 OCR、输入法写回。不能把分类和生成分别计时。</div>',
            '<h2>全部 150 条的结果</h2><div class="panel"><p>正文逐条复核：124 通过、22 失败、3 条待确认边界、1 条因未知类别未接受。分类与正文同时通过：122/150（81.3%）。6 条额外边界样例在兼容换行后全部通过。</p>',
            '<p>原严格解析分类匹配 146/150；兼容“正文：”后直接接正文的写法后为 147/150。全 156 条共恢复 3 条同一行正文，原始文字和类别均未改动。另 1 条输出“修改、释字拼写”，多出了第七类，仍计为失败。</p>',
            '<p>冻结标签与模型存在两处边界分歧：用户明确放宽交付条件，预期“润色”，模型“修改、润色”；OCR 多候选无法唯一确定，预期“修改”，模型“无可执行编辑”。两条均保留分类扣分，正文复核通过。</p>',
            '<p>语义结果由 Codex 对照输入输出复核，非独立人工盲评。3 条待确认分别为葡语截止日期边界、口述词形与字母拼写冲突、约一百字的长度容差。机械约束检查另计，不能等同于语义通过。</p></div>',
            '<p><a href="'+e(details_path.with_suffix('.html').name)+'">打开全部逐条输入、OCR、分类、正文、耗时及完整请求响应</a> · <a href="'+e(details_path.name)+'">下载完整 JSON 证据</a></p>',
            '<h2>一次请求实际返回示例</h2><div class="panel"><p>'+e(cases['fresh-three-tasks']['utterance'])+'</p><p>原文：'+e(cases['fresh-three-tasks']['target'])+'</p><pre>'+e(cases['fresh-three-tasks']['raw_response'])+'</pre></div>',
            '<h2>当前流程与 OCR 范围</h2><div class="panel"><ol><li>测试入口直接视作已由手动手势选择编辑，跳过听写／编辑分类。</li><li>输入本次口述、唯一可修改原文，以及只读 screen_context；预期标签只留在评分端。</li><li>把六类判定规则、共用编辑要求、组合规则和所有可用任务模块放入同一个系统提示词。空原文不提供“修改”。</li><li>向火山 DeepSeek 发一次请求，要求先输出中文类别，再输出完整正文；非流式，关闭 thinking，不用 JSON 或工具调用输出。</li><li>本地校验类别及分隔格式，比较冻结分类标签，并逐条检查正文。实验不写回应用。</li></ol>',
            '<p>旧方式先拿到计划，再由代码只拼接选中模块。合并后需同次发送所有可用模块，让模型按自身分类选择执行，无法从外部证明它在内部严格分成两个阶段。</p>',
            '<p>已把模拟 OCR 的文字接入模型 screen_context。此次没有启动实时截图／OCR；第三模式的设置项、tap／右滑接线和输入法写回也尚未接入。当前是可复现的测试原型。</p></div>',
            '<h2>六类沿用关系</h2><div class="panel">'+table(['代码类别','中文输出'],list(EDIT_TASK_LABELS.items()))+'</div>']
    for title, source, start, end in [
        ('A · 同次拼接类别规则和编辑模块','tools/check_llm_single_pass_edit.py',49,78),
        ('B · 只发一次请求，读取分类及正文','tools/check_llm_single_pass_edit.py',107,141),
        ('C · 离线兼容正文换行，拒绝未知类别','tools/review_llm_single_pass_edit.py',22,40),
    ]:
        p = ROOT/source
        code = '\n'.join(f'{i:03}  {line}' for i,line in enumerate(p.read_text().splitlines(),1) if start<=i<=end)
        body.append('<h2>'+e(title)+'</h2><div class="panel"><p>'+e(str(p))+f' · {start}–{end}</p><pre>'+e(code)+'</pre></div>')
    body.append('<h2>正文失败样例清单</h2><div class="panel">'+table(['样例','逐条复核原因'],[(c['id'],c['generation_review']['note']) for c in report['cases'] if c['generation_review']['status']=='fail'])+'</div>')
    body.append('<h2>复现与数据边界</h2><div class="panel"><p>原始严格输出保存在 llm-edit-single-pass-full-2026-11-09.json/jsonl；reviewed 文件仅增加兼容解析和评语。没有用本轮结果修改模型提示词后重跑。所有样例均为合成短文本，不能代表长文、真实 ASR 或真实截图使用准确率。</p><p>本轮 Ark 实际请求未包含 temperature 字段；虽然通用调用传入 0.0，传输层未发送它，不能宣称温度为零。token 总量：输入 '+str(report['usage']['input_tokens'])+'，输出 '+str(report['usage']['output_tokens'])+'，reasoning '+str(report['usage']['reasoning_tokens'])+'。</p></div></main></html>')
    summary_path.write_text(''.join(body))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw', type=Path, required=True)
    parser.add_argument('--reviews', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--summary', type=Path)
    args = parser.parse_args()
    if args.output.resolve() == args.raw.resolve():
        raise ValueError('Do not overwrite the original experiment')
    report = review_frozen_report(json.loads(args.raw.read_text()), json.loads(args.reviews.read_text()))
    report['review_provenance'] = {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.raw,args.reviews,Path(__file__))}
    write_report(report, args.output)
    add_review_explanation(report, args.output)
    if args.summary:
        write_summary(report, args.output, args.summary)
    print(json.dumps({'metrics':report['metrics'], 'comparison':report['comparison'], 'usage':report['usage']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
