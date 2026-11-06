#!/usr/bin/env python3
"""Test composed edit prompts against the configured Ark DeepSeek, without Clef."""
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
from urllib.request import urlopen
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from proximic_ring.text_processing.automatic_prompts import AUTOMATIC_PROMPT_VERSION
from proximic_ring.text_processing.llm import OpenAICompatibleTextProcessor
from proximic_ring.text_processing.model import LLMSettings
from proximic_ring.text_processing.router_prompts import ROUTER_PROMPT_VERSION
from proximic_ring.text_processing.task_routing import EditPlan


def load_cases(path, selected=None):
    cases = json.loads(Path(path).read_text())
    if not isinstance(cases, list) or not cases:
        raise ValueError('Cases must be a nonempty list')
    seen = set()
    for case in cases:
        case_id = case['id']
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ValueError('Case IDs must be unique nonempty strings')
        seen.add(case_id)
        assert isinstance(case['utterance'], str) and case['utterance'].strip()
        assert isinstance(case['target'], str)
        assert isinstance(case['context']['messages'], list)
        EditPlan(tuple(case['edit_plan']['tasks']))
        expected = case['expected']
        assert isinstance(expected, dict) and expected
        if 'modified_text' in expected: assert isinstance(expected['modified_text'], str)
        for key in ('contains', 'contains_ci', 'excludes'):
            if key in expected: assert isinstance(expected[key], list) and all(isinstance(x,str) and x for x in expected[key])
        if 'max_chars' in expected: assert isinstance(expected['max_chars'], int) and expected['max_chars'] > 0
        assert isinstance(case['review_points'], str) and case['review_points']
    if selected:
        if set(selected)-seen: raise ValueError('Unknown case IDs')
        cases = [c for c in cases if c['id'] in selected]
    return cases


def check_output(expected, output):
    checks = []
    if 'modified_text' in expected:
        checks.append({'kind':'exact_text','expected':expected['modified_text'],'passed':output==expected['modified_text']})
    for key in ('contains','excludes'):
        for text in expected.get(key,[]):
            checks.append({'kind':key,'text':text,'passed':(text in output) if key=='contains' else (text not in output)})
    for text in expected.get('contains_ci',[]):
        checks.append({'kind':'contains_ci','text':text,'passed':text.casefold() in output.casefold()})
    if 'max_chars' in expected:
        checks.append({'kind':'max_chars','limit':expected['max_chars'],'actual':len(output),'passed':len(output)<=expected['max_chars']})
    return checks


def metrics(records):
    times=sorted(r['latency_s'] for r in records if not r.get('error'))
    return {'samples':len(records),'completed':len(times),'errors':sum(bool(r.get('error')) for r in records),
            'constraint_passed':sum(r.get('constraint_passed') is True for r in records),
            'review_passed':sum(r.get('review',{}).get('status')=='pass' for r in records),
            'review_failed':sum(r.get('review',{}).get('status')=='fail' for r in records),
            'review_pending':sum(r.get('review',{}).get('status','pending')=='pending' for r in records),
            'latency_p50_s':statistics.median(times) if times else None,
            'latency_p95_s':times[math.ceil(.95*len(times))-1] if times else None,
            'latency_max_s':max(times) if times else None}


def write_report(report, path):
    report['metrics']=metrics(report['cases'])
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    e=lambda x:escape(str(x),quote=True)
    html=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>自动编辑组合提示词测试</title>',
          '<style>body{font:15px/1.6 system-ui;margin:28px;color:#172438}table{border-collapse:collapse;width:100%}td,th{padding:9px;border:1px solid #cbd5e1;text-align:left;vertical-align:top}th{background:#eef3f9}pre{white-space:pre-wrap;overflow-wrap:anywhere}.fail{background:#fee2e2}.pending{background:#fef3c7}details{border:1px solid #cbd5e1;padding:12px;margin:12px 0}summary{cursor:pointer}</style>',
          '<h1>自动编辑：六类模块与组合顺序</h1><p>'+e(report['scope'])+'</p>',
          '<p>生成提示词：'+e(report['prompt_version'])+'；模型：'+e(report['model'])+'；主项目分类：'+e(report['routing_version'])+'</p>',
          '<p>约束检查只验证预先写定的精确文本、保留项、禁止项和长度，不等于全部语义正确；逐条复核单独记录。延迟为本机生成调用到完整结果校验结束，包含网络和服务端处理；未测首 token 或纯模型前向时间。请求顺序执行，每条一次，不自动重试。</p>',
          '<p>'+e(report.get('review_policy','尚未逐条语义复核。'))+'</p>',
          '<pre>'+e(json.dumps(report['metrics'],ensure_ascii=False,indent=2))+'</pre>',
          '<p><a href="'+e(path.name)+'">完整 JSON：所有请求、响应及用量</a></p>',
          '<h2>简要记录</h2><table><tr><th>样例／候选任务</th><th>口述／原文</th><th>输出</th><th>检查／复核</th><th>延迟（秒）</th></tr>']
    for r in report['cases']:
        review=r.get('review',{'status':'pending'})
        cls='fail' if r.get('error') or review['status']=='fail' or r.get('constraint_passed') is False else 'pending' if review['status']=='pending' else ''
        html.append('<tr class="'+cls+'"><td><a href="#'+e(r['id'])+'">'+e(r['id'])+'</a><br>'+e('、'.join(r['edit_plan']['tasks']))+'</td><td>'+e(r['utterance'])+'<pre>'+e(r['target'])+'</pre></td><td><pre>'+e(r.get('final_text',r.get('error','')))+'</pre></td><td>'+e('约束：'+str(r.get('constraint_passed'))+'；复核：'+review['status'])+'<br>'+e(review.get('note',''))+'</td><td>'+f"{r['latency_s']:.3f}"+'</td></tr>')
    html.append('</table><h2>详细记录</h2>')
    for r in report['cases']:
        html.append('<details id="'+e(r['id'])+'"><summary>'+e(r['id']+'：'+r['utterance'])+'</summary><pre>'+e(json.dumps(r,ensure_ascii=False,indent=2))+'</pre></details>')
    html.append('<details><summary>复现信息</summary><pre>'+e(json.dumps({k:v for k,v in report.items() if k!='cases'},ensure_ascii=False,indent=2))+'</pre></details></html>')
    path.with_suffix('.html').write_text(''.join(html))


