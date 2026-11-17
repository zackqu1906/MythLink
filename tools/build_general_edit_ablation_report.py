#!/usr/bin/env python3
"""Offline, hash-bound paired comparison and exact/contained-missing-rule filters."""
from __future__ import annotations
import argparse
from collections import Counter
import csv
import difflib
from html import escape
import importlib.util
import json
from pathlib import Path
import re
import statistics
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.check_general_edit_ablation import digest, json_digest, sha, save

RENDERER=Path('/Users/admin/.codex/skills/llm-eval-report/scripts/render_report.py')
spec=importlib.util.spec_from_file_location('general_ablation_renderer',RENDERER)
renderer=importlib.util.module_from_spec(spec);spec.loader.exec_module(renderer)
FULL='general_full_v1'

FILTER_JS = r'''
const cards=Array.from(document.querySelectorAll('article.case'));
const filterIds=['result','group','missing','change','repeat','search'];
const controls=filterIds.map(id=>document.getElementById(id));
function matches(c,result,group,missing,change,repeat,q){
 const d=c.dataset;
 const resultOk=result==='all'||d.status===result||(result==='bodyfail'&&d.body==='fail'&&d.status!=='observation')||(result==='mechanical'&&d.mechanical==='true');
 return resultOk&&(group==='all'||d.group===group)&&(missing==='all'||d.missing.split(',').includes(missing))&&(change==='all'||d.change===change)&&(repeat==='all'||(repeat==='main'?d.repeat==='1':d.repeat!=='1'))&&(!q||d.search.toLowerCase().includes(q));
}
function applyFilters(){const values=controls.map(x=>x.value);values[5]=values[5].trim().toLowerCase();let n=0;cards.forEach(c=>{c.hidden=!matches(c,...values);if(!c.hidden)n++;});document.getElementById('count').textContent=`显示 ${n} / ${cards.length}`;document.getElementById('empty').hidden=n!==0;}
controls.forEach(c=>c.addEventListener(c.tagName==='INPUT'?'input':'change',applyFilters));
document.getElementById('reset').addEventListener('click',()=>{controls.forEach(c=>c.value=c.tagName==='INPUT'?'':'all');applyFilters();});
applyFilters();
'''


def paired_change(row, baseline):
    if row['variant']==FULL:return 'control'
    old,new=baseline['review']['status'],row['review']['status']
    if old=='pass' and new=='fail':return 'worse'
    if old=='fail' and new=='pass':return 'better'
    if old==new=='pass':return 'both_pass'
    if old==new=='fail':return 'both_fail'
    return 'unresolved'


def usage(row):
    return row['calls'][0].get('response',{}).get('usage',{}) if row['calls'] else {}


def load_reviews(out, plan):
    """Normalize omitted empty pass issues without changing any semantic decision."""
    batches={b['id']:b for b in plan['batches']}
    reviews={};calls=[];repairs=[];seen=set()
    for b in [json.loads(s) for s in (out/'reviews.jsonl').read_text().splitlines()]:
        calls.extend(b['calls'])
        assert b['id'] not in seen, 'Review attempts need explicit adjudication'
        seen.add(b['id'])
        expected={c['candidate_id']:c for c in batches[b['id']]['payload']['candidates']}
        parsed=b.get('reviews')
        if parsed is None:
            assert b.get('error') == "'issues'", b.get('error')
            parsed=json.loads(b['raw_response'])['reviews']
            repairs.append({'batch_id':b['id'],'original_error':b['error'],
                            'normalization':'Fill omitted issues=[] only for pass/uncertain; preserve every status/note/fail issue.'})
        assert len(parsed)==len(expected) and {r['candidate_id'] for r in parsed}==set(expected)
        for r in parsed:
            r=dict(r)
            assert r['status'] in ('pass','fail','uncertain') and r['note']
            if r['status'] in ('pass','uncertain'):r.setdefault('issues',[])
            if r['status']=='pass':assert not r['issues']
            if r['status']=='fail':
                if 'issues' not in r and r.get('expected') and r.get('actual'):
                    r['issues']=[{'reason':r['note'],'expected':r['expected'],'actual':r['actual']}]
                    repairs.append({'candidate_id':r['candidate_id'],'normalization':'Wrap existing flat expected/actual and note in issues; no semantic change.'})
                assert r['issues'] and all(all(i.get(k) for k in ('reason','expected','actual')) for i in r['issues'])
            r['output_sha256']=digest(expected[r['candidate_id']]['output'])
            assert r['candidate_id'] not in reviews
            reviews[r['candidate_id']]=r
    assert seen==set(batches), 'Review coverage incomplete'
    save(out/'review-normalization.json',{'source_sha256':sha(out/'reviews.jsonl'),'repairs':repairs})
    return reviews,calls


