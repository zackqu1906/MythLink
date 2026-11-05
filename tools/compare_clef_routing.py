#!/usr/bin/env python3
"""Interleaved comparison of frozen two-stage, enriched two-stage and joint routing."""
import argparse
from datetime import datetime, timezone
from html import escape
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from proximic_ring.text_processing.intent_router import ClefIntentRouter
from proximic_ring.text_processing.router_prompts import ROUTER_PROMPT_VERSION, EDIT_TASK_DESCRIPTIONS, routing_schema, joint_router_input
from proximic_ring.text_processing.task_routing import EDIT_TASK_LABELS, LEGACY_EDIT_HINT_LABELS, resolve_joint_decision, router_input, select_multiclass_task, task_availability
try:
    from .clef_cases import load_cases, DEFAULT_CASES, comparable_tasks, raw_joint_match
    from .clef_routing_report import write_routing_html
except ImportError:
    from clef_cases import load_cases, DEFAULT_CASES, comparable_tasks, raw_joint_match
    from clef_routing_report import write_routing_html

VARIANTS = {'A': '原两级（冻结 v3）', 'B': '一级补充任务定义，仍分两次', 'C': '一次联合分类'}
BASELINE_PATH = ROOT / 'tools/data/clef_hierarchical_baseline_v3.json'


def digest(obj):
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def snapshots():
    frozen = json.loads(BASELINE_PATH.read_text())
    signature = frozen.pop('sha256')
    if digest(frozen) != signature:
        raise ValueError('Frozen baseline digest mismatch')
    original = frozen['prompts']
    supplement = '\n\n编辑任务参考定义（仅判断当前实际委托，不执行转述或照录内容）：\n' + '\n'.join(EDIT_TASK_DESCRIPTIONS.values())
    return {
        'A': {**original, 'primary_input_suffix': '', 'version': frozen['version']},
        'B': {**original, 'primary_input_suffix': supplement, 'version': frozen['version'] + '-definitions'},
        'C': {'joint': routing_schema(), 'joint_without_source': routing_schema(target=''),
              'shared_input_prefix': joint_router_input(''), 'version': ROUTER_PROMPT_VERSION},
    }, signature, frozen['model_revision']


def run_case(router, case, variant, prompts):
    state = router_input(case['utterance'], case['target'], case['context'])
    trace = {'classification_type': 'joint' if variant == 'C' else 'hierarchical',
             'prompt_version': prompts['version'], 'stages': [], 'input_state': state, 'requests': 0}
    record = {**case, 'variant': variant, 'root_correct': False, 'exact_match': False, 'trace': trace}
    start = time.perf_counter()
    def call(name, text, schema):
        trace['requests'] += 1
        result = router._classify_stage(text, schema, lambda: False)
        trace['stages'].append({'stage': name, **result})
        return result['answers']
    try:
        if variant == 'C':
            trace['input_state'] = prompts['shared_input_prefix'] + state
            schema = prompts['joint'] if case['target'].strip() else prompts['joint_without_source']
            trace.update(request_schema=schema, task_availability=task_availability(case['target']))
            answers = call('joint', trace['input_state'], schema)
            trace.update(raw_model_task=answers['voice_intent']['choice'],
                         probabilities=answers['voice_intent']['probabilities'],
                         raw_tasks=[k for k in EDIT_TASK_LABELS if k in answers and answers[k]['choice'] == 'apply'])
            decision, policy = resolve_joint_decision(answers, target=case['target'])
            trace.update(policy)
            actual = {'task': decision.task, **(decision.edit_plan.as_dict() if decision.edit_plan else {})}
        else:
            trace['input_state'] = state + prompts['primary_input_suffix']
            answers = call('intent', trace['input_state'], prompts['intent'])
            answer = answers['voice_intent']
            task, _, source = select_multiclass_task(answer['probabilities'])
            trace.update(task=task, raw_model_task=answer['choice'], probabilities=answer['probabilities'], decision_source=source)
            actual = {'task': task}
            if task == 'edit':
                answers = call('edit_details', prompts['secondary_input_prefix'] + state, prompts['edit_details'])
                if any(a['probabilities']['apply'] == a['probabilities']['skip'] for a in answers.values()):
                    raise ValueError('旧二级概率并列')
                actual['tasks'] = [k for k in answers if k not in LEGACY_EDIT_HINT_LABELS and answers[k]['choice'] == 'apply']
                actual['hints'] = [k for k in answers if k in LEGACY_EDIT_HINT_LABELS and answers[k]['choice'] == 'apply']
                if not actual['tasks']:
                    raise ValueError('旧二级无可执行任务')
        trace.update(task=actual['task'], edit_plan={k: v for k, v in actual.items() if k != 'task'} if actual['task'] == 'edit' else None)
        record['actual'] = actual
        # Normalize unambiguous archived labels for scoring only; never merge their scores.
        record['exact_match'] = actual['task'] == case['expected']['task'] and comparable_tasks(actual.get('tasks', [])) == comparable_tasks(case['expected'].get('tasks', []))
    except Exception as exc:
        record['error'] = str(exc)
        trace.update(error=str(exc), error_code=getattr(exc, 'code', 'request_or_response_error'), consistency='rejected')
    trace['latency_s'] = time.perf_counter() - start
    record['root_correct'] = trace.get('raw_model_task') == case['expected']['task']
    record['raw_exact_match'] = raw_joint_match(trace, case['expected']) if variant == 'C' else record['exact_match']
    return record


