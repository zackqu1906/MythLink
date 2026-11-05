"""HTTP evidence and separate routing/generation scores for real worker probes."""
from collections import Counter
from html import escape
import json
import math
from pathlib import Path
import statistics
import time

from proximic_ring.text_processing.task_routing import EDIT_TASK_LABELS


class RecordedResponse:
    def __init__(self,response,record,started):
        self.response,self.record,self.started=response,record,started
    def __enter__(self):return self
    def __exit__(self,*args):self.response.close()
    def __getattr__(self,key):return getattr(self.response,key)
    def read(self,*args):
        raw=self.response.read(*args)
        self.record['http_latency_s']=time.perf_counter()-self.started
        try:self.record['response']=json.loads(raw)
        except (UnicodeError,ValueError):self.record['response_text']=raw.decode('utf-8',errors='replace')
        return raw


def recording_opener(opener,records):
    def call(request,**kwargs):
        record={'endpoint':request.full_url,'request':json.loads(request.data)}
        records.put(record)
        start=time.perf_counter()
        try:
            response=opener(request,**kwargs)
            record.update(http_status=response.status,response_headers={k:v for k,v in response.headers.items() if k.lower() in ('x-request-id','x-tt-logid','content-type')})
            return RecordedResponse(response,record,start)
        except Exception as exc:
            record.update(error_type=type(exc).__name__,http_status=getattr(exc,'code',None),http_latency_s=time.perf_counter()-start)
            raise
    return call


def timing(values):
    values=sorted(v for v in values if v is not None)
    return {'n':len(values),'p50_s':statistics.median(values) if values else None,
            'p95_s':values[math.ceil(.95*len(values))-1] if values else None,'max_s':max(values) if values else None}


def attribution(case):
    if case.get('routing_error'):return '路由接口失败'
    if case.get('generation_error'):
        if 'generation_calls' in case and not case['generation_calls']:
            return '路由不匹配，生成前校验拦截（未调用正文模型）' if not case['exact_match'] else '生成前校验拦截（未调用正文模型）'
        return '生成接口或校验失败'
    status=case.get('generation_review',{}).get('status','pending')
    if not case['exact_match']:
        return {'pass':'路由不匹配，但正文满足要求','fail':'路由不匹配且正文不符，原因待区分'}.get(status,'路由不匹配，正文待复核')
    return {'pass':'路由与正文均通过','fail':'路由正确，生成正文有问题'}.get(status,'路由正确，正文待复核')


def pipeline_metrics(cases):
    ok=[c for c in cases if not c.get('routing_error') and not c.get('generation_error')]
    def group(items):
        return {'n':len(items),'routing':timing([c.get('routing_latency_s') for c in items]),
                'generation':timing([c.get('generation_latency_s') for c in items]),
                'total':timing([c.get('total_latency_s') for c in items])}
    checks=[c for c in cases if c.get('generation_checks',{}).get('passed') is not None]
    return {'samples':len(cases),'completed':len(ok),'routing_errors':sum(bool(c.get('routing_error')) for c in cases),
            'generation_errors':sum(bool(c.get('generation_error')) for c in cases),
            'root_correct':sum(c.get('root_correct',False) for c in cases),'raw_exact_match':sum(c.get('raw_exact_match',False) for c in cases),
            'final_route_correct':sum(c['exact_match'] for c in cases),'warnings':sum(bool(c.get('routing_trace',{}).get('warning')) for c in cases),
            'generation_review_passed':sum(c.get('generation_review',{}).get('status')=='pass' for c in cases),
            'generation_review_failed':sum(c.get('generation_review',{}).get('status')=='fail' for c in cases),
            'generation_review_blocked':sum(c.get('generation_review',{}).get('status')=='blocked' for c in cases),
            'generation_review_pending':sum(c.get('generation_review',{}).get('status','pending')=='pending' for c in cases),
            'route_correct_generation_failed':sum(c['exact_match'] and c.get('generation_review',{}).get('status')=='fail' for c in cases),
            'both_passed':sum(c['exact_match'] and c.get('generation_review',{}).get('status')=='pass' for c in ok),
            'constraint_scored':len(checks),'constraint_passed':sum(c['generation_checks']['passed'] for c in checks),
            'generation_failures_by_category':dict(Counter(c['generation_review'].get('category','未分类') for c in cases if c.get('generation_review',{}).get('status')=='fail')),
            'generation_failures_by_severity':dict(Counter(c['generation_review'].get('severity','未分级') for c in cases if c.get('generation_review',{}).get('status')=='fail')),
            'timing_successful':group(ok),'by_expected_intent':{t:{'samples':len([c for c in cases if c['expected']['task']==t]),
                'review_passed':sum(c.get('generation_review',{}).get('status')=='pass' for c in cases if c['expected']['task']==t),
                **group([c for c in ok if c['expected']['task']==t])} for t in ('dictation','edit')}}