def build(out):
    manifest=json.loads((out/'manifest.json').read_text())
    plan=json.loads((out/'review-plan.json').read_text())
    assert plan['source_sha256']==sha(out/'generations.jsonl')
    assert plan['manifest_sha256']==sha(out/'manifest.json')
    status=json.loads((out/'run-status.json').read_text())
    assert status['complete'] and not status['source_drift']
    rows=[json.loads(s) for s in (out/'generations.jsonl').read_text().splitlines()]
    assert len(rows)==manifest['planned_requests']
    expected_ids={f'{b["case_id"]}__r{b["repeat"]}__{v}' for b in manifest['blocks'] for v in b['variants']}
    assert len({r['id'] for r in rows})==len(rows) and {r['id'] for r in rows}==expected_ids
    sources={c['id']:c for c in manifest['cases']}
    reviews,review_calls=load_reviews(out,plan)
    overrides={}
    audit={}
    if (out/'audit-overrides.json').exists():
        audit=json.loads((out/'audit-overrides.json').read_text())
        assert audit['source_sha256']==sha(out/'generations.jsonl')
        overrides=audit['reviews']
    request_params=[]
    for r in rows:
        c=sources[r['case_id']];v=manifest['variants'][r['variant']]
        inp={'utterance':c['utterance'].strip(),'editable_text':c['target'],'screen_context':c['context']}
        assert r['model_input']==inp and r['input_sha256']==json_digest(inp)
        assert r['output_sha256']==digest(r['final_text'])
        assert len(r['calls'])==1
        call=r['calls'][0];payload=call['request']
        assert payload['input'][0]['content'][0]['text']==v['prompt']
        assert json.loads(payload['input'][1]['content'][0]['text'])==inp
        request_params.append({k:value for k,value in payload.items() if k!='input'})
        if c['cohort']=='guard':
            review={'status':'observation','note':'旧防误触发语境与本次已确认编辑模式不同；展示输出，不纳入正确率。','issues':[],'output_sha256':r['output_sha256']}
        elif r.get('error'):
            review={'status':'pending','note':'请求失败，没有可复核的有效正文。','issues':[],'output_sha256':r['output_sha256']}
        else:
            key=plan['bindings'][r['id']]
            review=dict(overrides.get(key,reviews[key]))
            assert review['output_sha256']==r['output_sha256']
        review['input_sha256']=r['input_sha256']
        r['review']=review
    assert all(p==request_params[0] for p in request_params), 'Parameter drift'
    by_key={(r['case_id'],r['repeat'],r['variant']):r for r in rows}
    labels=manifest['rule_labels'];variants=manifest['variants']
    changes={'control':'完整对照','worse':'完整通过 → 删减失败','better':'完整失败 → 删减通过','both_pass':'两组均通过','both_fail':'两组均失败','unresolved':'含待确认／观察'}
    normalized=[]
    variant_order=list(variants)
    for r in sorted(rows,key=lambda r:(r['case_id'],r['repeat'],variant_order.index(r['variant']))):
        c=sources[r['case_id']];v=variants[r['variant']]
        baseline=by_key[(r['case_id'],r['repeat'],FULL)]
        change=paired_change(r,baseline);r['change']=change
        missing='、'.join(labels[k] for k in v['missing']) or '无（完整对照）'
        expected=c.get('review_points') or '完成口述中全部有效要求；按统一 correctness-v1 口径检查明确改错、遗漏与违令。'
        if c['generation_expected']:
            expected+='\n冻结的辅助检查（不要求只有一种表达）：'+json.dumps(c['generation_expected'],ensure_ascii=False)
        normalized.append({'id':r['id'],'group':r['variant'],'features':[c['cohort'],f'第 {r["repeat"]} 次',changes[change]],
            'instruction':c['utterance'],'source_text':c['target'],'context':c['context'],
            'expected_result':expected,'output':r['final_text'],'latency_s':r['latency_s'],
            'error':r.get('error',''),'observation':c['cohort']=='guard','review':r['review'],
            'checks':r.get('checks',[]),
            'fields':[{'label':'缺失的精细规则（general 始终保留）','value':missing},
                      {'label':'同例同轮完整组','value':f'{baseline["review"]["status"]} · {baseline["latency_s"]:.3f}s；{changes[change]}'},
                      {'label':'完整组输出','value':baseline['final_text']},
                      {'label':'完整组复核','value':baseline['review']['note']},
                      {'label':'与完整组的配对耗时差','value':f'{r["latency_s"]-baseline["latency_s"]:+.3f}s（负数较快）'}],
            'details':[{'title':'本组实际使用的精细规则','text':'、'.join(labels[k] for k in manifest['fine_rules'] if k not in v['missing']) or '无精细规则，只有相同 general 兜底。'}],
            'evidence':{'id':r['id'],'input_sha256':r['input_sha256'],'output_sha256':r['output_sha256'],
                        'system_prompt_file':r['variant']+'.txt','system_prompt_sha256':v['prompt_sha256'],
                        'request':{**request_params[0],'input':r['model_input']},
                        'response':r['calls'][0].get('response',{}),'review':r['review'],
                        'raw_request_location':'generations.jsonl 中相同 id 的 calls[0].request'},
            'missing':v['missing'],'repeat':r['repeat'],'change':change})
    main_rows=[];repeat_rows=[];token_rows=[];audit_rows=[];stats_by_variant={}
    primary=[r for r in rows if r['repeat']==1 and sources[r['case_id']]['cohort']!='guard']
    for variant,v in variants.items():
        subset=[r for r in primary if r['variant']==variant]
        base=[by_key[(r['case_id'],1,FULL)] for r in subset]
        passed=sum(r['review']['status']=='pass' and not r.get('error') for r in subset)
        base_pass=sum(r['review']['status']=='pass' and not r.get('error') for r in base)
        counts=Counter(r['review']['status'] for r in subset)
        delta=[r['latency_s']-b['latency_s'] for r,b in zip(subset,base) if not r.get('error') and not b.get('error')]
        latencies=[r['latency_s'] for r in subset if not r.get('error')]
        improved=sum(r['change']=='better' for r in subset);worse=sum(r['change']=='worse' for r in subset)
        s={'n':len(subset),'pass':passed,'fail':counts['fail'],'uncertain':counts['uncertain'],
           'accuracy':passed/len(subset),'delta_pp':100*(passed-base_pass)/len(subset),
           'p50_s':renderer.percentile(latencies,.5),'p95_s':renderer.percentile(latencies,.95),
           'paired_latency_median_s':statistics.median(delta),'improved':improved,'worse':worse,
           'prompt_chars':len(v['prompt'])}
        stats_by_variant[variant]=s
        before=sum(reviews[plan['bindings'][r['id']]]['status']=='pass' for r in subset if not r.get('error'))
        audit_rows.append([v['label'],f'{before}/210',f'{passed}/210',
                           sum(plan['bindings'].get(r['id']) in overrides for r in subset)])
        main_rows.append([v['label'], '、'.join(labels[k] for k in v['missing']) or '无',
            f'{passed}/{len(subset)} ({100*s["accuracy"]:.1f}%)',f'{s["delta_pp"]:+.1f} pp',
            f'{improved} / {worse}',counts['uncertain'],f'{s["p50_s"]:.3f}',f'{s["p95_s"]:.3f}',f'{s["paired_latency_median_s"]:+.3f}'])
        rs=[r for r in rows if r['variant']==variant and r['case_id'] in manifest['repeated_subset']]
        triplets=[[by_key[(cid,rep,variant)]['review']['status'] for rep in (1,2,3)] for cid in manifest['repeated_subset']]
        repeat_rows.append([v['label'],len(rs),sum(r['review']['status']=='pass' for r in rs),
                            sum(all(x=='pass' for x in t) for t in triplets),
                            sum(len(set(t))>1 for t in triplets)])
        usages=[usage(r) for r in subset];ins=sum(u.get('input_tokens',0) for u in usages)
        cached=sum(u.get('input_tokens_details',{}).get('cached_tokens',0) for u in usages)
        token_rows.append([v['label'],len(v['prompt']),f'{ins/len(usages):.1f}',
                           f'{sum(u.get("output_tokens",0) for u in usages)/len(usages):.1f}',
                           f'{100*cached/ins:.1f}%' if ins else '未报告'])
    full=stats_by_variant[FULL];general=stats_by_variant['general_only_v1']
    summary=[f'完成 {len(rows)} 次真实编辑请求：同一 216 条既有开发样例 × 7 个版本，另对冻结的 24 条每组多测 2 次。主表每组 210 条，6 条旧防误触发案例仅观察；重复试验单列。',
             '“缺失”只指删除精细规则；每组都逐字保留相同 general 编辑要求。完整对照是本次 general 底座加全部精细块；历史无分类 v3 的跨类别示例已在所有组统一替换，不把历史分数当同期基线。',
             f'首次全量：完整组 {full["pass"]}/210，只保留 general {general["pass"]}/210（差 {general["delta_pp"]:+.1f} 个百分点）。只保留 general 的 P50 {general["p50_s"]:.3f}s，完整组 {full["p50_s"]:.3f}s；配对延迟差中位数 {general["paired_latency_median_s"]:+.3f}s。',
             '准确率为同模型独立盲评请求所得的规则符合率，Codex 对可疑判定的修订另存且绑定输出哈希；不是独立人工验收。旧数据用于开发多次，不能当未见测试集，也不能据一次全量比较声称统计显著。',
             '本轮各删减版本没有呈现单调的准确率或延迟变化；只缺释字为 195/210，缺释字与润色反而为 199/210。这些小差异混合了样本内效果与输出随机性，不能据此认定删规则普遍更优。',
             '服务端缓存未控制，所有组固定相同并发、随机交错；短提示词也改变缓存命中和输入长度。延迟只含文字模型请求及正文校验，不含录音、ASR、OCR、输入法写回；非冷启动延迟。',
             '下方通用总览包含额外重复记录；判断提示词差异请优先看“首次全量配对比较”。每张卡片同时列出该组与完整组的正文、复核和耗时。']
    prompts=[]
    for variant,v in variants.items():
        diff=''.join(difflib.unified_diff(variants[FULL]['prompt'].splitlines(True),v['prompt'].splitlines(True),fromfile=FULL,tofile=variant))
        (out/(variant+'.diff')).write_text(diff)
        prompts.append([v['label'],'、'.join(labels[k] for k in v['missing']) or '无',len(v['prompt']),v['prompt_sha256'][:16]])
    data={'schema_version':1,'evaluation':{'classification':False},'title':'general 编辑要求：精细规则删减对照',
          'groups':{v:spec['label'] for v,spec in variants.items()},'summary':summary,
          'methodology':manifest['design']+' '+plan['method'],'cases':normalized,
          'links':[{'label':'全部原始请求与响应','href':'generations.jsonl'},
                   {'label':'冻结清单与全部提示词','href':'manifest.json'},
                   {'label':'general 兜底提示词','href':'general-core.txt'},
                   {'label':'规范化报告数据','href':'report.json'},
                   {'label':'逐条复核记录','href':'bound-reviews.json'},
                   {'label':'原始盲评请求','href':'reviews.jsonl'},
                   {'label':'Codex 修订与原判定','href':'audit-overrides.json'},
                   {'label':'复核格式归一化记录','href':'review-normalization.json'},
                   {'label':'统计数据','href':'comparison.metrics.json'},
                   {'label':'全部结果 CSV','href':'results.csv'}],
          'analysis_tables':[
              {'title':'首次全量配对比较（每组 210 条，准确率主表）','description':'同一案例、同一次重复配对；通过差为百分点。改善＝完整失败而删减通过；退步＝完整通过而删减失败。待确认不计通过。秒为完整请求耗时。',
               'columns':['提示词','缺失精细规则','正确率','比完整组','改善 / 退步','待确认','P50 秒','P95 秒','配对差中位数 秒'],'rows':main_rows},
              {'title':'额外重复：固定 24 条，每组总共 3 次','description':'只衡量这一固定子集的重复稳定性，不混入主表分母。选择规则在运行前冻结，未按结果挑样例。','columns':['提示词','请求数','通过数','3 次均过 / 24','状态有波动 / 24'],'rows':repeat_rows},
              {'title':'输入长度、输出长度与缓存','description':'取首次全量 210 条；缓存为服务端 usage 报告的 cached input / input tokens。实际请求没有 temperature 或模型 seed。','columns':['提示词','提示词字符','平均输入 tokens','平均输出 tokens','缓存输入比例'],'rows':token_rows},
              {'title':'提示词实验矩阵','description':'所有版本通用底座完全相同；缺失的是整块精细说明。完整提示词和逐行 diff 可在下方展开查看。','columns':['版本','缺失精细块','字符数','SHA256 前 16 位'],'rows':prompts},
              {'title':'评分审计：保留原始盲评与修订','description':audit.get('method','')+' 修订不重新生成编辑正文、不改变延迟。字段缺失只做结构归一化；判分修订另存，不覆盖原始模型评语。','columns':['提示词','原始盲评通过','审计后通过','被修订评语覆盖的记录'],'rows':audit_rows},
          ],
          'metadata':{'model':request_params[0]['model'],'actual_request_parameters':request_params[0],
                      'created_at':manifest['created_at'],'finished_at':status['finished_at'],
                      'manifest_sha256':sha(out/'manifest.json'),'generations_sha256':sha(out/'generations.jsonl'),
                      'reviews_sha256':sha(out/'reviews.jsonl'),'review_model':review_calls[0]['request']['model'] if review_calls else None,
                      'review_calls':len(review_calls),'review_candidates':len(reviews),'audit_overrides':len(overrides),
                      'source_sha256':manifest['source_sha256'],'renderer_sha256':sha(RENDERER),
                      'adapter_sha256':sha(Path(__file__))}}
    renderer.validate(data)
    page,stats=renderer.render(data)
    controls='<select id="missing" aria-label="缺失的精细条目"><option value="all">按缺失条目筛选（含累计缺失）</option>'+''.join(f'<option value="{k}">{escape(v)}</option>' for k,v in labels.items())+'</select>'
    controls+='<select id="change" aria-label="相对完整组变化"><option value="all">全部配对结果</option>'+''.join(f'<option value="{k}">{escape(v)}</option>' for k,v in changes.items())+'</select>'
    controls+='<select id="repeat" aria-label="重复轮次"><option value="all">全部轮次</option><option value="main">首次全量</option><option value="extra">额外重复</option></select>'
    page=page.replace('<input id="search"',controls+'<input id="search"')
    page=page.replace('aria-label="样例组"','aria-label="提示词缺失组合"').replace('全部分组</option>','全部提示词缺失组合</option>')
    for i,c in enumerate(normalized,1):
        page=page.replace(f'id="case-{i}" ',f'id="case-{i}" data-missing="{escape(",".join(c["missing"]),quote=True)}" data-repeat="{c["repeat"]}" data-change="{c["change"]}" ',1)
    page=page.replace(renderer.JS,FILTER_JS)
    # Lead with the matched first-run table, not the repetition-weighted aggregate.
    match=re.search(r'<section class="panel"><h3>首次全量配对比较.*?</section>',page,re.S)
    assert match
    primary_table=match.group(0)
    page=page.replace(primary_table,'',1).replace('<div class="kpis">',primary_table+'<div class="kpis">',1)
    prompt_panel='<section class="panel"><h3>逐版提示词与删除 diff</h3><p>general 底座在所有组相同；下面显示完整实际发送文本。</p>'
    for variant,v in variants.items():
        prompt_panel+=f'<details><summary>{escape(v["label"])} · {len(v["prompt"])} 字符</summary><p><a href="{variant}.txt">提示词文件</a> · <a href="{variant}.diff">相对完整组 diff</a></p><pre>{escape(v["prompt"])}</pre></details>'
    prompt_panel+='</section>'
    page=page.replace('<h2 id="cases">',prompt_panel+'<h2 id="cases">',1)
    page=page.replace('全部要求通过<b>','全部记录通过（含重复）<b>',1).replace('<h3>样例分布与表现</h3>','<h3>全部记录统计（含额外重复；主比较见下表）</h3>',1)
    save(out/'report.json',data);(out/'report.html').write_text(page)
    save(out/'report.metrics.json',stats);save(out/'comparison.metrics.json',stats_by_variant)
    save(out/'bound-reviews.json',{'source_sha256':sha(out/'generations.jsonl'),'method':plan['method'],
                                  'cases':{r['id']:r['review'] for r in rows}})
    with (out/'results.csv').open('w',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['id','case_id','variant','missing','repeat','status','latency_s','instruction','source_text','output','review_note','baseline_output'])
        for r in rows:
            c=sources[r['case_id']]
            writer.writerow([r['id'],r['case_id'],r['variant'],','.join(variants[r['variant']]['missing']),r['repeat'],r['review']['status'],r['latency_s'],c['utterance'],c['target'],r['final_text'],r['review']['note'],by_key[(r['case_id'],r['repeat'],FULL)]['final_text']])
    print(json.dumps(stats_by_variant,ensure_ascii=False,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('output_dir',type=Path)
    build(p.parse_args().output_dir)