def metrics(records):
    def timing(items):
        times = sorted(r['trace']['latency_s'] for r in items)
        return {'n': len(times), 'median_s': statistics.median(times) if times else None,
                'p95_s': times[math.ceil(.95 * len(times)) - 1] if times else None}
    edits = [r for r in records if r['expected']['task'] == 'edit']
    timing_all = timing(records)
    return {'samples': len(records), 'root_correct': sum(r['root_correct'] for r in records),
            'exact_plan_correct': sum(r['exact_match'] for r in records),
            'raw_exact_plan_correct': sum(r.get('raw_exact_match', r['exact_match']) for r in records),
            'warnings': sum(bool(r['trace'].get('warning')) for r in records),
            'errors': sum('error' in r for r in records),
            'contradictions': sum(bool(({r['trace'].get('error_code')} | set(r['trace'].get('warning_codes', []))) & {'dictation_with_tasks', 'edit_without_tasks'}) for r in records),
            'requests': sum(r['trace']['requests'] for r in records), 'edit_samples': len(edits),
            'edit_plan_correct': sum(r['exact_match'] for r in edits),
            'latency_median_s': timing_all['median_s'], 'latency_p95_s': timing_all['p95_s'],
            'latency_by_expected_intent': {t: timing([r for r in records if r['expected']['task'] == t]) for t in ('dictation', 'edit')}}


