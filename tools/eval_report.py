#!/usr/bin/env python3
"""Render a normalized, reviewed LLM evaluation as a standalone filterable HTML.

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
    if case.get('error') or case.get('route_match') is False or case['review']['status'] == 'fail':
        return 'fail'
    return case['review']['status']


def validate(data):
    if data.get('schema_version') != 1 or not isinstance(data.get('cases'), list) or not data['cases']:
        raise ValueError('Require schema_version=1 and nonempty cases')
    evaluation = data.setdefault('evaluation', {'classification': 'labels' in data})
    if not isinstance(evaluation, dict) or type(evaluation.get('classification')) is not bool:
        raise ValueError('evaluation.classification must be boolean')
    classification = evaluation['classification']
    if not classification and 'labels' in data:
        raise ValueError('Generation-only report must omit labels')
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
        if classification:
            for key in ('expected_labels', 'actual_labels'):
                values = case.get(key)
                if not isinstance(values, list) or len(values) != len(set(values)) or any(v not in labels for v in values):
                    raise ValueError(f'{cid}: invalid {key}')
            case['expected_labels'] = sorted(case['expected_labels'])
            if not case['expected_labels'] and not case.get('observation'):
                raise ValueError(f'{cid}: scored classification cases need expected labels')
            case['route_match'] = None if case.get('observation') else set(case['expected_labels']) == set(case['actual_labels'])
        elif any(k in case for k in ('expected_labels', 'actual_labels', 'route_note', 'route_match')):
            raise ValueError(f'{cid}: omit classification fields for generation-only evaluation')
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
        for field in case.get('fields', []):
            if not all(isinstance(field.get(k), str) for k in ('label', 'value')):
                raise ValueError(f'{cid}: fields need string label and value')
        for detail in case.get('details', []):
            if not all(isinstance(detail.get(k), str) for k in ('title', 'text')):
                raise ValueError(f'{cid}: details need string title and text')
        for a in case.get('assessments', []):
            if a.get('status') not in STATES or not all(isinstance(a.get(k),str) and a[k].strip() for k in ('label','expected','actual')):
                raise ValueError(f'{cid}: assessments need label, status, expected, actual')
            if review['status']=='pass' and a['status'] not in ('pass','observation'):
                raise ValueError(f'{cid}: passing review contradicts requirement assessment')
        case['overall'] = verdict(case)
    for block in data.get('analysis_tables', []):
        if not isinstance(block.get('title'),str) or not isinstance(block.get('columns'),list) or not block['columns'] or not isinstance(block.get('rows'),list):
            raise ValueError('analysis_tables need title, columns and rows')
        if any(not isinstance(row,list) or len(row)!=len(block['columns']) for row in block['rows']):
            raise ValueError('analysis table row width mismatch')
    return data


def percentile(values, q):
    values = sorted(values)
    return values[max(0, math.ceil(q * len(values)) - 1)] if values else None


def metrics(cases):
    scored = [c for c in cases if not c.get('observation')]
    times = [c['latency_s'] for c in scored if not c.get('error') and c.get('latency_s') is not None]
    reviews = Counter(c['review']['status'] for c in scored)
    states = Counter(c['overall'] for c in scored)
    result = {'n': len(cases), 'scored': len(scored), 'observation': len(cases)-len(scored),
        'overall': dict(states),
        'body': dict(reviews), 'errors': sum(bool(c.get('error')) for c in scored),
        'mechanical_mismatch': sum(any(not x['passed'] for x in c.get('checks', [])) for c in scored),
        'latency': {'n': len(times), 'p50': percentile(times, .5), 'p95': percentile(times, .95),
                    'mean': sum(times)/len(times) if times else None, 'max': max(times) if times else None}}
    if any('route_match' in c for c in cases):
        result['route_pass'] = sum(c.get('route_match') is True for c in scored)
    return result


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
function wasFailureNowAccepted(c){
 return c.dataset.initialStatus==='fail' && c.dataset.reviewed==='true' && c.dataset.status==='pass';
}
function matches(c,result,group,tasks,q){
 const resultOk=result==='all'||c.dataset.status===result||
   (result==='fail' && c.dataset.initialStatus==='fail' && c.dataset.status!=='observation')||
   (result==='reviewed-fail' && wasFailureNowAccepted(c))||(result==='routefail'&&c.dataset.route==='false')||(result==='bodyfail'&&(c.dataset.body==='fail'||c.dataset.initialBody==='fail')&&c.dataset.status!=='observation')||(result==='mechanical'&&c.dataset.mechanical==='true');
 return resultOk&&(group==='all'||c.dataset.group===group)&&(tasks==='all'||(tasks==='multi'?Number(c.dataset.tasks)>=2:Number(c.dataset.tasks)===Number(tasks)))&&(!q||c.dataset.search.toLowerCase().includes(q));
}
function applyFilters(){const [result,group,tasks,q0]=controls.map(x=>x?x.value:'all');const q=q0.trim().toLowerCase();let n=0;cards.forEach(c=>{c.hidden=!matches(c,result,group,tasks,q);if(!c.hidden)n++;});document.getElementById('count').textContent=`显示 ${n} / ${cards.length}`;document.getElementById('empty').hidden=n!==0;}
controls.filter(Boolean).forEach(c=>c.addEventListener(c.tagName==='INPUT'?'input':'change',applyFilters));
document.getElementById('reset').addEventListener('click',()=>{controls.filter(Boolean).forEach(c=>c.value=c.tagName==='INPUT'?'':'all');applyFilters();});
applyFilters();
'''


def review_run_key(data):
    # A report may be regraded without invalidating a review of the actual response.
    entries = []
    for case in data['cases']:
        evidence = case.get('evidence', {})
        response = evidence.get('response') or evidence.get('response_without_reasoning_text') or {}
        response_id = response.get('id') if isinstance(response, dict) else None
        entries.append([case['id'], evidence.get('input_sha256'), case['review']['output_sha256'], response_id])
    return hashlib.sha256(json.dumps(entries, ensure_ascii=False).encode()).hexdigest()


def context_display(value):
    """Lead with the material the editor could read, retaining the complete input."""
    if value is None:
        return '未记录上下文'
    if isinstance(value, str):
        return value or '空'
    if isinstance(value, dict):
        if not value or value == {'messages': []}:
            return '无可用内容'
        if value.get('status') == 'unavailable':
            return value.get('reason') or '未取得屏幕内容'
        if isinstance(value.get('context'), str) and value['context']:
            return value['context']
        if isinstance(value.get('messages'), list):
            speakers = {'self':'自己', 'other':'对方', 'unknown':'发言人未确定'}
            messages = value['messages']
            if all(isinstance(m, dict) and isinstance(m.get('text'), str) for m in messages):
                return '\n'.join(speakers.get(m.get('speaker'), '发言人未确定')+'　'+m['text'] for m in messages) or '无消息'
    return json.dumps(value, ensure_ascii=False, indent=2)


def input_section(case, editable):
    out = ['<section class="case-inputs" aria-label="本次模型输入">']
    for field, label, value in [('editable_text','原文',case['source_text']), ('utterance','口述',case['instruction'])]:
        out.append('<div class="input-field" data-field="'+field+'"><div class="input-heading"><strong>'+label+'</strong>')
        if editable:
            out.append('<button type="button" data-input-action="edit" aria-label="更改'+label+'">更改</button>')
        out.append('</div><div class="input-text" data-empty="'+str(not value).lower()+'">'+ESC(value or '（空）')+'</div>')
        if editable:
            out.append('<div class="input-form" hidden><label>题库中的'+label+'<textarea rows="4"></textarea></label>'
                '<button type="button" data-input-action="save">保存到题库</button> '
                '<button type="button" data-input-action="cancel">取消</button></div><output aria-live="polite"></output>')
        out.append('</div>')
    context = case.get('context')
    out.append('<div class="input-field"><div class="input-heading"><strong>给定上下文</strong></div>')
    parts = context.items() if isinstance(context,dict) and ('屏幕上文' in context or '已保存资料' in context) else [('',context)]
    for label, value in parts:
        if label:
            out.append('<b>'+ESC(label)+'</b>')
        out.append('<div class="input-text">'+ESC(context_display(value))+'</div>')
    if isinstance(context,(dict,list)) and context:
        out.append('<details><summary>完整上下文字段</summary><pre>'+ESC(json.dumps(context,ensure_ascii=False,indent=2))+'</pre></details>')
    out.append('</div></section>')
    return ''.join(out)


def render(data):
    data = validate(data)
    cases, labels = data['cases'], data.get('labels', {})
    classification = data['evaluation']['classification']
    stats = metrics(cases)
    n = stats['scored']
    group_names = data.get('groups', {})
    groups = list(dict.fromkeys(c['group'] for c in cases))
    summary = data.get('summary', [])
    if not isinstance(summary, list) or not all(isinstance(s, str) for s in summary):
        raise ValueError('summary must be a list of factual paragraphs')
    out = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
        '<title>'+ESC(data['title'])+'</title><style>'+CSS+
        '.case-deletion{margin:14px 0}.case-deletion button[data-delete=open],.case-deletion button[data-delete=confirm]{color:var(--red)}.delete-confirm{padding:10px 14px;background:#fff1ee;border-radius:7px}.delete-confirm p{white-space:pre-wrap;overflow-wrap:anywhere}.case-deletion output{display:block;color:var(--muted)}'+
        '.case-inputs{border-left:4px solid var(--teal);background:#f0f7f5;border-radius:6px;padding:4px 18px;margin:12px 0 18px}.input-field{padding:12px 0}.input-field+.input-field{border-top:1px solid #c9ded9}.input-heading{display:flex;align-items:center;gap:12px;color:#124e45}.input-heading strong{font-size:17px}.input-heading button{padding:2px 10px}.input-text{white-space:pre-wrap;overflow-wrap:anywhere;margin:4px 0}.input-form textarea{display:block;width:100%;font:inherit;margin:6px 0}.input-field output,.review-actions output{display:block;color:#345953}.case-result{display:flex;gap:12px;align-items:baseline;margin:10px 0}.case-result p{margin:0}.input-form{margin:10px 0}' +'</style></head><body><main>',
        '<header><div class="eyebrow">LLM EVALUATION · COMPLETE CASE REVIEW</div><h1>'+ESC(data['title'])+'</h1></header>',
        '<section class="panel" id="overview"><h2 style="margin-top:0">整体特征</h2>'+''.join('<p>'+ESC(p)+'</p>' for p in summary),
        '<p class="note">'+ESC(data.get('methodology',''))+'</p><div class="links">']
    for link in data.get('links', []):
        if not safe_link(link['href']):
            raise ValueError('Unsafe link scheme')
        out.append('<a href="'+ESC(link['href'], quote=True)+'">'+ESC(link['label'])+'</a>')
    out.extend(['</div></section><div class="kpis">',
        f'<div class="kpi">{"分类＋正文均通过" if classification else "全部要求通过"}<b>{stats["overall"].get("pass",0)} / {n}</b><small>{percentage(stats["overall"].get("pass",0),n)} · 观察例 {stats["observation"]} 另列</small></div>'])
    if classification:
        out.append(f'<div class="kpi">预期类别集合命中<b>{stats["route_pass"]} / {n}</b><small>{percentage(stats["route_pass"],n)} · 多选漏选均不匹配</small></div>')
    else:
        out.append(f'<div class="kpi">未满足要求<b>{stats["overall"].get("fail",0)} / {n}</b><small>其中调用异常 {stats["errors"]}</small></div>')
    out.extend([
        f'<div class="kpi">正文语义通过<b>{stats["body"].get("pass",0)} / {n}</b><small>失败 {stats["body"].get("fail",0)} · 待确认 {stats["body"].get("uncertain",0)} · 未复核 {stats["body"].get("pending",0)}</small></div>',
        f'<div class="kpi">{ESC(data.get("latency_label","完整调用 P50 / P95"))}<b>{seconds(stats["latency"]["p50"])}</b><small>P95 {seconds(stats["latency"]["p95"])} · 含网络</small></div></div>',
        '<div class="panel"><h3>样例分布与表现</h3><div class="metrics-wrap">'])
    rows=[]
    for group in groups:
        m=metrics([c for c in cases if c['group']==group])
        rows.append([group_names.get(group,group),m['n']]+([f'{m["route_pass"]} / {m["scored"]}' if m['scored'] else '观察'] if classification else [])+
            [m['body'].get('pass',0),m['overall'].get('fail',0),m['overall'].get('uncertain',0),seconds(m['latency']['p95'])])
    out.append(table(['分组','样例数']+(['分类命中'] if classification else [])+['正文通过','整体失败','待确认','P95'],rows))
    if classification:
        rows=[]
        for size in sorted({len(c['expected_labels']) for c in cases if not c.get('observation')}):
            m=metrics([c for c in cases if not c.get('observation') and len(c['expected_labels'])==size])
            rows.append((f'{size} 类任务',m['scored'],m['route_pass'],m['body'].get('pass',0),m['overall'].get('pass',0),m['overall'].get('fail',0)))
        out.append('<h3 style="margin-top:20px">按预期类别数</h3>'+table(['任务数','样例数','分类命中','正文通过','均通过','失败'],rows))
    reason='请求异常、类别集合不匹配或正文复核失败' if classification else '请求异常或正文复核失败'
    out.append('</div><p class="muted">整体失败＝'+reason+'。机械断言差异单独展示；语义复核可解释合法表达差异。待确认和未复核不算通过。观察例不进入主分母。</p></div>')
    for block in data.get('analysis_tables', []):
        out.append('<section class="panel"><h3>'+ESC(block['title'])+'</h3><p>'+ESC(block.get('description',''))+'</p><div class="metrics-wrap">'+table(block['columns'],block['rows'])+'</div></section>')
    out.append('<h2 id="cases">'+('逐例：模型判断与预期判断' if classification else '逐例：实际输出与预期要求')+'</h2><div class="filters"><select id="result" aria-label="结果筛选">')
    for key,text in [('all','全部结果'),('fail','失败（含已判对）'),('reviewed-fail','原失败、已判对'),('pass','仅通过'),('uncertain','待确认'),('pending','未复核'),('observation','观察例'),('routefail','分类不匹配'),('bodyfail','正文失败'),('mechanical','机械断言差异')]:
        if key=='routefail' and not classification:continue
        if key=='mechanical' and not any(c.get('checks') for c in cases):continue
        out.append(f'<option value="{key}">{text}</option>')
    out.append('</select><select id="group" aria-label="样例组"><option value="all">全部分组</option>'+''.join('<option value="'+ESC(g,quote=True)+'">'+ESC(group_names.get(g,g))+'</option>' for g in groups)+'</select>')
    if classification:
        out.append('<select id="tasks" aria-label="预期类别数量"><option value="all">全部预期类别数</option><option value="multi">多类别组合</option>'+''.join(f'<option value="{s}">{s} 类</option>' for s in sorted({len(c['expected_labels']) for c in cases if c['expected_labels']}))+'</select>')
    out.append('<input id="search" aria-label="搜索样例" placeholder="搜索口述、结果、ID、失败原因…"><button id="reset">重置</button><span id="count" role="status"></span></div><p id="empty" hidden>没有匹配的样例。</p>')
    run_key = review_run_key(data)
    out.append(f'<section class="panel" id="human-review" data-run="{run_key}"><h3>人工审阅</h3>'
        '<p>先更新本题验收标准，再将这次正文判为通过。原失败题改判后仍显示在失败列表，可单独筛选。上方统计保留出报告时的结果。</p>'
        '<p role="status" aria-live="polite"></p><button id="export-reviews" disabled>导出人工判定</button> '
        '<a hidden>打开本机审阅页，写入题库</a></section>')
    for index,c in enumerate(cases,1):
        state=c['overall']; review=c['review']; mismatch=any(not x['passed'] for x in c.get('checks',[]))
        search=json.dumps([c['id'],c['instruction'],c['source_text'],c.get('context'),c['expected_result'],c['output'],review,c.get('features'),c.get('fields'),c.get('assessments'),c.get('details')]+([c.get('route_note'),label_text(c['expected_labels'],labels),label_text(c['actual_labels'],labels)] if classification else []),ensure_ascii=False)
        initial_state = c.get('initial_status', state)
        initial_body = c.get('initial_body_status', review['status'])
        if initial_state not in STATES or initial_body not in STATES:
            raise ValueError('Invalid initial verdict')
        route_attrs=f' data-route="{str(c["route_match"]).lower()}" data-tasks="{len(c["expected_labels"])}"' if classification else ''
        out.append(f'<article class="case" id="case-{index}" data-status="{state}" data-initial-status="{initial_state}" data-initial-body="{initial_body}"{route_attrs} data-body="{review["status"]}" data-mechanical="{str(mismatch).lower()}" data-group="{ESC(c["group"],quote=True)}" data-search="{ESC(search,quote=True)}">')
        out.append(f'<h3>{index:03} · {ESC(c["id"])}</h3>')
        editable = not c.get('observation') and not c.get('error') and bool(c.get('evidence',{}).get('input_sha256'))
        out.append(input_section(c, editable))
        out.append(f'<div class="case-head case-result"><span class="badge {state}">{STATES[state]}</span><p class="muted">{ESC(group_names.get(c["group"],c["group"]))} · {seconds(c.get("latency_s"))} · {ESC(" / ".join(c.get("features",[])))}</p></div><div class="grid">')
        fields=([('预期分类',label_text(c['expected_labels'],labels) if not c.get('observation') else '历史边界观察，不计分类分数'),('模型实际分类',label_text(c['actual_labels'],labels))] if classification else [])
        fields += [(f['label'],f['value']) for f in c.get('fields', [])]
        fields += [('预期结果与判定依据',c['expected_result']),('模型实际正文',c['output'] if not c.get('error') else '【请求或解析异常，见原因】')]
        for title, value in fields:
            out.append('<div class="field"><div class="label">'+ESC(title)+'</div><div class="text'+(' output' if title=='模型实际正文' else '')+'">'+ESC(value)+'</div></div>')
        out.append('</div>')
        if c.get('assessments'):
            out.append('<div class="metrics-wrap">'+table(['逐项要求','判定','预期','实际'],[(a['label'],STATES[a['status']],a['expected'],a['actual']) for a in c['assessments']])+'</div>')
        if classification and c['route_match'] is False:
            missing=sorted(set(c['expected_labels'])-set(c['actual_labels'])); extra=sorted(set(c['actual_labels'])-set(c['expected_labels']))
            desc=[]
            if missing:desc.append('漏选：'+label_text(missing,labels))
            if extra:desc.append('多选：'+label_text(extra,labels))
            out.append('<div class="reason"><strong>分类差异原因（按冻结预期）</strong><p>'+ESC('；'.join(desc))+'</p><p>'+ESC(c.get('route_note') or '与调用前固定的预期任务集合不一致；具体任务依据见上方预期结果。')+'</p></div>')
        if c.get('error'):
            out.append('<div class="reason"><strong>调用／解析失败原因</strong><p>'+ESC(c['error'])+'</p></div>')
        if not c.get('observation') and not c.get('error'):
            inp = c.get('evidence',{}).get('input_sha256') or hashlib.sha256(json.dumps(
                [c['instruction'],c['source_text'],c.get('context')],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            out.append('<div class="review-actions" data-id="'+ESC(c['id'],quote=True)+'" data-input="'+ESC(inp,quote=True)+'" data-output="'+review['output_sha256']+'">'
                '<div hidden class="rule-form"><label>本题验收标准（每行一条）<textarea rows="4" style="width:100%;font:inherit"></textarea></label></div>'
                '<button type="button" data-action="rule">更新标准并判对</button> '
                '<button type="button" data-action="cancel" hidden>取消</button> <output aria-live="polite"></output></div>')
        if not c.get('observation') and c.get('evidence', {}).get('input_sha256'):
            out.append('<div class="case-deletion" data-id="'+ESC(c['id'],quote=True)+'" data-input="'+
                ESC(c['evidence']['input_sha256'],quote=True)+'" data-output="'+review['output_sha256']+'">'
                '<button type="button" data-delete="open">从题库删除</button>'
                '<div class="delete-confirm" hidden><p></p><button type="button" data-delete="confirm">确认删除</button> '
                '<button type="button" data-delete="cancel">取消</button></div>'
                '<output aria-live="polite"></output></div>')
        if review['status'] in ('fail','uncertain'):
            out.append('<div class="initial-review reason '+('uncertain' if review['status']=='uncertain' else '')+'"><strong>'+('初评失败原因' if review['status']=='fail' else '需要确认的原因')+'</strong><p>'+ESC(review['note'])+'</p>')
            for issue in review.get('issues',[]):
                out.append('<p><b>'+ESC(issue['reason'])+'</b><br>应当：'+ESC(issue['expected'])+'<br>实际：'+ESC(issue['actual'])+'</p>')
            out.append('</div>')
        else:
            out.append('<p class="initial-review"><span class="badge '+review['status']+'">正文复核：'+STATES[review['status']]+'</span> '+ESC(review['note'])+'</p>')
        if c.get('checks'):
            out.append('<details><summary>机械检查：'+('存在差异，须结合语义复核' if mismatch else '全部满足')+'</summary><pre>'+ESC(json.dumps(c['checks'],ensure_ascii=False,indent=2))+'</pre></details>')
        for detail in c.get('details', []):
            out.append('<details><summary>'+ESC(detail['title'])+'</summary><div class="text">'+ESC(detail['text'])+'</div></details>')
        out.append('<details><summary>完整证据与原始模型返回</summary><pre>'+ESC(json.dumps(c.get('evidence',{}),ensure_ascii=False,indent=2))+'</pre></details></article>')
    out.append('<section class="panel"><details><summary>评测元数据与统计口径</summary><pre>'+ESC(json.dumps({'metadata':data.get('metadata',{}),'metrics':stats},ensure_ascii=False,indent=2))+'</pre></details></section><script>'+JS+'</script><script>'+Path(__file__).with_name('eval_human_review.js').read_text()+'</script></main></body></html>')
    return ''.join(out), stats


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    data=json.loads(args.input.read_text())
    rendered,stats=render(data)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    # Keep the normalized input discoverable for the review service even when the
    # caller gives it a custom name or saves the HTML in a different directory.
    sidecar = args.output.with_suffix('.report.json')
    standard = {sidecar.resolve(), args.output.with_suffix('.json').resolve()}
    if args.output.name == 'index.html':
        standard.update((args.output.parent/name).resolve() for name in ('report.json','human-reviewed-report.json'))
    if args.input.resolve() not in standard:
        sidecar.write_bytes(args.input.read_bytes())
    args.output.write_text(rendered)
    args.output.with_suffix('.metrics.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(stats,ensure_ascii=False))


if __name__=='__main__':main()
