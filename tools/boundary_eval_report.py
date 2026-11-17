#!/usr/bin/env python3
"""Render a normalized, reviewed LLM evaluation as a standalone filterable HTML.

Adapted from llm-eval-report/scripts/render_report.py for a generation-only paired experiment.
Standard library only. This renderer never calls a model or supplies judgments.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import html
import json
import math
from pathlib import Path
from urllib.parse import urlsplit

STATES = {'pass': '通过', 'fail': '失败', 'uncertain': '待确认', 'pending': '未复核', 'observation': '观察'}
ESC = html.escape


def verdict(case):
    if case.get('observation'):
        return 'observation'
    if case.get('error') or (case.get('classification_applicable', True) and case['expected_labels'] != sorted(set(case['actual_labels']))) or case['review']['status'] == 'fail':
        return 'fail'
    return case['review']['status']


def validate(data):
    if data.get('schema_version') != 1 or not isinstance(data.get('cases'), list) or not data['cases']:
        raise ValueError('Require schema_version=1 and nonempty cases')
    seen = set()
    labels = data.get('labels', {})
    for case in data['cases']:
        cid = case.get('id')
        if not isinstance(cid, str) or not cid or cid in seen:
            raise ValueError('Duplicate/invalid case ID')
        seen.add(cid)
        for key in ('instruction', 'source_text', 'output', 'expected_result', 'group'):
            if not isinstance(case.get(key), str):
                raise ValueError(f'{cid}: missing string {key}')
        for key in ('expected_labels', 'actual_labels'):
            values = case.get(key)
            if not isinstance(values, list) or len(values) != len(set(values)) or any(v not in labels for v in values):
                raise ValueError(f'{cid}: invalid {key}')
        case['expected_labels'] = sorted(case['expected_labels'])
        if not case['expected_labels'] and not case.get('observation'):
            raise ValueError(f'{cid}: scored classification cases need expected labels')
        review = case.get('review', {})
        if review.get('status') not in STATES or not isinstance(review.get('note'), str) or not review['note'].strip():
            raise ValueError(f'{cid}: review status and note required; unreviewed is pending')
        digest = hashlib.sha256(case['output'].encode()).hexdigest()
        if review.get('output_sha256') != digest:
            raise ValueError(f'{cid}: review does not match output SHA-256')
        if review['status'] == 'observation' and not case.get('observation'):
            raise ValueError(f'{cid}: observation review requires observation case')
        if review['status'] == 'fail' and not review.get('issues'):
            raise ValueError(f'{cid}: failed body requires concrete issues')
        for issue in review.get('issues', []):
            if not all(isinstance(issue.get(k), str) and issue[k].strip() for k in ('reason', 'expected', 'actual')):
                raise ValueError(f'{cid}: issues need reason, expected, actual')
        latency = case.get('latency_s')
        if latency is not None and (not isinstance(latency, (int, float)) or not math.isfinite(latency) or latency < 0):
            raise ValueError(f'{cid}: invalid latency')
        if 'checks' in case and (not isinstance(case['checks'], list) or any(type(c.get('passed')) is not bool for c in case['checks'])):
            raise ValueError(f'{cid}: invalid mechanical checks')
        case['overall'] = verdict(case)
        case['route_match'] = None if case.get('observation') or not case.get('classification_applicable', True) else set(case['expected_labels']) == set(case['actual_labels'])
    return data


def percentile(values, q):
    values = sorted(values)
    return values[max(0, math.ceil(q * len(values)) - 1)] if values else None


def metrics(cases):
    scored = [c for c in cases if not c.get('observation')]
    times = [c['latency_s'] for c in scored if not c.get('error') and c.get('latency_s') is not None]
    reviews = Counter(c['review']['status'] for c in scored)
    states = Counter(c['overall'] for c in scored)
    return {'n': len(cases), 'scored': len(scored), 'observation': len(cases)-len(scored),
        'overall': dict(states), 'route_pass': sum(c['route_match'] is True for c in scored), 'route_scored': sum(c['route_match'] is not None for c in scored),
        'body': dict(reviews), 'errors': sum(bool(c.get('error')) for c in scored),
        'mechanical_mismatch': sum(any(not x['passed'] for x in c.get('checks', [])) for c in scored),
        'latency': {'n': len(times), 'p50': percentile(times, .5), 'p95': percentile(times, .95),
                    'mean': sum(times)/len(times) if times else None, 'max': max(times) if times else None}}


def percentage(n, d):
    return f'{n/d:.1%}' if d else '—'


def seconds(value):
    return f'{value:.3f} s' if value is not None else '—'


def safe_link(href):
    parsed = urlsplit(href)
    return parsed.scheme in ('', 'https', 'http', 'file') and not href.startswith('//')


def label_text(values, labels):
    return '、'.join(labels[v] for v in values) or '无类别'


def table(heads, rows):
    return '<table><thead><tr>' + ''.join('<th>'+ESC(str(h))+'</th>' for h in heads) + '</tr></thead><tbody>' + ''.join(
        '<tr>' + ''.join('<td>'+ESC(str(v))+'</td>' for v in row) + '</tr>' for row in rows) + '</tbody></table>'


CSS = '''
:root{--ink:#172c37;--muted:#5b6f78;--line:#d9e3e7;--teal:#086c63;--red:#b03d36;--amber:#8d600e}*{box-sizing:border-box}html{scroll-padding-top:135px}body{margin:0;color:var(--ink);background:#f2f5f5;font:15px/1.75 system-ui,-apple-system,"PingFang SC",sans-serif}main{max-width:1240px;margin:auto;padding:30px 24px 70px}a{color:var(--teal);text-underline-offset:4px}h1{font-size:34px;line-height:1.35;margin:8px 0 16px}h2{font-size:23px;margin:30px 0 14px}h3{font-size:17px;margin:0 0 8px;overflow-wrap:anywhere}p{margin:8px 0 14px}.eyebrow{font-size:12px;letter-spacing:.14em;color:var(--teal);font-weight:700}.muted{color:var(--muted)}.panel,article{background:white;border:1px solid var(--line);border-radius:12px;padding:24px;margin:18px 0}.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.kpi{background:white;border:1px solid var(--line);border-radius:10px;padding:18px}.kpi b{display:block;font-size:27px;color:var(--teal)}.kpi small{color:var(--muted)}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.field{min-width:0}.label{font-size:12px;color:var(--muted);font-weight:650;letter-spacing:.03em}.text{white-space:pre-wrap;overflow-wrap:anywhere;margin:6px 0 12px;background:#f6f8f9;padding:12px 14px;border-radius:6px}.output{background:#edf5f3}.case-head{display:flex;justify-content:space-between;align-items:flex-start;gap:14px}.badge{display:inline-block;white-space:nowrap;font-size:12px;padding:3px 10px;border-radius:30px;background:#e6f2ee;color:var(--teal)}.badge.fail{background:#f9e9e6;color:var(--red)}.badge.uncertain,.badge.pending{background:#fcf1d8;color:var(--amber)}.badge.observation{background:#edf0f3;color:#5b6f78}article[data-status=fail]{border-left:4px solid var(--red)}article[data-status=uncertain]{border-left:4px solid #c58e30}.reason{background:#fff1ee;padding:14px 16px;border-radius:7px;border-left:3px solid #c55445;margin:12px 0}.reason p{margin:4px 0}.reason.uncertain{background:#fff8e8;border-color:#c58e30}.filters{position:sticky;top:0;z-index:3;background:#f2f5f5f5;backdrop-filter:blur(10px);padding:14px 0;border-bottom:1px solid var(--line);display:flex;gap:8px;flex-wrap:wrap;align-items:center}select,input,button{font:inherit;font-size:14px;padding:9px 11px;border:1px solid #bfced4;border-radius:7px;background:white;color:var(--ink)}input{flex:1;min-width:180px}button{cursor:pointer}#count{font-size:13px;color:var(--muted)}table{border-collapse:collapse;width:100%;font-size:14px}td,th{text-align:left;padding:10px 12px;border-bottom:1px solid var(--line)}th{background:#eef3f4}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f7f8;padding:14px;border-radius:6px;font:12px/1.7 ui-monospace,monospace}details{border-top:1px solid var(--line);padding-top:12px;margin-top:14px}summary{cursor:pointer;color:var(--teal);font-weight:600}.links{display:flex;gap:16px;flex-wrap:wrap;font-size:14px}.note{border-left:3px solid var(--teal);padding:10px 16px;background:#eaf3f0}.metrics-wrap{overflow:auto}.flag{font-size:12px;margin-right:10px;color:var(--muted)}[hidden]{display:none!important}#empty{padding:25px;text-align:center;color:var(--muted)}@media(max-width:720px){main{padding:20px 12px 50px}h1{font-size:26px}.kpis{grid-template-columns:repeat(2,1fr)}.grid{grid-template-columns:1fr;gap:0}.panel,article{padding:18px 14px}.filters{position:static}th,td{padding:8px 6px}.case-head{gap:8px}.kpi b{font-size:23px}}
'''

JS = '''
const cards=Array.from(document.querySelectorAll('article.case'));
const controls=['result','group','tasks','search'].map(id=>document.getElementById(id));
function matches(c,result,group,tasks,q){
 const resultOk=result==='all'||c.dataset.status===result||(result==='routefail'&&c.dataset.route==='false')||(result==='bodyfail'&&c.dataset.body==='fail'&&c.dataset.status!=='observation')||(result==='mechanical'&&c.dataset.mechanical==='true')||(['improved','regressed','stablefail','stablepass'].includes(result)&&c.dataset.change===result)||(result==='oldroutefail'&&c.dataset.oldroute==='false');
 return resultOk&&(group==='all'||c.dataset.group===group)&&(tasks==='all'||(tasks==='multi'?Number(c.dataset.tasks)>=2:Number(c.dataset.tasks)===Number(tasks)))&&(!q||c.dataset.search.toLowerCase().includes(q));
}
function applyFilters(){const [result,group,tasks,q0]=controls.map(x=>x.value);const q=q0.trim().toLowerCase();let n=0;cards.forEach(c=>{c.hidden=!matches(c,result,group,tasks,q);if(!c.hidden)n++;});document.getElementById('count').textContent=`显示 ${n} / ${cards.length}`;document.getElementById('empty').hidden=n!==0;}
controls.forEach(c=>c.addEventListener(c.tagName==='INPUT'?'input':'change',applyFilters));
document.getElementById('reset').addEventListener('click',()=>{controls.forEach(c=>c.value=c.tagName==='INPUT'?'':'all');applyFilters();});
applyFilters();
'''


def render(data):
    data = validate(data)
    cases, labels = data['cases'], data['labels']
    stats = metrics(cases)
    n = stats['scored']
    group_names = data.get('groups', {})
    groups = list(dict.fromkeys(c['group'] for c in cases))
    summary = data.get('summary', [])
    if not isinstance(summary, list) or not all(isinstance(s, str) for s in summary):
        raise ValueError('summary must be a list of factual paragraphs')
    out = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
        '<title>'+ESC(data['title'])+'</title><style>'+CSS+'</style></head><body><main>',
        '<header><div class="eyebrow">LLM EVALUATION · COMPLETE CASE REVIEW</div><h1>'+ESC(data['title'])+'</h1></header>',
        '<section class="panel" id="overview"><h2 style="margin-top:0">整体特征</h2>'+''.join('<p>'+ESC(p)+'</p>' for p in summary),
        '<p class="note">'+ESC(data.get('methodology',''))+'</p><div class="links">']
    for link in data.get('links', []):
        if not safe_link(link['href']):
            raise ValueError('Unsafe link scheme')
        out.append('<a href="'+ESC(link['href'], quote=True)+'">'+ESC(link['label'])+'</a>')
    baseline_stats = metrics([dict(c,review=c['baseline']['review'],overall=c['baseline']['review']['status'],latency_s=c['baseline']['latency_s'],error=c['baseline'].get('error','')) for c in cases])
    changes = Counter(c['change'] for c in cases if not c.get('observation'))
    stats['baseline_body']=baseline_stats['body']
    stats['baseline_latency']=baseline_stats['latency']
    stats['changes']=dict(changes)
    out.extend(['</div></section><div class="kpis">',
        f'<div class="kpi">无分类：正文通过<b>{stats["body"].get("pass",0)} / {n}</b><small>{percentage(stats["body"].get("pass",0),n)} · 失败 {stats["body"].get("fail",0)} · 待确认 {stats["body"].get("uncertain",0)}</small></div>',
        f'<div class="kpi">六类 v5：正文通过<b>{baseline_stats["body"].get("pass",0)} / {n}</b><small>仅比较正文，旧分类错误不扣此分</small></div>',
        f'<div class="kpi">修复 / 回退<b>{changes["improved"]} / {changes["regressed"]}</b><small>旧失败→通过 / 旧通过→失败</small></div>',
        f'<div class="kpi">无分类 P50 / P95<b>{seconds(stats["latency"]["p50"])}</b><small>P95 {seconds(stats["latency"]["p95"])} · 含网络</small></div></div>',
        '<div class="panel"><h3>样例分布与正文表现</h3><div class="metrics-wrap">'])
    rows=[]
    for group in groups:
        subset=[c for c in cases if c['group']==group]
        m=metrics(subset)
        rows.append((group_names.get(group,group),m['n'],sum(c['baseline']['review']['status']=='pass' for c in subset),
            m['body'].get('pass',0),m['overall'].get('fail',0),m['overall'].get('uncertain',0),seconds(m['latency']['p95'])))
    out.append(table(['分组','样例数','旧正文通过','新正文通过','新失败','新待确认','新 P95'],rows))
    rows=[]
    for size in sorted({len(c['expected_labels']) for c in cases if not c.get('observation')}):
        subset=[c for c in cases if not c.get('observation') and len(c['expected_labels'])==size]
        m=metrics(subset)
        rows.append((f'{size} 项离线标签',m['scored'],sum(c['baseline']['review']['status']=='pass' for c in subset),m['body'].get('pass',0),m['overall'].get('fail',0)))
    out.append('<h3 style="margin-top:20px">按旧样例标签数量分组</h3>'+table(['样例特征','样例数','旧正文通过','新正文通过','新失败'],rows))
    out.append('</div><p class="muted">本组不要求、不推断模型分类，分类准确率不适用。旧预期标签仅用于离线分组，未发送给模型。失败＝请求异常或正文复核失败；待确认不计通过；6 条旧 guard 单独观察。</p></div>')
    out.append('<section class="panel" id="prompt"><h2>完整发送提示词</h2><p>所有 216 次请求使用同一个 system prompt。每次 user 消息仅包含 utterance、editable_text、screen_context；预期、标签和评语不进入请求。</p><details><summary>展开完整 system prompt（无删节）</summary><pre>'+ESC(data['metadata']['prompt'])+'</pre></details></section>')
    out.append('<h2 id="cases">逐例：无分类结果、预期与六类 v5 对照</h2><div class="filters"><select id="result" aria-label="结果筛选">')
    for key,text in [('all','全部结果'),('fail','仅失败'),('pass','仅通过'),('uncertain','待确认'),('pending','未复核'),('observation','观察例'),('improved','修复：旧失败→通过'),('regressed','回退：旧通过→失败'),('stablefail','两组都失败'),('stablepass','两组都通过'),('oldroutefail','旧路由错误的 8 条'),('bodyfail','正文失败'),('mechanical','机械断言差异')]:
        out.append(f'<option value="{key}">{text}</option>')
    out.append('</select><select id="group" aria-label="样例组"><option value="all">全部分组</option>'+''.join('<option value="'+ESC(g,quote=True)+'">'+ESC(group_names.get(g,g))+'</option>' for g in groups)+'</select>')
    out.append('<select id="tasks" aria-label="旧样例标签数量"><option value="all">全部离线标签数</option><option value="multi">多要求组合</option>'+''.join(f'<option value="{s}">{s} 类</option>' for s in sorted({len(c['expected_labels']) for c in cases if c['expected_labels']}))+'</select><input id="search" aria-label="搜索样例" placeholder="搜索口述、结果、ID、失败原因…"><button id="reset">重置</button><span id="count" role="status"></span></div><p id="empty" hidden>没有匹配的样例。</p>')
    for index,c in enumerate(cases,1):
        state=c['overall']; review=c['review']; mismatch=any(not x['passed'] for x in c.get('checks',[]))
        search=json.dumps([c['id'],c['instruction'],c['source_text'],c.get('context'),c['expected_result'],c['output'],review['note'],c.get('route_note'),c.get('features'),label_text(c['expected_labels'],labels),label_text(c['actual_labels'],labels)],ensure_ascii=False)
        out.append(f'<article class="case" id="case-{index}" data-status="{state}" data-route="{str(c["route_match"]).lower()}" data-body="{review["status"]}" data-change="{c["change"]}" data-oldroute="{str(c["baseline"]["route_match"]).lower()}" data-mechanical="{str(mismatch).lower()}" data-group="{ESC(c["group"],quote=True)}" data-tasks="{len(c["expected_labels"])}" data-search="{ESC(search,quote=True)}">')
        out.append(f'<div class="case-head"><div><h3>{index:03} · {ESC(c["id"])}</h3><p class="muted">{ESC(group_names.get(c["group"],c["group"]))} · {seconds(c.get("latency_s"))} · {ESC(" / ".join(c.get("features",[])))}</p></div><span class="badge {state}">{STATES[state]}</span></div>')
        out.append('<div class="grid">')
        for title, value in [('离线样例标签（不发送）',label_text(c['expected_labels'],labels) if not c.get('observation') else '历史边界观察'),('本轮模型分类','不适用：请求直接返回正文，没有类别输出'),('本次口述',c['instruction']),('可编辑原文',c['source_text'] or '【空原文】'),('预期结果与判定依据',c['expected_result']),('模型实际正文',c['output'] if not c.get('error') else '【请求或解析异常，见原因】')]:
            out.append('<div class="field"><div class="label">'+title+'</div><div class="text'+(' output' if title=='模型实际正文' else '')+'">'+ESC(value)+'</div></div>')
        out.append('</div>')
        b=c['baseline']
        change_names={'improved':'修复：旧失败→通过','regressed':'回退：旧通过→失败','stablefail':'两组都失败','stablepass':'两组都通过','uncertain':'至少一组待确认','observation':'观察，不计主分母','pending':'未复核'}
        out.append('<div class="note"><strong>'+ESC(change_names.get(c['change'],c['change']))+'</strong> · 六类 v5 正文：'+STATES[b['review']['status']]+' · '+seconds(b['latency_s'])+' → '+seconds(c['latency_s'])+'</div><div class="grid"><div><div class="label">六类 v5 的实际正文</div><div class="text">'+ESC(b['output'])+'</div></div><div><div class="label">六类 v5 的复核依据与分类</div><div class="text">'+ESC(b['review']['note'])+'\n原分类：'+ESC(label_text(b['actual_labels'],labels))+'\n分类集合：'+('匹配' if b['route_match'] else '不匹配' if b['route_match'] is False else '不计分')+'</div></div></div>')
        if c.get('context'):
            context=c['context'] if isinstance(c['context'],str) else json.dumps(c['context'],ensure_ascii=False,indent=2)
            out.append('<div class="field"><div class="label">给定只读上下文</div><div class="text">'+ESC(context)+'</div></div>')
        if c['route_match'] is False:
            missing=sorted(set(c['expected_labels'])-set(c['actual_labels'])); extra=sorted(set(c['actual_labels'])-set(c['expected_labels']))
            desc=[]
            if missing:desc.append('漏选：'+label_text(missing,labels))
            if extra:desc.append('多选：'+label_text(extra,labels))
            out.append('<div class="reason"><strong>分类差异原因（按冻结预期）</strong><p>'+ESC('；'.join(desc))+'</p><p>'+ESC(c.get('route_note') or '与调用前固定的预期任务集合不一致；具体任务依据见上方预期结果。')+'</p></div>')
        if c.get('error'):
            out.append('<div class="reason"><strong>调用／解析失败原因</strong><p>'+ESC(c['error'])+'</p></div>')
        if review['status'] in ('fail','uncertain'):
            out.append('<div class="reason '+('uncertain' if review['status']=='uncertain' else '')+'"><strong>'+('正文失败原因' if review['status']=='fail' else '需要确认的原因')+'</strong><p>'+ESC(review['note'])+'</p>')
            for issue in review.get('issues',[]):
                out.append('<p><b>'+ESC(issue['reason'])+'</b><br>应当：'+ESC(issue['expected'])+'<br>实际：'+ESC(issue['actual'])+'</p>')
            out.append('</div>')
        else:
            out.append('<p><span class="badge '+review['status']+'">正文复核：'+STATES[review['status']]+'</span> '+ESC(review['note'])+'</p>')
        if c.get('checks'):
            out.append('<details><summary>机械检查：'+('存在差异，须结合语义复核' if mismatch else '全部满足')+'</summary><pre>'+ESC(json.dumps(c['checks'],ensure_ascii=False,indent=2))+'</pre></details>')
        out.append('<details><summary>完整证据与原始模型返回</summary><pre>'+ESC(json.dumps(c.get('evidence',{}),ensure_ascii=False,indent=2))+'</pre></details></article>')
    out.append('<section class="panel"><details><summary>评测元数据与统计口径</summary><pre>'+ESC(json.dumps({'metadata':data.get('metadata',{}),'metrics':stats},ensure_ascii=False,indent=2))+'</pre></details></section><script>'+JS+'</script></main></body></html>')
    return ''.join(out), stats


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    data=json.loads(args.input.read_text())
    rendered,stats=render(data)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(rendered)
    args.output.with_suffix('.metrics.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(stats,ensure_ascii=False))


if __name__=='__main__':main()
