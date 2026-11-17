#!/usr/bin/env python3
"""Offline ablation adapter; consumes hash-bound reviews, never calls a model."""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.eval_report import render, validate, metrics, percentile, seconds

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def digest(value):return hashlib.sha256(value.encode()).hexdigest()
def input_digest(value):return digest(json.dumps(value,ensure_ascii=False,sort_keys=True))

def expected_text(g):
    lines=['中文姓名：'+'、'.join(g['names']),g['facts']]
    if g['english']:lines.append('除保留的中文姓名外，所有内容译为英文。')
    if g['polite']:lines.append('语气礼貌、柔和；不能弱化明确要求或硬性截止。')
    if g['reminder']:lines.append('保留“提交前核对数据”这件事，并服从整体语言要求。')
    if g['numbered']:lines.append(f'按事项恰好排成 {g["numbered"]} 条编号，不附中间结果。')
    return '\n'.join(lines)

def build(raw_path,reviews_path,output,baseline_path=None):
    raw=json.loads(raw_path.read_text());reviews=json.loads(reviews_path.read_text())
    if reviews['source_sha256']!=sha(raw_path):raise ValueError('Reviews belong to a different run')
    manifest_path=raw_path.parent/raw['input_manifest']['file']
    if sha(manifest_path)!=raw['input_manifest']['sha256']:raise ValueError('Input manifest changed')
    manifest=json.loads(manifest_path.read_text())
    frozen={c['id']:c for c in manifest['cases']}
    if digest(raw['prompt'])!=raw['prompt_sha256'] or manifest['system_prompt']!=raw['prompt']:raise ValueError('Prompt mismatch')
    if not raw['completed_all'] or raw['changed_sources']:raise ValueError('Incomplete run or source drift')
    if [c['id'] for c in raw['cases']]!=manifest['trial_order']:raise ValueError('Trial order changed')
    if set(reviews['cases'])!={c['id'] for c in raw['cases']}:raise ValueError('Review coverage mismatch')
    baseline=None;baseline_reviews=None;baseline_cases={}
    if baseline_path:
        baseline=json.loads(baseline_path.read_text())
        baseline_reviews=json.loads(baseline_path.with_name(baseline_path.name.replace('.raw.json','.reviews.json')).read_text())
        if baseline_reviews['source_sha256']!=sha(baseline_path):raise ValueError('Stale baseline reviews')
        baseline_cases={c['id']:c for c in baseline['cases']}
        if set(baseline_cases)!={c['id'] for c in raw['cases']}:raise ValueError('Baseline trial mismatch')
    cases=[];calls=[];pipeline_rows=[]
    for c in sorted(raw['cases'],key=lambda c:c['id']):
        f=frozen[c['condition_id']];r=reviews['cases'][c['id']]
        if c['steps']!=f['steps'] or c['source_text']!=f['source_text']:raise ValueError('Frozen condition changed')
        if len(c['results'])!=len(c['steps']) or len(r['steps'])!=len(c['results']):raise ValueError('Step coverage mismatch')
        source=c['source_text'];details=[];first_problem='无'
        for i,(s,sr,goal) in enumerate(zip(c['results'],r['steps'],c['steps'],strict=True),1):
            if s.get('error'):raise ValueError('This completed-run adapter needs an explicit error review')
            expected={'utterance':goal['instruction'].strip(),'editable_text':source,'screen_context':{}}
            if s['source_text']!=source or s['input_sha256']!=input_digest(expected) or sr['input_sha256']!=s['input_sha256']:raise ValueError('Step input mismatch')
            if sr['output_sha256']!=digest(s['final_text']):raise ValueError('Stale step review')
            if s['criteria']!=goal['criteria'] or len(s['calls'])!=1:raise ValueError('Step contract changed')
            call=s['calls'][0];payload=call['request'];calls.append(call)
            if payload['input'][0]['content'][0]['text']!=raw['prompt'] or json.loads(payload['input'][1]['content'][0]['text'])!=expected:raise ValueError('Unexpected model input')
            if call['endpoint']!=raw['endpoint'] or payload['model']!=raw['model']:raise ValueError('Destination/model changed')
            dummy={'schema_version':1,'evaluation':{'classification':False},'title':'step','cases':[dict(id=c['id'],group=c['family'],instruction=s['instruction'],source_text=source,output=s['final_text'],expected_result=expected_text(s['criteria']),review=sr,assessments=sr['assessments'])]}
            validate(dummy)
            if first_problem=='无' and sr['status']!='pass':first_problem=f'第 {i} 步：{sr["status"]}'
            detail='口述：'+s['instruction']+'\n\n实际输入：\n'+source+'\n\n预期：\n'+expected_text(s['criteria'])+'\n\n实际输出：\n'+s['final_text']+'\n\n复核：'+sr['status']+'\n'+sr['note']+'\n耗时：'+seconds(s['latency_s'])
            details.append({'title':f'第 {i} 步 · {sr["status"]} · {seconds(s["latency_s"])}','text':detail})
            if len(c['steps'])>1:
                pipeline_rows.append([c['id'],i,sr['status'],'、'.join(a['label'] for a in sr['assessments'] if a['status']!='pass') or '全部满足',seconds(s['latency_s'])])
            source=s['final_text']
        if source!=c['final_text'] or r['final']!=r['steps'][-1]:raise ValueError('Final review/result mismatch')
        final=r['final']
        cases.append(dict(id=c['id'],group=c['family'],features=[c['contrast'],c['title']],
            instruction='\n\n'.join(f'第 {i} 次请求：{s["instruction"]}' for i,s in enumerate(c['steps'],1)) if len(c['steps'])>1 else c['steps'][0]['instruction'],
            source_text=c['source_text'],expected_result=expected_text(c['steps'][-1]['criteria']),output=c['final_text'],
            latency_s=c['latency_s'],review=final,assessments=final['assessments'],details=details if len(c['steps'])>1 else [],
            fields=[{'label':'对照目的','value':c['comparison']},{'label':'重复与请求数','value':f'第 {c["repeat"]}/3 次；{len(c["steps"])} 个请求'},
                    {'label':'首次未满足或待确认','value':first_problem}],
            evidence={'condition_id':c['condition_id'],'parent_case':c['parent_case'],'steps':c['results'],'reviews':r['steps']}))
        if baseline:
            old=baseline_cases[c['id']];old_review=baseline_reviews['cases'][c['id']]['final']
            if c['steps']!=old['steps'] or c['source_text']!=old['source_text']:raise ValueError('Baseline conditions differ')
            if old_review['output_sha256']!=digest(old['final_text']):raise ValueError('Stale baseline final review')
            cases[-1]['fields'].extend([{'label':'v1 → v2 全部要求','value':old_review['status']+' → '+final['status']},{'label':'v1 原最终正文','value':old['final_text']}])
            cases[-1]['details'].append({'title':'v1 原复核','text':old_review['note']})
    if len(calls)!=raw['planned_requests']:raise ValueError('Call budget mismatch')
    normalized={'schema_version':1,'evaluation':{'classification':False},'title':'组合指令拆分对照：姓名字形与中文姓名保护'+('（v2 复测）' if baseline else ''),
        'latency_label':'整条试验耗时 P50 / P95','groups':{'spelling':'姓名偏旁＋语气（S）','chain':'五项操作＋保护条件（T）'},'cases':cases}
    validate(normalized)
    rows_by_family={}
    for family in ('spelling','chain'):
        rows=[]
        for cond in [c for c in manifest['cases'] if c['family']==family]:
            subset=[c for c in cases if c['evidence']['condition_id']==cond['id']]
            m=metrics(subset);name_count=sum(next(a for a in c['assessments'] if a['key']=='names')['status']=='pass' for c in subset)
            issues=Counter(a['label'] for c in subset for a in c['assessments'] if a['status']=='fail')
            rows.append([cond['id']+' · '+cond['title'],len(cond['steps']),f'{name_count}/3',f'{m["overall"].get("pass",0)}/3',m['overall'].get('fail',0),m['overall'].get('uncertain',0),seconds(m['latency']['p50']),'；'.join(f'{k} {v}/3' for k,v in issues.items()) or '无确定失败；待确认另计'])
        rows_by_family[family]=rows
    timing_rows=[]
    for count in (1,2,5):
        subset=[c for c in cases if len(c['evidence']['steps'])==count];m=metrics(subset)
        timing_rows.append([count,len(subset),m['overall'].get('pass',0),m['overall'].get('fail',0),m['overall'].get('uncertain',0),seconds(m['latency']['p50']),seconds(m['latency']['p95'])])
    usage=Counter()
    for call in calls:
        u=call['response'].get('usage',{})
        for k in ('input_tokens','output_tokens','total_tokens'):usage[k]+=u.get(k,0)
        usage['cached_input_tokens']+=u.get('input_tokens_details',{}).get('cached_tokens',0)
    request_params=[{k:v for k,v in c['request'].items() if k!='input'} for c in calls]
    if any(p!=request_params[0] for p in request_params):raise ValueError('Request parameter drift')
    normalized['summary']=reviews['summary']
    normalized['methodology']=reviews['method']+' 每轮随机排列全部条件（seed 20261111 仅控制顺序，未向模型发送 seed）。期望与条件在调用前冻结。每步使用真实上一步输出，无参考答案替换、无自动重试。T15 在后续步骤重复保护条件；T14 使用可信预处理文本，因此都不是仅改变任务数量的纯对照。每条件只有 3 次；结论限于本样例、模型和提示词，不是统计显著性或内部机制证明。'
    columns=['条件','请求数','姓名满足','全部通过','失败','待确认','流程 P50','确定失败项']
    normalized['analysis_tables']=[
        {'title':'S 组：偏旁改名与语气的对照','description':'姓名满足只衡量指定姓名；全部通过还要求对象、日期、动作及强制程度成立。S04 没有改名要求，预期仍为张伟。','columns':columns,'rows':rows_by_family['spelling']},
        {'title':'T 组：从单项到完整五项要求','description':'有改名的条件要求张玮；未要求改名的条件为张伟。中文姓名须保留在对应任务中；不能仅在额外中间结果或末尾补回。','columns':columns,'rows':rows_by_family['chain']},
        {'title':'调用成本：按整条试验的请求数分组','description':'流程耗时为脚本开始该试验到最后一步完成，含网络；组内条件不同，不据此估计质量提升的因果效果。单请求 HTTP P50 '+seconds(percentile([c['http_latency_s'] for c in calls],.5))+'，P95 '+seconds(percentile([c['http_latency_s'] for c in calls],.95))+'。缓存由服务端管理，本轮未控制冷缓存。','columns':['每条请求数','试验数','通过','失败','待确认','流程 P50','流程 P95'],'rows':timing_rows},
        {'title':'逐次执行：错误从哪一步出现','description':'每一步都按当时累积的要求复核。待确认与确定失败分列；下面的样例卡片可展开每一步真实输入和输出。','columns':['试验','步骤','状态','未满足／待确认项','步骤耗时'],'rows':pipeline_rows},
    ]
    if baseline:
        comparison=[]
        for condition in manifest['cases']:
            ids=[c['id'] for c in cases if c['evidence']['condition_id']==condition['id']]
            old=[baseline_reviews['cases'][cid]['final'] for cid in ids];new=[reviews['cases'][cid]['final'] for cid in ids]
            def name_count(items):return sum(next(a for a in r['assessments'] if a['key']=='names')['status']=='pass' for r in items)
            comparison.append([condition['id']+' · '+condition['title'],f'{name_count(old)}/3 → {name_count(new)}/3',
                f'{sum(r["status"]=="pass" for r in old)}/3 → {sum(r["status"]=="pass" for r in new)}/3',
                f'{sum(r["status"]=="fail" for r in old)} → {sum(r["status"]=="fail" for r in new)}',
                f'{sum(r["status"]=="uncertain" for r in old)} → {sum(r["status"]=="uncertain" for r in new)}'])
        normalized['analysis_tables'].insert(0,{'title':'同一 28 条条件：v1 → v2','description':'两轮各条件重复 3 次，按同一顺序运行，非同期对照。姓名满足和全部要求通过分别统计。','columns':['条件','姓名满足','全部通过','失败','待确认'],'rows':comparison})
    system_path=output.with_name(output.stem+'-system.txt');system_path.write_text(raw['prompt'])
    normalized_path=output.with_suffix('.report.json')
    artifacts=[('完整系统提示词',system_path),('所有原始请求与响应',raw_path),('冻结条件与运行顺序',manifest_path),('逐步复核记录',reviews_path),('规范化报告数据',normalized_path)]
    import os
    normalized['links']=[{'label':label,'href':os.path.relpath(p,output.parent)} for label,p in artifacts]
    normalized['links'].extend(reviews.get('links', []))
    normalized['metadata']={'model':raw['model'],'endpoint':raw['endpoint'],'entry':raw['entry'],'prompt_version':raw['prompt_version'],
        'prompt_sha256':raw['prompt_sha256'],'created_at':raw['created_at'],'finished_at':raw['finished_at'],'actual_request_parameters':request_params[0],
        'parameter_note':'实际请求未发送 temperature 或 model seed；不能声称温度为 0 或输出确定。stream=false、thinking disabled。',
        'trial_count':len(cases),'request_count':len(calls),'usage':dict(usage),'cache_note':'cached_input_tokens 来自每次服务端 usage；没有控制冷缓存。',
        'latency_note':'KPI 是整条试验流程耗时，包含多步串行；单 HTTP 请求耗时在成本表单列。','raw_sha256':sha(raw_path),'reviews_sha256':sha(reviews_path),
        'source_sha256':raw['source_sha256'],'renderer_sha256':sha(ROOT/'skills/llm-eval-report/scripts/render_report.py'),
        'adapter_sha256':sha(Path(__file__)),'full_system_prompt':raw['prompt']}
    if baseline:
        normalized['metadata']['baseline']={'file':baseline_path.name,'sha256':sha(baseline_path),'created_at':baseline['created_at'],'finished_at':baseline['finished_at'],'model':baseline['model']}
    page,stats=render(normalized)
    normalized_path.write_text(json.dumps(normalized,ensure_ascii=False,indent=2)+'\n')
    output.write_text(page);output.with_suffix('.metrics.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'metrics':stats,'usage':dict(usage),'report':str(output)},ensure_ascii=False))

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('raw',type=Path);p.add_argument('--reviews',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--baseline',type=Path)
    a=p.parse_args();build(a.raw,a.reviews,a.output,a.baseline)

if __name__=='__main__':main()