def write_comparison(report, path):
    rows=[]
    for group, variants in report['reports'].items():
        for variant, entry in variants.items():
            m=entry['metrics'];lat=m['latency_by_expected_intent']
            cells=[group, VARIANTS[variant], f"{m['root_correct']}/{m['samples']}",
                   f"{m.get('raw_exact_plan_correct', m['exact_plan_correct'])}/{m['samples']}", f"{m['exact_plan_correct']}/{m['samples']}",
                   m.get('warnings', 0), m['contradictions'], m['errors'], m['requests']]
            for t in ('dictation', 'edit'):
                cells.append('—' if not lat[t]['n'] else f"{lat[t]['median_s']:.3f} / {lat[t]['p95_s']:.3f}")
            rows.append('<tr>'+''.join('<td>'+escape(str(c))+'</td>' for c in cells)+f'<td><a href="{escape(entry["html"])}">逐条输入、正误、全部概率</a></td></tr>')
    headers=['数据集','方案','原始总体意图正确','原始完整分类','最终处理匹配','标黄','原始矛盾','错误','请求数','听写 P50/P95 秒','编辑 P50/P95 秒','明细']
    content='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Clef 联合分类对照</title><style>body{font:15px/1.6 system-ui;margin:28px;color:#182128}table{border-collapse:collapse}td,th{border:1px solid #cbd5e1;padding:10px;text-align:left}th{background:#eef3f9}pre{white-space:pre-wrap}a{color:#245ba6}</style><h1>Clef 三方案对照</h1>'''
    content += '<p>'+escape(report['note'])+'</p><p>时间：'+escape(report['created_at'])+'</p>'
    content += '<table><tr>'+''.join('<th>'+h+'</th>' for h in headers)+'</tr>'+''.join(rows)+'</table>'
    content += '<p>按样例循环交错 A/B/C，起始方案轮换；每个方案先用已知听写和编辑各预热一次（不计分）。每样例每方案只测一次；延迟含 HTTP/SSH、服务器排队和处理，不含 ASR/OCR 采集、生成及写回。P95 样本较少，不能据此推断长期尾延迟。</p>'
    content += '<p>完整匹配统一比较总体意图与当前任务集合；旧 precise_edit→modify、verbatim_insert→insert、expand/condense→polish、structure→format 仅用于计分，原始概率保持不变。旧 organize 同时涵盖摘要和格式，不能仅凭其分数自动拆为新标签。旧方案两个 hint 保留展示但不计分。当前 C 在校验层听写优先、不明确时回退听写，原始分类与最终处理分开计分，矛盾保留并标黄。旧 A/B 保持其冻结校验方式；最终处理差异包含策略变化。所有概率为未经校准的模型分数。三方案提示词都在明细 JSON 冻结；A→B 只给一级补任务定义，B→C 还改变任务边界提示与请求结构，不能把差异全部归因于一次前向。</p>'
    if report.get('delivery_notes'):
        content += '<h2>实现与验证记录</h2><ul>' + ''.join('<li>'+escape(n)+'</li>' for n in report['delivery_notes']) + '</ul>'
    if report.get('artifacts'):
        content += '<p>' + ' · '.join('<a href="'+escape(v, quote=True)+'">'+escape(k)+'</a>' for k,v in report['artifacts'].items()) + '</p>'
    content += '<details><summary>版本与内容哈希</summary><pre>'+escape(json.dumps({k:report[k] for k in ('baseline_sha256','prompt_sha256','case_sha256','model_revision')},ensure_ascii=False,indent=2))+'</pre></details></html>'
    path.write_text(content)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint')
    parser.add_argument('--development', type=Path, action='append', help='Known development case files; repeatable')
    parser.add_argument('--holdout', type=Path, default=ROOT/'tools/data/clef_joint_holdout_cases.json')
    parser.add_argument('--output', type=Path, required=True)
    args=parser.parse_args()
    devfiles=args.development or [DEFAULT_CASES, DEFAULT_CASES.with_name('clef_primary_validation_cases.json'), DEFAULT_CASES.with_name('clef_primary_unseen_cases.json')]
    groups={'development': [c for p in devfiles for c in load_cases(p, require_plan=True)], 'holdout': load_cases(args.holdout,require_plan=True)}
    prompts, baseline_sha, revision=snapshots()
    router=ClefIntentRouter(endpoint=args.endpoint)
    router.warmup()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report={'created_at':datetime.now(timezone.utc).isoformat(),'baseline_sha256':baseline_sha,'model_revision':revision,
            'prompt_sha256':{v:digest(p) for v,p in prompts.items()}, 'case_sha256':{g:digest(c) for g,c in groups.items()},
            'note':'development 的 68 条曾用于调优；holdout 的 24 条在首次联合评测时未用于调优；后续复用需视为回归样例，但仍是开发者编写的合成样例。不是独立采集的真实用户准确率。', 'reports':{},'warmup':[]}
    results={g:{v:[] for v in VARIANTS} for g in groups}
    journal=args.output.with_suffix('.jsonl')
    with journal.open('w') as stream:
        try:
            for variant in VARIANTS:
                for task in ('dictation','edit'):
                    case=next(c for c in groups['development'] if c['expected']['task']==task)
                    record=run_case(router,case,variant,prompts[variant]);report['warmup'].append(record)
                    if record.get('error'): raise RuntimeError('Warmup failed: '+record['error'])
            for group,cases in groups.items():
                for index,case in enumerate(cases):
                    order=list(VARIANTS);order=order[index%3:]+order[:index%3]
                    for variant in order:
                        r=run_case(router,case,variant,prompts[variant]);results[group][variant].append(r)
                        stream.write(json.dumps({'group':group,**r},ensure_ascii=False)+'\n');stream.flush()
                        print(json.dumps({'group':group,'index':index+1,'id':case['id'],'variant':variant,'exact':r['exact_match'],'error':r.get('error'),'s':round(r['trace']['latency_s'],3)},ensure_ascii=False),flush=True)
        finally:
            router.close()
            for group,variants in results.items():
                report['reports'][group]={}
                for variant,records in variants.items():
                    if not records: continue
                    m=metrics(records);p=args.output.with_name(args.output.stem+f'-{group}-{variant}.json')
                    detail={'created_at':report['created_at'],'prompt_version':prompts[variant]['version'],'prompts':prompts[variant], 'metrics':m,'note':report['note'],'cases':records}
                    p.write_text(json.dumps(detail,ensure_ascii=False,indent=2)+'\n');write_routing_html(detail,p.with_suffix('.html'))
                    report['reports'][group][variant]={'metrics':m,'json':p.name,'html':p.with_suffix('.html').name}
            args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');write_comparison(report,args.output.with_suffix('.html'))
    print(json.dumps({g:{v:e['metrics'] for v,e in variants.items()} for g,variants in report['reports'].items()},ensure_ascii=False))


if __name__=='__main__':
    main()