def write_pipeline_report(report,path):
    path=Path(path);cases=report['cases'];m=report['metrics']=pipeline_metrics(cases)
    for c in cases:c['attribution']=attribution(c)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    e=lambda v:escape(str(v),quote=True)
    def sec(v):return '—' if v is None else f'{v:.3f}'
    def plan(p):return '听写' if p.get('task')=='dictation' else '编辑：'+'＋'.join(EDIT_TASK_LABELS.get(t,t) for t in p.get('tasks',[]))
    def cell(v):return '<td>'+e(v)+'</td>'
    def table(head,rows):return '<div class="scroll"><table><tr>'+''.join('<th>'+e(h)+'</th>' for h in head)+'</tr>'+''.join(rows)+'</table></div>'
    def row(values,css=''):return '<tr class="'+css+'">'+''.join(cell(v) for v in values)+'</tr>'
    html=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>联合路由与生成完整流程评测</title>',
          '<style>body{font:15px/1.6 system-ui;margin:28px;color:#172438}table{border-collapse:collapse;width:100%;background:white}th,td{padding:9px;border:1px solid #cbd5e1;text-align:left;vertical-align:top;white-space:pre-wrap}th{background:#eef3f9}.scroll{overflow:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere}.bad{background:#fee2e2}.warn{background:#fff3cd}.pending{background:#eef2f6}details{border:1px solid #cbd5e1;padding:12px;margin:12px 0}summary{cursor:pointer}input,select{padding:8px;margin:6px}</style>',
          '<h1>联合路由 → 组合生成：完整流程</h1><p>'+e(report['scope'])+'</p>',
          '<p>分类：'+e(report['prompt_version'])+'；生成：'+e(report['generation_prompt_version'])+'；文本模型：'+e(report['llm_model'])+'</p>',
          '<p>原始总体意图正确 '+str(m['root_correct'])+'/'+str(m['samples'])+'；原始全部分类匹配 '+str(m['raw_exact_match'])+'/'+str(m['samples'])+'；校验后最终路由匹配 '+str(m['final_route_correct'])+'/'+str(m['samples'])+'；标黄 '+str(m['warnings'])+' 条。听写优先拦下的 apply 不算最终路由错误，原始概率仍完整保留。</p>',
          '<p>接口完成 '+str(m['completed'])+'/'+str(m['samples'])+'；生成正文逐条复核通过 '+str(m['generation_review_passed'])+'，不通过 '+str(m['generation_review_failed'])+'，未生成 '+str(m['generation_review_blocked'])+'，待复核 '+str(m['generation_review_pending'])+'。其中路由正确但正文有问题 '+str(m['route_correct_generation_failed'])+' 条。未生成时 final_text 可能是 Worker 的回退口述，不是正文模型输出。自动文字约束 '+str(m['constraint_passed'])+'/'+str(m['constraint_scored'])+' 通过，未配置约束的样例不记为自动通过。</p>',
          '<p>'+e(report.get('review_policy','尚未逐条语义复核。'))+'</p>',
          '<p>'+e(report.get('dataset_note','本批样例曾用于提示词调优，不是独立测试准确率。'))+'真实路由按全部预期标签评分，输出另行复核；没有用预期计划替换实际计划。分类正确不保证正文正确，分类错误但正文正确也不会反过来把路由记为正确。</p>',
          '<p>每道分类题的全部概率按服务原样记录，是未校准的模型分数；不同题不能相加。空原文时修改不在 schema 中，不伪造 skip 或 0%/100% 概率。红色表示不符合预期，黄色表示原始联合冲突已被后置策略拦下。</p>',
          '<p>所有延迟均为客户端实际测量，包含网络和服务端处理；总耗时包括本机 Worker 调度，不含 ASR、截图 OCR 和输入法写回。不称为纯模型推理时间。每例顺序运行一次，无额外预热或重试。生成 JSONL 保存运行当时的状态；后续语义复核保存于 JSON/HTML。</p>',
          '<p><a href="'+e(path.name)+'">完整 JSON</a> · <a href="'+e(path.with_suffix('.jsonl').name)+'">逐例原始日志</a></p><h2>正文质量分组</h2>']
    rows=[]
    for t,label in [('dictation','听写正文整理'),('edit','编辑生成')]:
        group=[c for c in cases if c['expected']['task']==t]
        rows.append(row([label,len(group),sum(c['exact_match'] for c in group),
                         sum(c.get('generation_review',{}).get('status')=='pass' for c in group),
                         sum(c.get('generation_review',{}).get('status')=='fail' for c in group),
                         sum(c.get('generation_review',{}).get('status')=='blocked' for c in group)]))
    html.append(table(['分组','样例数','最终路由正确','正文复核通过','正文有问题','未生成'],rows))
    html.append('<h2>延迟与分组</h2>')
    rows=[]
    for title,g in [('全部',m['timing_successful']),('预期听写',m['by_expected_intent']['dictation']),('预期编辑',m['by_expected_intent']['edit'])]:
        rows.append(row([title,g['n']]+[sec(g[k][v]) for k in ('routing','generation','total') for v in ('p50_s','p95_s')]))
    html.append(table(['分组','成功数','路由 P50 秒','路由 P95 秒','生成 P50 秒','生成 P95 秒','完整流程 P50 秒','完整流程 P95 秒'],rows))
    for title,predicate in [
        ('最终路由错误',lambda c:not c['exact_match']),
        ('路由正确但编辑生成有问题',lambda c:c['exact_match'] and c['expected']['task']=='edit' and c.get('generation_review',{}).get('status')=='fail'),
        ('路由正确但听写正文整理有问题',lambda c:c['exact_match'] and c['expected']['task']=='dictation' and c.get('generation_review',{}).get('status')=='fail'),
        ('原始分类异常、最终已被策略拦下（标黄）',lambda c:bool(c.get('routing_trace',{}).get('warning')))]:
        html.append('<h2>'+title+'</h2>');items=[c for c in cases if predicate(c)]
        rows=[]
        for c in items:
            review=c.get('generation_review',{})
            severity={'minor':'轻微语言／标点问题','medium':'内容或要求偏差','high':'重要事实或功能偏差','none':'通过'}.get(review.get('severity'),'待复核')
            rows.append(row([c['id'],c['utterance'],c['target'],c.get('final_text','未生成'),c['attribution'],severity,review.get('note',''),c.get('routing_trace',{}).get('warning','')],'warn' if '标黄' in title else 'bad'))
        html.append(table(['编号','口述','原文','实际正文','归因','程度','复核原因','校验提示'],rows) if rows else '<p>无。</p>')
    html.append('<h2>全部样例简表</h2><label>筛选 <select id="filter"><option value="all">全部</option><option value="route">最终路由不匹配</option><option value="generation">生成问题</option><option value="warn">标黄</option><option value="pass">路由及正文通过</option></select></label><label>搜索 <input id="search" placeholder="编号、口述或输出"></label><div class="scroll"><table id="all-cases"><tr><th>编号</th><th>口述／原文</th><th>预期 → 实际路由</th><th>实际正文</th><th>归因／复核</th><th>路由／生成／总计 秒</th></tr>')
    for c in cases:
        rev=c.get('generation_review',{});warn=bool(c['routing_trace'].get('warning'));bad=not c['exact_match'] or rev.get('status')=='fail' or c.get('generation_error')
        actual={'task':c['actual_task'],**(c.get('edit_plan') or {})}
        tags=('route ' if not c['exact_match'] else '')+('generation ' if rev.get('status')=='fail' or c.get('generation_error') else '')+('warn ' if warn else '')+('pass' if not bad and rev.get('status')=='pass' else '')
        html.append('<tr data-tags="'+tags+'" class="'+('bad' if bad else 'warn' if warn else '')+'"><td><a href="#'+e(c['id'])+'">'+e(c['id'])+'</a></td>'+cell(c['utterance']+'\n原文：'+c['target'])+cell(plan(c['expected'])+' → '+plan(actual))+cell(c.get('final_text',c.get('routing_error','')))+cell(c['attribution']+'\n'+rev.get('note',''))+cell(' / '.join(sec(c.get(k)) for k in ('routing_latency_s','generation_latency_s','total_latency_s')))+'</tr>')
    html.append('</table></div><h2>逐例详细记录与全部分类概率</h2>')
    for c in cases:
        html.append('<details id="'+e(c['id'])+'"><summary>'+e(c['id']+' — '+c['attribution'])+'</summary>')
        stage=next((s for s in c['routing_trace'].get('stages',[]) if s['stage']=='joint'),{})
        rows=[]
        for name,a in stage.get('answers',{}).items():
            ps=a['probabilities'];expected=c['expected']['task'] if name=='voice_intent' else 'apply' if name in c['expected'].get('tasks',[]) else 'skip'
            rows.append(row(['总体意图' if name=='voice_intent' else EDIT_TASK_LABELS.get(name,name),a['choice'],expected,'；'.join(f'{k}: {v*100:.2f}%' for k,v in ps.items())],'bad' if a['choice']!=expected else ''))
        for name,reason in c['routing_trace'].get('task_availability',{}).get('excluded_tasks',{}).items():rows.append(row([EDIT_TASK_LABELS.get(name,name),'不可用',reason,'未请求，无模型概率']))
        html.append(table(['分类','原始选择','预期','全部概率'],rows))
        html.append('<pre>'+e(json.dumps(c,ensure_ascii=False,indent=2))+'</pre></details>')
    html.append('<details><summary>版本、文件摘要和统计口径</summary><pre>'+e(json.dumps({k:v for k,v in report.items() if k!='cases'},ensure_ascii=False,indent=2))+'</pre></details>')
    html.append('''<script>function filterRows(){const f=document.getElementById('filter').value,q=document.getElementById('search').value.toLowerCase();for(const r of document.querySelectorAll('#all-cases tr[data-tags]')){r.hidden=!((f==='all'||r.dataset.tags.split(' ').includes(f))&&r.textContent.toLowerCase().includes(q));}}document.getElementById('filter').addEventListener('change',filterRows);document.getElementById('search').addEventListener('input',filterRows);</script></html>''')
    path.with_suffix('.html').write_text(''.join(html))
