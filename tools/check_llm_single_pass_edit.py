#!/usr/bin/env python3
"""One Ark request returns Chinese routing labels and final edited text.

Synthetic fixture evaluation only; does not change application modes or writeback.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import re
import sys
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))

from tools.check_llm_edit_router import (
    configured_settings, load_cases, parse_labels, routing_prompt, latency_stats, RecordedResponse,
)
from tools.check_automatic_edit_generation import check_output
from proximic_ring.text_processing.automatic_prompts import (
    AUTOMATIC_PROMPT_VERSION, EDIT_BASE_PROMPT, EDIT_COMBINATION_RULES, EDIT_PROMPT_MODULES,
)
from proximic_ring.text_processing.llm import OpenAICompatibleTextProcessor
from proximic_ring.text_processing.model import validate_edit_target_text
from proximic_ring.text_processing.processing_common import _text_output_budget
from proximic_ring.text_processing.prompts import SCREEN_CONTEXT_RULES
from proximic_ring.text_processing.task_routing import EDIT_TASK_LABELS, available_edit_tasks
from proximic_ring.text_processing.router_prompts import ROUTER_PROMPT_VERSION

VERSION = 'manual-edit-single-pass-natural-v1'
BASELINE = ROOT / 'docs/reports/llm-edit-natural-full-2026-11-09.json'
SUPPLEMENT = ROOT / 'docs/reports/llm-edit-natural-error-generation-2026-11-09.json'


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError('Source prompt changed; review the single-pass adaptation')
    return text.replace(before, after, 1)


def single_pass_prompt(target):
    # Keep all category semantics; adapt only upstream-plan and delivery contracts.
    route_rules = routing_prompt(target).rsplit('\n\n', 1)[0]
    base = replace_once(EDIT_BASE_PROMPT,
        'edit_plan 是上游路由选定的任务集合，具体边界和执行方式见随附的任务模块。',
        '编辑任务由你在本次请求内先行确定，具体边界和执行方式见下面的任务模块。')
    base = replace_once(base,
        '仅调用 submit_text_edit，modified_text 为最终完整正文；',
        '按本提示末尾规定的纯文本格式交付类别和最终完整正文；')
    combination = replace_once(EDIT_COMBINATION_RULES,
        '只提交最终完整正文，不输出操作计划或中间稿。',
        '正文部分只提交最终完整正文，不输出操作计划或中间稿。')
    modules = '\n\n'.join(EDIT_PROMPT_MODULES[k] for k in available_edit_tasks(target))
    return '\n\n'.join([
        '在一次回复中完成编辑任务分类和正文编辑。用户已经手动选择编辑，不做听写／编辑二分类。'
        '先确定最终有效的任务类别，再按这些类别的规则完成编辑；回复先给类别，再给结果，'
        '不展示推理过程或解释。类别名称和最终正文必须相互一致。',
        '【类别判断规则】\n' + route_rules,
        '【分类后执行规则】\n' + base,
        combination,
        '下面列出所有本次可用任务的执行规则，供你按分类选择。只执行用户实际要求且你选中的任务；'
        '没有选中的模块不构成编辑授权，不要为了用上某模块而增加类别或扩大修改。\n' + modules,
        SCREEN_CONTEXT_RULES,
        '【最终返回格式】\n第一行：分类：中文类别名称\n第二行：正文：\n'
        '第三行起：修改后的完整正文，可包含多行。多个类别用顿号连接。'
        '不要 JSON，不要 Markdown 代码围栏，不要额外说明；正文自身要求的列表、表格等格式应保留。'
        '没有有效任务时第一行写“分类：无可执行编辑”，正文逐字返回 editable_text，不擅自录入口述。'
        '明确要求清空原文时允许正文为空，此时仍保留“正文：”标题。'
        '分类只有类别名称，不能夹带操作内容；正文中不要包含这两个用于分隔的外层标题。',
    ])


def parse_response(raw, target):
    if not isinstance(raw, str):
        raise ValueError('Combined response must be plain text')
    match = re.fullmatch(r'分类：([^\r\n]+)\r?\n正文：(?:\r?\n([\s\S]*))?', raw)
    if not match:
        raise ValueError('Response must contain a classification line and a separate body header')
    raw_labels, final = match.group(1), match.group(2) or ''
    plan = parse_labels(raw_labels, target)
    if plan is None and final != target:
        raise ValueError('No-task classification changed the source')
    return raw_labels, plan, final


def generation_expectations(case):
    if case['cohort'] == 'guard':
        return {'modified_text': case['target']}
    result = dict(case.get('generation_expected', {}))
    original = case.get('expected', {})
    for key in ('modified_text', 'contains', 'contains_ci', 'excludes', 'max_chars'):
        if key in original:
            result[key] = original[key]
    if original.get('preserve_text'):
        result['contains'] = [*result.get('contains', []), *original['preserve_text']]
    return result


def run_case(case, settings):
    record = {**case, 'generate': True, 'calls': []}
    target = validate_edit_target_text(case['target'], allow_empty=True)
    model_input = {'utterance': case['utterance'], 'editable_text': target, 'screen_context': case['context']}
    record['model_input'] = model_input
    record['generation_expected'] = generation_expectations(case)

    def capture(request, **kwargs):
        if record['calls']:
            raise RuntimeError('Single-pass evaluation forbids a second HTTP request')
        call = {'endpoint': request.full_url, 'request': json.loads(request.data.decode('utf-8'))}
        record['calls'].append(call)
        started = time.perf_counter()
        try:
            response = urlopen(request, **kwargs)
            call['http_status'] = response.status
            return RecordedResponse(response, call, started)
        except Exception as exc:
            call.update(http_latency_s=time.perf_counter()-started, error_type=type(exc).__name__)
            raise

    processor = OpenAICompatibleTextProcessor(urlopen=capture)
    started = time.perf_counter()
    try:
        raw = processor._request_chat(
            settings, system_prompt=single_pass_prompt(target),
            user_content=json.dumps(model_input, ensure_ascii=False),
            temperature=0.0, max_tokens=_text_output_budget(settings), edit_tool=None)
        record['raw_response'] = raw
        payload = record['calls'][0].get('response', {})
        if payload.get('status') != 'completed':
            raise ValueError('Ark response was not completed: ' + str(payload.get('status')))
        raw_labels, plan, final = parse_response(raw, target)
        record.update(raw_labels=raw_labels, actual_tasks=plan.as_dict()['tasks'] if plan else [], final_text=final)
        record['route_exact'] = set(record['actual_tasks']) == set(case['expected_tasks'])
        record['checks'] = check_output(record['generation_expected'], final)
        record['constraint_passed'] = all(c['passed'] for c in record['checks']) if record['checks'] else None
        record['generation_review'] = {'status': 'pending'}
    except Exception as exc:
        record.update(error=str(exc), route_exact=False)
    finally:
        record['latency_s'] = time.perf_counter() - started
        processor.close()
    return record


def score_group(cases):
    valid = [c for c in cases if not c.get('error')]
    reviews = Counter(c.get('generation_review', {}).get('status', 'unavailable') for c in cases)
    per_task = {}
    for task in EDIT_TASK_LABELS:
        tp = sum(task in c.get('actual_tasks', []) and task in c['expected_tasks'] for c in cases)
        fp = sum(task in c.get('actual_tasks', []) and task not in c['expected_tasks'] for c in cases)
        fn = sum(task not in c.get('actual_tasks', []) and task in c['expected_tasks'] for c in cases)
        per_task[task] = {'tp':tp, 'fp':fp, 'fn':fn, 'precision':tp/(tp+fp) if tp+fp else None,
                          'recall':tp/(tp+fn) if tp+fn else None}
    return {'n':len(cases), 'route_exact':sum(c['route_exact'] for c in cases),
            'response_errors':len(cases)-len(valid), 'latency':latency_stats([c['latency_s'] for c in valid]),
            'constraint_passed':sum(c.get('constraint_passed') is True for c in cases),
            'constraint_failed':sum(c.get('constraint_passed') is False for c in cases),
            'constraint_unchecked':sum(c.get('constraint_passed') is None for c in cases),
            'semantic_review':dict(reviews),
            'route_and_body_passed':sum(c['route_exact'] and c.get('generation_review',{}).get('status')=='pass' for c in cases),
            'per_task':per_task}


def summarize(cases):
    return {'effective_edit_150':score_group([c for c in cases if c['cohort']!='guard']),
            **{cohort:score_group([c for c in cases if c['cohort']==cohort]) for cohort in ('regression','fresh','guard')}}


def write_report(report, path):
    report['metrics'] = summarize(report['cases'])
    old=json.loads(BASELINE.read_text())
    baseline={c['id']:c for c in old['cases']}
    historical_followup={c['id']:c for c in json.loads(SUPPLEMENT.read_text())['cases']}
    effective=[c for c in report['cases'] if c['cohort']!='guard']
    old_effective=[baseline[c['id']] for c in effective]
    paired=[c for c in effective if 'final_text' in baseline[c['id']]]
    report['comparison']={
        'scope':'同日已保存的两次请求基线；不是同期随机交错 A/B。基线仅24条同轮生成，4条另行补测，其余无正文对照。',
        'baseline_route_exact':sum(c['route_exact'] for c in old_effective),
        'single_route_exact':sum(c['route_exact'] for c in effective),
        'route_changed':[{'id':c['id'],'expected':c['expected_tasks'],'before':baseline[c['id']].get('raw_labels'),
                          'after':c.get('raw_labels'),'before_correct':baseline[c['id']]['route_exact'],'after_correct':c['route_exact']}
                         for c in effective if set(c.get('actual_tasks',[]))!=set(baseline[c['id']].get('actual_tasks',[]))],
        'matched_generation_cases':len(paired),
        'baseline_two_call_latency':latency_stats([baseline[c['id']]['total_latency_s'] for c in paired]),
        'single_call_latency_same_cases':latency_stats([c['latency_s'] for c in paired if not c.get('error')]),
        'baseline_semantic_reviews':dict(Counter(baseline[c['id']]['generation_review']['status'] for c in paired)),
        'single_semantic_reviews_same_cases':dict(Counter(c.get('generation_review',{}).get('status','unavailable') for c in paired)),
        'pairwise_latency':[{'id':c['id'],'before_s':baseline[c['id']]['total_latency_s'],'after_s':c['latency_s'],
                             'difference_s':c['latency_s']-baseline[c['id']]['total_latency_s']} for c in paired],
    }
    usages=[call['response'].get('usage',{}) for c in report['cases'] for call in c['calls'] if 'response' in call]
    report['usage']={'requests':len(usages),'input_tokens':sum(u.get('input_tokens',0) for u in usages),
                    'output_tokens':sum(u.get('output_tokens',0) for u in usages),
                    'cached_requests':sum(u.get('input_tokens_details',{}).get('cached_tokens',0)>0 for u in usages),
                    'reasoning_tokens':sum(u.get('output_tokens_details',{}).get('reasoning_tokens',0) for u in usages)}
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    from tools.build_llm_edit_review_report import CSS, table
    e=html.escape
    metricrows=[]
    for key,title in [('effective_edit_150','全部有效编辑'),('regression','旧回归'),('fresh','新增样例'),('guard','无有效任务边界')]:
        m=report['metrics'][key];lat=m['latency']
        metricrows.append((title,m['n'],f"{m['route_exact']}/{m['n']}",json.dumps(m['semantic_review'],ensure_ascii=False),m['route_and_body_passed'],f"{lat['p50_s']:.3f} / {lat['p95_s']:.3f}" if lat else '—'))
    body=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
          '<title>单次请求：分类＋编辑实测</title><style>'+CSS+'</style><main>',
          '<header><div class="eyebrow">MYTHLINK · SINGLE REQUEST · DEEPSEEK</div><h1>一次请求，同时返回分类与编辑正文</h1><p>'+e(report['scope'])+'</p></header>',
          '<div class="callout">每个样例严格一次 HTTP 请求；中文分类行＋正文，无 JSON 输出。已明确手动选择编辑，不做听写／编辑二分类。耗时是整个回复完成时间，不能拆成独立的路由和生成耗时。预期标签仅用于评分。</div>',
          '<h2>结果汇总</h2><div class="panel">'+table(['分组','条数','分类匹配','正文语义复核','分类及正文同时通过','总耗时 P50 / P95 秒'],metricrows)+'</div>',
          '<h2>与之前两次请求对照</h2><div class="panel"><pre>'+e(json.dumps(report['comparison'],ensure_ascii=False,indent=2))+'</pre></div>',
          '<h2>全部逐条结果</h2>']
    for i,c in enumerate(report['cases'],1):
        review=c.get('generation_review',{'status':'unavailable'})
        isbad=not c['route_exact'] or review['status']=='fail' or c.get('error')
        body.append('<article class="case '+('bad' if isbad else '')+'" id="case-'+str(i)+'"><div class="case-head"><h3>'+f'{i:03} · '+e(c['id'])+'</h3><span class="badge '+('bad' if isbad else '')+'">'+('分类匹配' if c['route_exact'] else '分类不匹配')+f" · {c['latency_s']:.3f} 秒</span></div>")
        values=[('口述',c['utterance']),('原文',c['target'] or '【空原文】'),('预期类别','、'.join(EDIT_TASK_LABELS[k] for k in c['expected_tasks']) or '无可执行编辑'),('实际类别',c.get('raw_labels',c.get('error','')))]
        body.append('<div class="grid">'+''.join('<div class="field"><div class="label">'+name+'</div><div class="text">'+e(value)+'</div></div>' for name,value in values)+'</div>')
        body.append('<p><strong>OCR 参考</strong></p><pre>'+e(json.dumps(c['context'],ensure_ascii=False,indent=2))+'</pre><div class="gen"><h3>单次请求返回的正文</h3><pre>'+e(c.get('final_text','【未取得有效正文】'))+'</pre><p>语义复核：'+e(review['status'])+'；机械约束：'+str(c.get('constraint_passed'))+'</p><p>'+e(review.get('note','尚未复核'))+'</p></div>')
        if c.get('routing_review'):body.append('<p>分类复核：'+e(c['routing_review']['note'])+'</p>')
        prior=baseline[c['id']]
        previous_body=prior if 'final_text' in prior else historical_followup.get(c['id'])
        body.append('<details><summary>之前两次请求方案的结果</summary><p>原分类：'+e(prior.get('raw_labels',''))+'；路由耗时：'+f"{prior['routing_latency_s']:.3f}"+' 秒</p>')
        if previous_body:
            body.append('<p>'+('同轮生成，总耗时 '+f"{prior['total_latency_s']:.3f}"+' 秒' if 'final_text' in prior else '另行冻结错误任务补测的正文，不计为同轮总耗时')+'</p><pre>'+e(previous_body['final_text'])+'</pre><pre>'+e(json.dumps(previous_body.get('generation_review',previous_body.get('review',{})),ensure_ascii=False))+'</pre>')
        else:body.append('<p>旧方案本条未生成正文。</p>')
        body.append('</details><details class="raw"><summary>完整实际请求、原始返回、token 和评分证据</summary><pre>'+e(json.dumps(c,ensure_ascii=False,indent=2))+'</pre></details></article>')
    body.append('<h2>复现信息</h2><div class="panel"><pre>'+e(json.dumps({k:v for k,v in report.items() if k!='cases'},ensure_ascii=False,indent=2))+'</pre></div></main></html>')
    path.with_suffix('.html').write_text(''.join(body))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--case',action='append')
    parser.add_argument('--validate-only',action='store_true')
    args=parser.parse_args()
    fixture=ROOT/'tools/data/llm_edit_routing_natural_cases.json'
    cases=load_cases(fixture)
    if args.case:
        if set(args.case)-{c['id'] for c in cases}:raise ValueError('Unknown case IDs')
        cases=[c for c in cases if c['id'] in args.case]
    if args.validate_only:
        for c in cases:single_pass_prompt(c['target'])
        print('CASES_AND_PROMPT_OK',len(cases));return 0
    if args.output.exists():raise ValueError('Refusing to overwrite an existing experiment')
    settings=configured_settings()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    sources=[Path(__file__),ROOT/'tools/check_llm_edit_router.py',fixture,BASELINE,SUPPLEMENT,
             *(ROOT/'src/proximic_ring/text_processing'/f for f in ('automatic_prompts.py','router_prompts.py','task_routing.py','prompts.py','llm.py'))]
    report={'created_at':datetime.now(timezone.utc).isoformat(),'version':VERSION,'model':settings.model,
            'routing_definitions':ROUTER_PROMPT_VERSION,'generation_rules':AUTOMATIC_PROMPT_VERSION,
            'scope':'合成文本实测：一次火山 DeepSeek 请求直接返回中文分类与完整编辑结果，150条有效编辑＋6条边界；无实时ASR、截图OCR或输入法写回。',
            'prompt_adaptation':'复用原类别规则、共用编辑要求、组合规则与所有可用任务模块；仅把上游edit_plan和工具交付要求改为模型本次自行判断及分类／正文纯文本输出。所有模块同次输入，不能像两次调用那样事先只发送选中模块。',
            'method':'Serial, non-streaming, thinking disabled, one HTTP request per case, no retries; input has utterance, editable_text, screen_context only. No expected labels or plan in request. Completion latency only, not time-to-first-token or separately measured routing latency.',
            'source_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},'cases':[]}
    try:
        with args.output.with_suffix('.jsonl').open('x') as journal:
            for i,c in enumerate(cases,1):
                row=run_case(c,settings);report['cases'].append(row)
                journal.write(json.dumps(row,ensure_ascii=False)+'\n');journal.flush()
                print(json.dumps({'i':i,'n':len(cases),'id':c['id'],'labels':row.get('raw_labels'),'route_ok':row['route_exact'],
                                  'constraints':row.get('constraint_passed'),'s':round(row['latency_s'],3),'error':row.get('error')},ensure_ascii=False),flush=True)
                if len(report['cases'])>=3 and all(x.get('error') for x in report['cases'][-3:]):break
    finally:write_report(report,args.output)
    print(json.dumps(report['metrics'],ensure_ascii=False),flush=True)
    return int(any(c.get('error') for c in report['cases']))


if __name__=='__main__':raise SystemExit(main())
