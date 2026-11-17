#!/usr/bin/env python3
"""Build boundary-only experiment report with hash-checked historical body comparison.

Uses a local generation-only adaptation of the llm-eval-report skill renderer.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import html
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.boundary_eval_report import render,validate,metrics,seconds,CSS
from tools.build_routed_edit_eval_report import build as build_baseline
from tools.check_boundary_only_edit import BASELINE,model_input

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def usage(raw):
    us=[call.get('response',{}).get('usage',{}) for c in raw['cases'] for call in c['calls']]
    return {'requests':len(us),'input_tokens':sum(u.get('input_tokens',0) for u in us),
        'output_tokens':sum(u.get('output_tokens',0) for u in us),
        'cached_requests':sum(u.get('input_tokens_details',{}).get('cached_tokens',0)>0 for u in us),
        'cached_tokens':sum(u.get('input_tokens_details',{}).get('cached_tokens',0) for u in us)}
def build(raw_path,review_path,output):
    raw=json.loads(raw_path.read_text()); rev=json.loads(review_path.read_text())
    assert rev['source_sha256']==sha(raw_path)
    assert raw['baseline']['sha256']==sha(BASELINE)
    old=build_baseline(BASELINE,BASELINE.with_name(BASELINE.name.replace('.raw.json','.reviews.json')))
    prior={c['id']:c for c in old['cases']};oldraw=json.loads(BASELINE.read_text())
    assert set(rev['cases'])==set(prior)=={c['id'] for c in raw['cases']}
    cases=[]
    for c in raw['cases']:
        r=rev['cases'][c['id']];b=prior[c['id']]
        assert r['input_sha256']==c['input_sha256']==b['review']['input_sha256']
        assert len(c['calls'])==1
        wire=c['calls'][0]['request']
        assert json.loads(wire['input'][1]['content'][0]['text'])==model_input(c)
        assert wire['input'][0]['content'][0]['text']==raw['prompt']
        assert wire['stream'] is False and wire['thinking']=={'type':'disabled'} and wire['max_output_tokens']==32768
        assert 'temperature' not in wire
        before,after=b['review']['status'],r['status']
        change=('observation' if c['cohort']=='guard' else 'uncertain' if 'uncertain' in (before,after) else
                'pending' if 'pending' in (before,after) else 'improved' if (before,after)==('fail','pass') else
                'regressed' if (before,after)==('pass','fail') else 'stablepass' if before==after=='pass' else 'stablefail')
        cases.append({**b,'classification_applicable':False,'actual_labels':[],'route_note':'',
            'output':c.get('final_text',''),'error':c.get('error',''),'review':r,'latency_s':c['latency_s'],
            'checks':c.get('checks',[]),'context':c['context'],'change':change,
            'baseline':{k:b[k] for k in ('output','review','latency_s','actual_labels','route_match','error')},
            'evidence':{'id':c['id'],'input_sha256':c['input_sha256'],'source_file':c.get('source_file'),
                'actual_model_input':c.get('model_input'),'raw_response':c.get('raw_response'),'calls':c['calls'],'review':r}})
    u=usage(raw);ou=usage(oldraw)
    data={'schema_version':1,'title':'无显式分类 vs 六类 v5：同一批 216 条编辑评测',
        'labels':old['labels'],'groups':{**old['groups'],'combination':'原批次 48 条多要求组合'},'cases':cases,
        'methodology':rev['method'],
        'metadata':{**{k:v for k,v in raw.items() if k!='cases'},'usage':u,'baseline_usage':ou,
            'report_source_sha256':{str(p.relative_to(ROOT)):sha(p) for p in
                (Path(__file__),ROOT/'tools/boundary_eval_report.py')},
            'baseline_created_at':oldraw['created_at'],'baseline_finished_at':oldraw['finished_at'],
            'review_method':rev['method'],'raw_sha256':sha(raw_path),'review_sha256':sha(review_path),
            'renderer_origin':'llm-eval-report/scripts/render_report.py，局部适配分类不适用与历史正文对照；未改全局 skill。',
            'classification_metric':'not_applicable','sampling':'Ark Responses 未发送 temperature，服务默认采样；stream=false，thinking=disabled，max_output_tokens=32768；逐例一次请求，无重试。',
            'prompt_change':'删除显式分类、条件选模块和分类输出，将相邻类别定义改为行为边界，保留六类执行限制及组合规则。非空 prompt 从5921字符降到4679；空目标原4869字符，本轮统一4679字符。不是等长仅删标签的严格消融。'},
        'links':[{'label':'完整新 prompt（可直接查看）','href':output.stem+'-prompt.html'},
                 {'label':'完整 system TXT','href':output.stem+'-system.txt'},
                 {'label':'一次真实请求示例（完整 system + user）','href':output.stem+'-request-example.txt'},
                 {'label':'216 条原始请求与响应','href':raw_path.name},
                 {'label':'逐条复核记录','href':review_path.name},
                 {'label':'六类 v5 原报告','href':'manual-routed-edit-v5-eval-2026-11-11.html'}],
        'summary':[]}
    validate(data);m=metrics(cases);bm=metrics(old['cases']);changes=Counter(c['change'] for c in cases if not c['observation'])
    newcomb=metrics([c for c in cases if c['group']=='combination']);oldcomb=metrics([c for c in old['cases'] if c['group']=='combination'])
    oldroute=[c for c in cases if c['baseline']['route_match'] is False]
    routepasses=sum(c['review']['status']=='pass' for c in oldroute)
    mult=[c for c in cases if len(c['expected_labels'])>=2]
    data['summary']=[
        '问题是：编辑是否必须显式先选类别？本组只给执行边界和最终目标，模型在一次请求内直接生成正文，不输出类别；全部 216 次请求完成，调用和正文解析无异常，未改主应用代码。模型仍为 '+raw['model']+'。',
        '数据、预期、顺序与上一轮完全一致：126 条回归、24 条历史新增、12 条省略歧义、48 条多要求组合，以及 6 条旧 guard 观察。预期类别只作离线标签，不发送给模型。全部是已用于开发的合成测试，不是独立真实用户留出集。',
        f'按同一正文口径：六类 v5 通过 {bm["body"].get("pass",0)}/210（{bm["body"].get("pass",0)/210:.1%}），无分类通过 {m["body"].get("pass",0)}/210（{m["body"].get("pass",0)/210:.1%}）；本轮失败 {m["body"].get("fail",0)}、待确认 {m["body"].get("uncertain",0)}。旧失败→通过 {changes["improved"]} 条，旧通过→失败 {changes["regressed"]} 条；两组都失败 {changes["stablefail"]} 条。另有一条旧待确认样例本轮丢失原文后缀，出现可确定错误。分类指标在本组不适用，不能拿旧“分类＋正文”170/210和新正文分数直接比较。',
        f'你指出的旧路由错误 {len(oldroute)} 条，本轮正文 {routepasses}/{len(oldroute)} 通过。这支持“类别标签错误并不等于无法编辑”的观察。48 条组合正文从 {oldcomb["body"].get("pass",0)}/48 到 {newcomb["body"].get("pass",0)}/48；全部 {len(mult)} 条多要求样例可在下方筛选。',
        '修复包含只删谢谢时保留逗号、最终英文表格不再重复中间稿、严格篇幅与用户回复范围。回退包含漏掉独立英文祝福、输出中间稿、未授权弱化行动要求、取消润色后仍改范围外文字。释字误改姓氏、虚构事实或承诺、复杂取消指令等在两组都存在。',
        f'本次无分类 P50/P95 为 {seconds(m["latency"]["p50"])} / {seconds(m["latency"]["p95"])}；旧六类为 {seconds(bm["latency"]["p50"])} / {seconds(bm["latency"]["p95"])}。输入 token {ou["input_tokens"]:,} → {u["input_tokens"]:,}，输出 {ou["output_tokens"]:,} → {u["output_tokens"]:,}。新组 {u["cached_requests"]}/216 次、旧组 {ou["cached_requests"]}/216 次命中过前缀缓存。延迟含网络及解析，不含音频、OCR 采集或输入法写回。',
        '结论：当前样例上无分类正文质量接近六类方案，显式分类没有显示出明显的质量优势；但也未证明去分类会提升质量。两组非同期、各采样一次，提示词长度、措辞与输出协议同时变化，不能把差异完全归因于分类。可以继续保留无分类作为候选，后续用未见样例和交错重复配对测试决定是否替换主流程。',
    ]
    # Full prompt artifacts are derived from the captured request, not reconstructed later.
    sample=next(c for c in raw['cases'] if c['id']=='v5-combo-five-separated')
    wire=sample['calls'][0]['request'];user=wire['input'][1]['content'][0]['text']
    prefix=output.with_suffix('')
    Path(str(prefix)+'-system.txt').write_text(raw['prompt'])
    Path(str(prefix)+'-request-example.txt').write_text('[system]\n'+raw['prompt']+'\n\n[user]\n'+user+'\n')
    Path(str(prefix)+'-request-example.json').write_text(json.dumps(wire,ensure_ascii=False,indent=2)+'\n')
    e=html.escape
    Path(str(prefix)+'-prompt.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>无分类 v1 完整发送提示词</title><style>'+CSS+'</style><body><main><h1>无分类 v1：完整发送提示词</h1><p>实际216次请求使用同一个 system。下方 user 来自本轮一条合成五要求组合案例；其他案例只替换这三个输入字段。模型不收到预期标签、判定标准或旧输出。</p><p><a href="'+e(output.name)+'">返回完整评测报告</a> · <a href="'+e(output.stem+'-request-example.json')+'">下载实际请求体 JSON</a></p><section class="panel"><h2>System · 4679 字符</h2><pre>'+e(raw['prompt'])+'</pre></section><section class="panel"><h2>User · 真实样例</h2><pre>'+e(json.dumps(json.loads(user),ensure_ascii=False,indent=2))+'</pre></section><p>一次 Responses 请求，thinking=disabled，stream=false，max_output_tokens=32768。未发送 temperature；无工具、无二次路由请求。</p></main></body></html>')
    return data

def main():
    p=argparse.ArgumentParser();p.add_argument('raw',type=Path);p.add_argument('--reviews',required=True,type=Path);p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    d=build(a.raw,a.reviews,a.output);page,m=render(d)
    a.output.write_text(page)
    a.output.with_suffix('.report.json').write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
    a.output.with_suffix('.metrics.json').write_text(json.dumps(m,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(m,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
