#!/usr/bin/env python3
"""Paired, interleaved joint/root-only/speculative split routing benchmark."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html import escape
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from proximic_ring.text_processing.intent_router import ClefIntentRouter
from proximic_ring.text_processing.router_prompts import ROUTER_PROMPT_VERSION
from proximic_ring.text_processing.task_routing import EDIT_TASK_LABELS
try:
    from .clef_cases import load_cases
    from .clef_split_routing import VERSION, requests_for, run_case
except ImportError:
    from clef_cases import load_cases
    from clef_split_routing import VERSION, requests_for, run_case

MODES = {"joint": "v19 联合分类", "root": "独立听写／编辑", "parallel": "同时发送两个请求"}
NOTE = ("同一批已参与调优的 130 条开发样例，不是独立测试准确率。按样例交错三种方案、轮换起始顺序；"
        "并发分支用两个客户端线程同时放行，服务保持原配置。听写结果返回时不等待细分，随后仅为审计收集细分并排空，"
        "再测下一条。延迟含本地调度、HTTP/SSH、服务器排队及处理，不含 ASR、OCR 采集、文本生成或写回。"
        "排空耗时说明被忽略的细分仍占用服务器；本实验不代表连续用户流量或持续负载。概率为未经校准的模型分数。")


def timing(values):
    values = sorted(v for v in values if v is not None)
    return {"n": len(values), "p50_s": statistics.median(values) if values else None,
            "p95_s": values[math.ceil(.95 * len(values)) - 1] if values else None}


def metrics(records):
    valid = [r for r in records if "error" not in r]
    return {"n": len(records), "root_correct": sum(r["root_correct"] for r in records),
            "exact_correct": sum(r["exact_match"] is True for r in records) if records and records[0]["mode"] != "root" else None,
            "errors": sum("error" in r for r in records),
            "details_errors": sum("details_error" in r for r in records),
            "warnings": sum(bool(r.get("policy_after_details", r.get("policy", {})).get("warning")) for r in records),
            "completion_order": {order: sum(r.get("completion_order") == order for r in records) for order in ("root_first", "details_first")},
            "by_expected_intent": {t: {field: timing([r.get(field) for r in valid if r["expected"]["task"] == t])
                for field in ("root_ready_s", "decision_ready_s", "drain_s")} for t in ("dictation", "edit")}}


def paired_comparison(records, candidate):
    lookup = {(r["id"], r["mode"]): r for r in records}
    result = {}
    for intent in ("dictation", "edit"):
        pairs = []
        for (case_id, mode), baseline in lookup.items():
            if mode != "joint" or baseline["expected"]["task"] != intent:
                continue
            other = lookup.get((case_id, candidate))
            if other and "error" not in baseline and "error" not in other:
                pairs.append((baseline["root_ready_s"], other["root_ready_s"]))
        if not pairs:
            continue
        # Resample paired cases, never independently resample the two variants.
        deltas = [a-b for a,b in pairs]
        rng = random.Random(20261105)
        boot = sorted(statistics.median(rng.choices(deltas, k=len(deltas))) for _ in range(2000))
        result[intent] = {"n": len(pairs), "median_paired_saving_s": statistics.median(deltas),
            "median_paired_reduction_percent": statistics.median((a-b)/a*100 for a,b in pairs),
            "bootstrap_95_ci_s": [boot[49], boot[1949]], "faster_cases": sum(d>0 for d in deltas)}
    return result


def cell(value):
    return "—" if value is None else f"{value:.3f}"


def latency_cell(metric):
    return f'{cell(metric["p50_s"])} / {cell(metric["p95_s"])}'


def write_html(report, path):
    e = lambda value: escape(str(value), quote=True)
    rows = []
    for mode, m in report["metrics"].items():
        fields = [MODES[mode], f'{m["root_correct"]}/{m["n"]}',
                  "未测细分" if m["exact_correct"] is None else f'{m["exact_correct"]}/{m["n"]}', m["errors"], m["warnings"]]
        for intent in ("dictation", "edit"):
            fields += [latency_cell(m["by_expected_intent"][intent][key]) for key in ("root_ready_s", "decision_ready_s", "drain_s")]
        rows.append("<tr>"+"".join(f"<td>{e(v)}</td>" for v in fields)+"</tr>")
    headers = ["方案", "总体判断正确", "最终任务匹配", "请求错误", "标黄"]
    for intent in ("听写", "编辑"):
        headers += [f"{intent}总体 P50/P95 秒", f"{intent}可继续后续 P50/P95 秒", f"{intent}全部排空 P50/P95 秒"]
    html = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Clef v20 拆分延迟实验</title>',
        '<style>body{font:15px/1.6 system-ui;margin:28px;color:#1e293b}table{border-collapse:collapse;width:100%;margin:16px 0}td,th{border:1px solid #cbd5e1;padding:8px;text-align:left}th{background:#edf2f7}pre{white-space:pre-wrap;overflow-wrap:anywhere}.bad{background:#fee2e2}.warn{background:#fef3c7}.scroll{overflow:auto}details{margin:12px 0;padding:12px;border:1px solid #cbd5e1}summary{cursor:pointer}small{color:#475569}</style>',
        '<h1>Clef v20：总体判断独立与并发细分实验</h1>', '<p>'+e(report["note"])+"</p>",
        '<p>当前应用仍使用 v19；本报告测试实验入口。独立总体请求只返回听写／编辑，不伪造细类结果。'
        '“可继续后续”是有足够结果完成路由的时刻；编辑必须等细分，听写无需等细分。“排空”是两个请求都结束。'
        '分组按期望类别，错误预测也计入该类别的延迟；接口失败另计，不纳入成功延迟。</p>',
        '<div class="scroll"><table><tr>'+"".join(f"<th>{h}</th>" for h in headers)+"</tr>"+"".join(rows)+"</table></div>",
        '<h2>同样例配对延迟差</h2><p>正数表示比本轮 v19 更快。区间为对样例配对重采样 2000 次的中位差区间；每样例每方案一轮，不代表长期网络尾延迟或真实用户泛化。</p>',
        '<pre>'+e(json.dumps(report["paired"],ensure_ascii=False,indent=2))+'</pre>',
        '<h2>提示词与硬约束</h2><p>共同意图边界、总体 criteria、六类 description/criteria 复用当前 v19。总体请求补充本次可用类别的简短定义，'
        '不再带六类 apply/skip 问题；细分分支保留完整类别 criteria 和组合规则，改为假设编辑的预计算。无原文时，细分 schema 不含 modify，总体参考定义也不含修改。'
        '假设编辑不强迫命中：转述、计划、照录仍允许全 skip。总体听写立即采用，随后发现细分 apply 只标黄；总体编辑时沿用全 skip／并列回退听写规则。</p>',
        '<p><a href="'+e(path.with_suffix('.json').name)+'">完整 JSON（实际输入、schema、每项概率、时间）</a></p>',
        '<h2>错误样例</h2>']
    failures = [r for r in report["cases"] if not r["root_correct"] or r["exact_match"] is False or "error" in r]
    if not failures:
        html.append('<p>本轮没有总体或最终任务匹配错误。</p>')
    else:
        html.append('<table><tr><th>样例／方案</th><th>口述／原文</th><th>期望</th><th>实际</th><th>总体概率</th></tr>')
        for r in failures:
            values=[r["id"]+" / "+MODES[r["mode"]],r["utterance"]+"\n原文："+r["target"],
                    json.dumps(r["expected"],ensure_ascii=False),json.dumps(r.get("actual",r.get("root_actual",r.get("error"))),ensure_ascii=False),
                    json.dumps(r.get("root_probabilities",{}),ensure_ascii=False)]
            html.append('<tr class="bad">'+''.join('<td>'+e(v)+'</td>' for v in values)+'</tr>')
        html.append('</table>')
    html.append('<h2>全部样例：输入、正误和每类全部概率</h2>')
    grouped = {}
    for r in report["cases"]:
        grouped.setdefault(r["id"],[]).append(r)
    for case_id, records in grouped.items():
        case = records[0]
        bad = any(not r["root_correct"] or r["exact_match"] is False for r in records)
        warn = any(r.get("policy_after_details",r.get("policy",{})).get("warning") for r in records)
        cls = 'bad' if bad else 'warn' if warn else ''
        html.append(f'<details class="{cls}"'+(' open' if bad else '')+'><summary>'+e(case_id+'：'+case['utterance'])+'</summary>')
        html.append('<pre>'+e(json.dumps({k:case[k] for k in ('utterance','target','context','expected')},ensure_ascii=False,indent=2))+'</pre>')
        for r in sorted(records,key=lambda v:list(MODES).index(v['mode'])):
            html.append('<h3>'+e(MODES[r['mode']])+'</h3><p>'+e('总体：'+str(r.get('root_actual'))+'；最终：'+str(r.get('actual','仅测总体'))+'；总体正确：'+str(r['root_correct'])+'；完整匹配：'+str(r['exact_match']))+'</p>')
            if r.get('error') or r.get('details_error'):
                html.append('<p class="bad">'+e(r.get('error',r.get('details_error')))+'</p>')
            policy = r.get('policy_after_details',r.get('policy',{}))
            if policy.get('warning'):
                html.append('<p class="warn">'+e(policy['warning'])+'</p>')
            html.append('<p>'+e('总体返回 '+cell(r.get('root_ready_s'))+' 秒；后续可继续 '+cell(r.get('decision_ready_s'))+' 秒；排空 '+cell(r.get('drain_s'))+' 秒；'+r.get('completion_order',''))+'</p>')
            html.append('<table><tr><th>分支</th><th>分类</th><th>模型选择</th><th>所有选项概率</th></tr>')
            for branch, stage in r['stages'].items():
                for name, answer in stage.get('answers',{}).items():
                    probs=' / '.join(f'{k}: {v*100:.2f}%' for k,v in answer['probabilities'].items())
                    html.append('<tr>'+''.join('<td>'+e(v)+'</td>' for v in (branch,EDIT_TASK_LABELS.get(name,'总体意图'),answer['choice'],probs))+'</tr>')
            html.append('</table>')
            if r['task_availability']['excluded_tasks']:
                html.append('<p>修改：不可用（无可编辑原文，未参与分类，无模型概率）。</p>')
            html.append('<details><summary>本次实际请求</summary><pre>'+e(json.dumps(r['requests'],ensure_ascii=False,indent=2))+'</pre></details>')
        html.append('</details>')
    html.append('<details><summary>复现信息</summary><pre>'+e(json.dumps({k:v for k,v in report.items() if k not in ('cases','warmup','metrics','paired')},ensure_ascii=False,indent=2))+'</pre></details></html>')
    path.write_text(''.join(html))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint')
    parser.add_argument('--cases',type=Path,default=ROOT/'docs/reports/clef-six-tasks-cases-2026-11-04.json')
    parser.add_argument('--case',action='append')
    parser.add_argument('--modes',nargs='+',choices=tuple(MODES),default=list(MODES))
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--validate-only',action='store_true')
    parser.add_argument('--dump-request',action='store_true')
    args=parser.parse_args()
    cases=load_cases(args.cases,args.case,require_plan=True)
    if args.validate_only:
        print(f'CASES_OK: {len(cases)}');return 0
    if args.dump_request:
        print(json.dumps({c['id']:requests_for(c) for c in cases},ensure_ascii=False,indent=2));return 0
    args.output.parent.mkdir(parents=True,exist_ok=True)
    report={'version':VERSION,'baseline':ROUTER_PROMPT_VERSION,'created_at':datetime.now(timezone.utc).isoformat(),
            'note':NOTE.replace('130 条',f'{len(cases)} 条'),'cases_path':str(args.cases.resolve()),
            'case_sha256':hashlib.sha256(args.cases.read_bytes()).hexdigest(),
            'source_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (
                ROOT/'tools/clef_split_routing.py',Path(__file__),ROOT/'src/proximic_ring/text_processing/router_prompts.py',
                ROOT/'src/proximic_ring/text_processing/task_routing.py',ROOT/'src/proximic_ring/text_processing/intent_router.py')},
            'modes':args.modes,'warmup':[],'cases':[]}
    routers=[ClefIntentRouter(endpoint=args.endpoint) for _ in range(2)]
    for router in routers:router.warmup()
    try:
        with ThreadPoolExecutor(max_workers=2,thread_name_prefix='clef-split-client') as pool, args.output.with_suffix('.jsonl').open('w') as journal:
            for mode in args.modes:
                for intent in ('dictation','edit'):
                    case=next((c for c in cases if c['expected']['task']==intent),cases[0])
                    r=run_case(routers,pool,case,mode);report['warmup'].append(r)
                    if r.get('error') or r.get('details_error'):raise RuntimeError('Warmup failed: '+str(r.get('error',r.get('details_error'))))
            for index,case in enumerate(cases):
                n=len(args.modes);order=args.modes[index%n:]+args.modes[:index%n]
                for mode in order:
                    r=run_case(routers,pool,case,mode,details_first=bool(index%2));report['cases'].append(r)
                    journal.write(json.dumps(r,ensure_ascii=False)+'\n');journal.flush()
                    print(json.dumps({'index':index+1,'n':len(cases),'id':case['id'],'mode':mode,'root_correct':r['root_correct'],
                                      'exact':r['exact_match'],'root_s':r.get('root_ready_s'),'ready_s':r.get('decision_ready_s'),
                                      'order':r.get('completion_order'),'error':r.get('error')},ensure_ascii=False),flush=True)
    finally:
        for router in routers:router.close()
        report['metrics']={mode:metrics([r for r in report['cases'] if r['mode']==mode]) for mode in args.modes}
        report['paired']={mode:paired_comparison(report['cases'],mode) for mode in args.modes if mode!='joint'}
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        write_html(report,args.output.with_suffix('.html'))
    print(json.dumps(report['metrics'],ensure_ascii=False))
    return 1 if any(r.get('error') for r in report['cases']) else 0


if __name__=='__main__':
    raise SystemExit(main())
