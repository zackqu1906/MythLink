#!/usr/bin/env python3
"""Render a reviewed production boundary run against its frozen historical run."""
from __future__ import annotations
import argparse
from collections import Counter
import difflib
import hashlib
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.eval_report import render,validate,metrics,seconds,STATES

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def digest(s):return hashlib.sha256(s.encode()).hexdigest()
def usage(raw):
    calls=[call for c in raw['cases'] for call in c['calls']]
    u=[c.get('response',{}).get('usage',{}) for c in calls]
    return {'requests':len(calls),'input_tokens':sum(x.get('input_tokens',0) for x in u),'output_tokens':sum(x.get('output_tokens',0) for x in u),
        'cached_tokens':sum(x.get('input_tokens_details',{}).get('cached_tokens',0) for x in u),'cached_requests':sum(x.get('input_tokens_details',{}).get('cached_tokens',0)>0 for x in u)}

def build(raw_path,reviews_path,output,ablation_report=None):
    raw=json.loads(raw_path.read_text());rev=json.loads(reviews_path.read_text())
    if rev['source_sha256']!=sha(raw_path):raise ValueError('Stale reviews')
    bp=raw_path.parent/raw['baseline']['file'];baseline=json.loads(bp.read_text());brp=bp.with_name(bp.name.replace('.raw.json','.reviews.json'));br=json.loads(brp.read_text())
    if sha(bp)!=raw['baseline']['sha256'] or br['source_sha256']!=sha(bp):raise ValueError('Baseline evidence mismatch')
    prior={c['id']:c for c in baseline['cases']}
    manifest_path=raw_path.parent/raw['input_manifest']['file'];manifest=json.loads(manifest_path.read_text())
    if sha(manifest_path)!=raw['input_manifest']['sha256'] or manifest['cases']!=json.loads((bp.parent/baseline['input_manifest']['file']).read_text())['cases']:raise ValueError('Input set changed')
    if set(rev['cases'])!=set(prior) or [c['id'] for c in raw['cases']]!=[c['id'] for c in baseline['cases']]:raise ValueError('Coverage/order mismatch')
    if not raw['completed_all'] or raw['changed_sources']:raise ValueError('Incomplete or changed-source run')
    if digest(raw['prompt'])!=raw['prompt_sha256']:raise ValueError('Prompt mismatch')
    cases=[];oldcases=[];changes=Counter();change_rows=[]
    for c in raw['cases']:
        r=rev['cases'][c['id']];b=prior[c['id']];before=br['cases'][c['id']]
        expected_input={'utterance':c['utterance'].strip(),'editable_text':c['target'],'screen_context':c['context']}
        if r['input_sha256']!=c['input_sha256'] or c['input_sha256']!=b['input_sha256']:raise ValueError('Input hash mismatch')
        if before['output_sha256']!=digest(b.get('final_text','')):raise ValueError('Stale baseline output review')
        if len(c['calls'])!=1:raise ValueError('Expected one call')
        wire=c['calls'][0]['request']
        if json.loads(wire['input'][1]['content'][0]['text'])!=expected_input or wire['input'][0]['content'][0]['text']!=raw['prompt']:raise ValueError('Wire mismatch')
        expectation=c.get('review_points') or '按本次口述完成要求：'+c['utterance']
        if c.get('generation_expected'):expectation+='\n调用前冻结的机械约束：'+json.dumps(c['generation_expected'],ensure_ascii=False)
        a,z=before['status'],r['status'];observation=c['cohort']=='guard'
        change='观察' if observation else '改善' if (a,z)==('fail','pass') else '退化' if (a,z)==('pass','fail') else '均通过' if a==z=='pass' else '均失败' if a==z=='fail' else '涉及待确认／未复核'
        if not observation:changes[change]+=1
        normal=dict(id=c['id'],group=c['cohort'],instruction=c['utterance'],source_text=c['target'],context=c['context'],features=c.get('features',[]),
            expected_result=expectation,output=c.get('final_text',''),error=c.get('error',''),observation=observation,latency_s=c['latency_s'],checks=c.get('checks',[]),review=r,
            fields=[{'label':'与 v1 的逐例比较','value':f'{change}：{STATES[a]} → {STATES[z]}'},{'label':'v1 原正文','value':b.get('final_text','')}],
            details=[{'title':'v1 原复核与依据','text':before['note']+'\n'+json.dumps(before.get('issues',[]),ensure_ascii=False,indent=2)}],
            evidence={'input_sha256':c['input_sha256'],'actual_model_input':expected_input,'calls':c['calls'],'review':r,'baseline_output_sha256':before['output_sha256']})
        cases.append(normal)
        oldcases.append({**normal,'output':b.get('final_text',''),'review':before,'error':b.get('error',''),'latency_s':b['latency_s'],'checks':b.get('checks',[])})
        if change in ('改善','退化'):change_rows.append([c['id'],change,b.get('final_text',''),c.get('final_text',''),r['note']])
    data={'schema_version':1,'evaluation':{'classification':False},'title':'偏旁释字边界 v2：216 条完整回归与 v1 对照',
        'groups':{'regression':'既有回归','fresh':'历史新增（已知）','ambiguity':'省略与歧义','combination':'48 条多要求组合','guard':'6 条旧边界观察'},
        'cases':cases,'methodology':rev['method'],'summary':rev['summary']}
    validate(data);validate({'schema_version':1,'evaluation':{'classification':False},'cases':oldcases})
    m=metrics(cases);om=metrics(oldcases)
    summary_rows=[]
    for label,subset,oldset in [('全部计分样例',cases,oldcases),('48 条多要求组合',[c for c in cases if c['group']=='combination'],[c for c in oldcases if c['group']=='combination'])]:
        for version,cs in [('v1',oldset),('v2',subset)]:
            x=metrics(cs);summary_rows.append([label,version,x['scored'],x['body'].get('pass',0),x['body'].get('fail',0),x['body'].get('uncertain',0),seconds(x['latency']['p50']),seconds(x['latency']['p95'])])
    data['analysis_tables']=[{'title':'完整套件的前后表现','description':'同一批合成开发数据；6 条旧 guard 单列观察。各版本每例一次，非同期运行，不把采样差异直接解释为提示词的因果作用。','columns':['范围','版本','计分数','通过','失败','待确认','P50','P95'],'rows':summary_rows},
        {'title':'逐例状态变化','description':'改善严格指 fail→pass；退化严格指 pass→fail。涉及 uncertain/pending 的配对单列，未混进改善数量。','columns':['变化','数量'],'rows':[[k,v] for k,v in changes.items()]},
        {'title':'改善与退化的实际文本','description':'下方全部样例支持失败筛选；搜索“改善”或“退化”也可查看对应案例。','columns':['ID','变化','v1 输出','v2 输出','v2 判定说明'],'rows':change_rows}]
    if 'analysis_tables' in rev:data['analysis_tables']+=rev['analysis_tables']
    system=output.with_name(output.stem+'-system.txt');system.write_text(raw['prompt'])
    diff=output.with_name(output.stem+'-prompt.diff');diff.write_text('\n'.join(difflib.unified_diff(baseline['prompt'].splitlines(),raw['prompt'].splitlines(),fromfile='v1',tofile='v2'))+'\n')
    normalized=output.with_suffix('.report.json')
    artifacts=[('本轮完整提示词',system),('v1 → v2 提示词差异',diff),('全部原始请求与响应',raw_path),('冻结输入与预期',manifest_path),('逐例复核记录',reviews_path),('规范化报告数据',normalized)]
    if ablation_report:artifacts.insert(0,('84 条指令拆分对照报告',ablation_report))
    import os
    data['links']=[{'label':l,'href':os.path.relpath(p,output.parent)} for l,p in artifacts]
    data['metadata']={k:v for k,v in raw.items() if k!='cases'}
    data['metadata'].update(usage=usage(raw),baseline_usage=usage(baseline),baseline_metrics=om,changes=dict(changes),raw_sha256=sha(raw_path),reviews_sha256=sha(reviews_path),
        actual_request_parameters={k:v for k,v in raw['cases'][0]['calls'][0]['request'].items() if k!='input'},
        sampling_note='未发送 temperature 或模型 seed；无自动重试。服务端管理前缀缓存。耗时包含网络和正文解析，不含 ASR/OCR 或输入法写回。',
        renderer_sha256=sha(ROOT/'skills/llm-eval-report/scripts/render_report.py'),adapter_sha256=sha(Path(__file__)))
    page,m=render(data);output.write_text(page);normalized.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');output.with_suffix('.metrics.json').write_text(json.dumps(m,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'metrics':m,'baseline':om,'changes':dict(changes)},ensure_ascii=False))

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('raw',type=Path);p.add_argument('--reviews',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--ablation-report',type=Path)
    a=p.parse_args();build(a.raw,a.reviews,a.output,a.ablation_report)

if __name__=='__main__':main()