class RecordedResponse:
    def __init__(self, response, record, started):
        self.response,self.record,self.started=response,record,started
    def __enter__(self):return self
    def __exit__(self,*args):self.response.close()
    def read(self):
        raw=self.response.read()
        self.record['http_latency_s']=time.perf_counter()-self.started
        try:self.record['response']=json.loads(raw.decode('utf-8'))
        except (UnicodeError,ValueError):self.record['response_text']=raw.decode('utf-8',errors='replace')
        return raw


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases',type=Path,default=ROOT/'tools/data/automatic_edit_order_cases.json')
    parser.add_argument('--case',action='append')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--validate-only',action='store_true')
    args=parser.parse_args()
    cases=load_cases(args.cases,args.case)
    if args.validate_only:print(f'CASES_OK: {len(cases)}');return 0
    from PySide6.QtCore import QSettings
    saved=QSettings('ProxiMic','ProxiMic Voice')
    settings=LLMSettings(enabled=True,provider=str(saved.value('llm/provider','volcengine')),
                        base_url=str(saved.value('llm/baseUrl','')),model=str(saved.value('llm/model','')),
                        api_key=str(saved.value('llm/apiKey','')),api_key_env=str(saved.value('llm/apiKeyEnv','ARK_API_KEY')),
                        timeout_s=float(saved.value('llm/timeoutSeconds',30.0)))
    settings.validate()
    if settings.provider!='volcengine' or not settings.model.lower().startswith('deepseek') or urlsplit(settings.base_url).hostname!='ark.cn-beijing.volces.com':
        raise ValueError('This test requires the configured Ark DeepSeek endpoint; settings were not changed')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    report={'created_at':datetime.now(timezone.utc).isoformat(),'prompt_version':AUTOMATIC_PROMPT_VERSION,
            'routing_version':ROUTER_PROMPT_VERSION,'model':settings.model,'host':urlsplit(settings.base_url).hostname,
            'scope':'合成编辑生成测试：直接指定候选任务，通过主项目真实 process_task_with_trace 调用火山引擎 DeepSeek。未调用 Clef、未采集音频或 OCR、未执行输入法写回；部分防误触发样例故意提供错误候选任务。没有分类概率或分类准确率。',
            'case_sha256':hashlib.sha256(args.cases.read_bytes()).hexdigest(),'cases_path':str(args.cases.resolve()),
            'source_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (
                ROOT/'src/proximic_ring/text_processing/automatic_prompts.py',ROOT/'src/proximic_ring/text_processing/automatic_processing.py',
                ROOT/'src/proximic_ring/text_processing/automatic_inputs.py',
                ROOT/'src/proximic_ring/text_processing/llm.py',Path(__file__))},'cases':[]}
    try:
        with args.output.with_suffix('.jsonl').open('w') as journal:
            for index,case in enumerate(cases,1):
                record={**case,'calls':[],'review':{'status':'pending'}}
                def capture(request,**kwargs):
                    call={'endpoint':request.full_url,'request':json.loads(request.data.decode('utf-8'))}
                    record['calls'].append(call)
                    started=time.perf_counter()
                    try:
                        response=urlopen(request,**kwargs)
                        call.update(http_status=response.status,response_headers={k:v for k,v in response.headers.items() if k.lower() in ('x-request-id','x-tt-logid','content-type')})
                        return RecordedResponse(response,call,started)
                    except Exception as exc:
                        call.update(http_latency_s=time.perf_counter()-started,error_type=type(exc).__name__)
                        raise
                processor=OpenAICompatibleTextProcessor(urlopen=capture)
                started=time.perf_counter()
                try:
                    final,model_output=processor.process_task_with_trace(case['utterance'],'edit',settings,case['target'],case['context'],edit_plan=EditPlan(tuple(case['edit_plan']['tasks'])))
                    record.update(final_text=final,model_output=model_output,checks=check_output(case['expected'],final))
                    record['constraint_passed']=all(c['passed'] for c in record['checks'])
                except Exception as exc:
                    record.update(error=str(exc),model_output=getattr(exc,'model_output',''),constraint_passed=False)
                finally:
                    record['latency_s']=time.perf_counter()-started
                    processor.close()
                report['cases'].append(record)
                journal.write(json.dumps(record,ensure_ascii=False)+'\n');journal.flush()
                print(json.dumps({'index':index,'n':len(cases),'id':record['id'],'s':round(record['latency_s'],3),'checks':record['constraint_passed'],'error':record.get('error')},ensure_ascii=False),flush=True)
    finally:write_report(report,args.output)
    print(json.dumps(report['metrics'],ensure_ascii=False))
    return 1 if report['metrics']['errors'] else 0


if __name__=='__main__':raise SystemExit(main())
