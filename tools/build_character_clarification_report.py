"""Offline complete report for all prompt iterations, with semantic paired results."""
from __future__ import annotations
import argparse
from collections import Counter
from html import escape
import importlib.util
import json
from pathlib import Path
import re
import random
import statistics
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.character_clarification_experiment import OUT,save,digest,jd
RENDERER=Path('/Users/admin/.codex/skills/llm-eval-report/scripts/render_report.py')
spec=importlib.util.spec_from_file_location('clarification_renderer',RENDERER)
renderer=importlib.util.module_from_spec(spec);spec.loader.exec_module(renderer)

FILTER_JS='''
const cards=Array.from(document.querySelectorAll('article.case'));
const filterIds=['result','group','split','mechanism','change','search'];
const controls=filterIds.map(id=>document.getElementById(id));
function matches(c,result,group,split,mechanism,change,q){const d=c.dataset;
return (result==='all'||d.status===result||(result==='bodyfail'&&d.body==='fail')||(result==='mechanical'&&d.mechanical==='true'))
&&(group==='all'||d.group===group)&&(split==='all'||d.split===split)&&(mechanism==='all'||d.mechanism===mechanism)
&&(change==='all'||d.change===change)&&(!q||d.search.toLowerCase().includes(q));}
function applyFilters(){const v=controls.map(c=>c.value);v[5]=v[5].trim().toLowerCase();let n=0;
cards.forEach(c=>{c.hidden=!matches(c,...v);if(!c.hidden)n++;});document.getElementById('count').textContent=`显示 ${n} / ${cards.length}`;document.getElementById('empty').hidden=n!==0;}
controls.forEach(c=>c.addEventListener(c.tagName==='INPUT'?'input':'change',applyFilters));
document.getElementById('reset').addEventListener('click',()=>{controls.forEach(c=>c.value=c.tagName==='INPUT'?'':'all');applyFilters();});applyFilters();
'''

def load_reviews(out,plan):
    """Keep raw judgments immutable; only normalize missing empty issue arrays."""
    batches={b['id']:b for b in plan['batches']};reviews={};calls=[];repairs=[];seen=set()
    explicit_repairs=json.loads((out/'review-json-repairs.json').read_text()) if (out/'review-json-repairs.json').exists() else {}
    for line in (out/'reviews.jsonl').read_text().splitlines():
        b=json.loads(line);assert b['id'] not in seen;seen.add(b['id']);calls+=b['calls']
        expected={c['candidate_id']:c for c in batches[b['id']]['payload']['candidates']}
        raw=b.get('raw_response','').strip()
        if raw.startswith('```'):
            raw=re.sub(r'^```(?:json)?\s*|\s*```$','',raw,flags=re.I)
            repairs.append({'batch_id':b['id'],'normalization':'Remove JSON code fence only; no judgment change.'})
        if b['id'] in explicit_repairs:
            fix=explicit_repairs[b['id']];assert fix['raw_sha256']==digest(b['raw_response'])
            parsed=fix['parsed']['reviews'];repairs.append({'batch_id':b['id'],'normalization':fix['reason']})
        else:parsed=b.get('reviews') or json.loads(raw)['reviews']
        actual={r['candidate_id'] for r in parsed}
        assert len(parsed)==len(actual) and actual<=set(expected)
        for missing in set(expected)-actual:
            parsed.append({'candidate_id':missing,'status':'pending','note':'盲评响应漏掉该候选，须由明确记录的补充复核处理。','issues':[]})
            repairs.append({'candidate_id':missing,'normalization':'Judge omitted candidate; pending placeholder only, no invented model verdict.'})
        for r in parsed:
            r=dict(r)
            if 'issues' not in r and r['status'] in ('pass','uncertain'):
                r['issues']=[];repairs.append({'candidate_id':r['candidate_id'],'normalization':'Fill omitted empty issues; no status change.'})
            if r['status']=='fail' and not r.get('issues'):
                r['issues']=[{'reason':r['note'],'expected':batches[b['id']]['payload']['utterance'],'actual':expected[r['candidate_id']]['output']}]
                repairs.append({'candidate_id':r['candidate_id'],'normalization':'Wrap existing failure note with original instruction and actual candidate output; no status change.'})
            r['output_sha256']=digest(expected[r['candidate_id']]['output']);reviews[r['candidate_id']]=r
    assert seen==set(batches)
    save(out/'review-normalization.json',{'repairs':repairs,'source_sha256':digest((out/'reviews.jsonl').read_text())})
    return reviews,calls

def load_run(name):
    out=OUT/name;m=json.loads((out/'manifest.json').read_text());plan=json.loads((out/'review-plan.json').read_text())
    assert plan['source_sha256']==digest((out/'generations.jsonl').read_text())
    assert plan['manifest_sha256']==digest((out/'manifest.json').read_text())
    reviews,calls=load_reviews(out,plan)
    overrides={}
    if (out/'audit-overrides.json').exists():
        audit=json.loads((out/'audit-overrides.json').read_text());assert audit['source_sha256']==plan['source_sha256'];overrides=audit['reviews']
    rows=[json.loads(s) for s in (out/'generations.jsonl').read_text().splitlines()]
    cases={c['id']:c for c in m['cases']};assert len(rows)==m['planned_requests']
    expected={f'{b["case_id"]}__r{b["repeat"]}__{v}' for b in m['blocks'] for v in b['variants']}
    assert {r['id'] for r in rows}==expected and len(rows)==len(expected)
    params=[]
    for r in rows:
        c=cases[r['case_id']];inp={'utterance':c['utterance'].strip(),'editable_text':c['target'],'screen_context':c['context']}
        assert r['model_input']==inp and r['input_sha256']==jd(inp) and r['output_sha256']==digest(r['final_text'])
        call,=r['calls'];payload=call['request'];assert payload['input'][0]['content'][0]['text']==m['variants'][r['variant']]['prompt']
        assert json.loads(payload['input'][1]['content'][0]['text'])==inp
        params.append({k:v for k,v in payload.items() if k!='input'})
        if c['cohort']=='guard':rv={'status':'observation','note':'历史防误触发样例与已确认编辑意图的模式不同，独立展示，不计主分母。','issues':[],'output_sha256':r['output_sha256']}
        elif r.get('error'):rv={'status':'pending','note':'请求异常。','issues':[],'output_sha256':r['output_sha256']}
        else:
            key=plan['bindings'][r['id']];rv=dict(overrides.get(key,reviews[key]));assert rv['output_sha256']==r['output_sha256']
            assert rv['status'] in ('pass','fail','uncertain') and rv['note']
            if rv['status']=='pass':assert not rv['issues'], 'Contradictory review requires explicit audit'
            if rv['status']=='fail':assert rv['issues'] and all(all(i.get(k) for k in ('reason','expected','actual')) for i in rv['issues'])
        r['review']=rv;r['case']=c;r['round']=name
    assert all(p==params[0] for p in params)
    return m,rows,params[0],len(calls)

def build(names):
    normalized=[];stats=[];tables=[];groups={};links=[{'label':'冻结的完整数据集','href':'dataset.json'},{'label':'逐轮改动记录','href':'iterations.json'},{'label':'运行前冻结计划（保留当时暂定分数）','href':'selection.json'}]
    all_rows=[];metadata={};category_rows=[];capability_rows=[];changes={'control':'同期旧版对照','improved':'旧版失败 → 新版通过','regressed':'旧版通过 → 新版失败','both_pass':'两版通过','both_fail':'两版失败','unresolved':'待确认或观察'}
    capability_labels={'modify':'定向修改','insert':'插入','polish':'润色','format':'结构与格式','translate':'翻译','compose':'创作'}
    for name in names:
        m,rows,params,judge_calls=load_run(name);all_rows+=rows;baseline=next(iter(m['variants']))
        by_key={(r['case_id'],r['repeat'],r['variant']):r for r in rows}
        metadata[name]={'wire_parameters':params,'review_calls':judge_calls,'manifest':'%s/manifest.json'%name,'judge':'同一模型另行盲评，未提供提示词版本；Codex 复核争议。非独立人工。',
          'unique_cases':len(m['cases']),'repeats':max(r['repeat'] for r in rows),'generation_errors':sum(bool(r.get('error')) for r in rows)}
        links.extend([{'label':name+' 全部原始请求/返回','href':name+'/generations.jsonl'},{'label':name+' 盲评记录','href':name+'/reviews.jsonl'},{'label':name+' 冻结条件','href':name+'/manifest.json'}])
        if (OUT/name/'audit-overrides.json').exists():links.append({'label':name+' 复核与原判对照','href':name+'/audit-overrides.json'})
        for variant in m['variants']:
            group=name+':'+variant;groups[group]=name+' · '+variant+(' 旧版完整提示词' if variant=='v3' else ' 释字与偏旁合并改写')
            subset=[r for r in rows if r['variant']==variant and r['case']['cohort']!='guard']
            base=[by_key[(r['case_id'],r['repeat'],baseline)] for r in subset]
            improved=sum(r['review']['status']=='pass' and b['review']['status']=='fail' for r,b in zip(subset,base))
            regressed=sum(r['review']['status']=='fail' and b['review']['status']=='pass' for r,b in zip(subset,base))
            s={'round':name,'variant':variant,'n':len(subset),'pass':sum(r['review']['status']=='pass' and not r.get('error') for r in subset),'fail':sum(r['review']['status']=='fail' for r in subset),
               'uncertain':sum(r['review']['status']=='uncertain' for r in subset),'improved':improved,'regressed':regressed,
               'p50_s':renderer.percentile([r['latency_s'] for r in subset],.5),'p95_s':renderer.percentile([r['latency_s'] for r in subset],.95),
               'paired_median_s':statistics.median(r['latency_s']-b['latency_s'] for r,b in zip(subset,base))}
            stats.append(s)
            tables.append([name,variant,f'{s["pass"]}/{s["n"]}',f'{100*s["pass"]/s["n"]:.1f}%',s['improved'],s['regressed'],f'{s["p50_s"]:.3f}',f'{s["p95_s"]:.3f}'])
            for split in dict.fromkeys(r['case']['split'] for r in subset):
                group_rows=[r for r in subset if r['case']['split']==split]
                category_rows.append([name,variant,split,len(group_rows),sum(r['review']['status']=='pass' for r in group_rows),sum(r['review']['status']=='fail' for r in group_rows),sum(r['review']['status']=='uncertain' for r in group_rows)])
            for task,label in capability_labels.items():
                task_rows=[r for r in subset if task in r['case'].get('expected_tasks',[])]
                if task_rows:capability_rows.append([name,variant,label,len(task_rows),sum(r['review']['status']=='pass' for r in task_rows),sum(r['review']['status']=='fail' for r in task_rows)])
        for r in sorted(rows,key=lambda r:(r['case_id'],r['repeat'],r['variant'])):
            c=r['case'];b=by_key[(r['case_id'],r['repeat'],baseline)];old=b['review']['status'];new=r['review']['status']
            change='control' if r['variant']==baseline else 'improved' if old=='fail' and new=='pass' else 'regressed' if old=='pass' and new=='fail' else 'both_pass' if old==new=='pass' else 'both_fail' if old==new=='fail' else 'unresolved'
            expected=c.get('review_points','完成全部有效要求，没有明显错误。')
            if c.get('reference'):expected+='\n可接受的参考正文（并非唯一答案）：'+c['reference']
            else:expected+='\n辅助约束：'+json.dumps(c['generation_expected'],ensure_ascii=False)
            normalized.append({'id':name+'__'+r['id'],'group':name+':'+r['variant'],'split':c['split'],'mechanism':c['mechanism'],'change':change,
                'features':[c['split'],c['mechanism'],c['scene'],changes[change],f'重复 {r["repeat"]}',*[capability_labels[t] for t in c.get('expected_tasks',[]) if t in capability_labels]],
                'instruction':c['utterance'],'source_text':c['target'],'context':c['context'],'expected_result':expected,
                'output':r['final_text'],'review':r['review'],'error':r.get('error',''),'latency_s':r['latency_s'],'observation':c['cohort']=='guard','checks':r.get('checks',[]),
                'fields':[{'label':'同期对照 '+baseline,'value':b['final_text']},{'label':'对照判定','value':old+'：'+b['review']['note']},{'label':'配对变化','value':changes[change]}],
                'evidence':{'input_sha256':r['input_sha256'],'output_sha256':r['output_sha256'],'raw_response':r['raw_response'],'response':r['calls'][0].get('response',{}),
                'actual_request_parameters':params,'system_prompt_sha256':digest(m['variants'][r['variant']]['prompt']),'full_request':name+'/generations.jsonl 中 '+r['id']}})
    links.extend({'label':p.stem+' 提示词','href':p.name} for p in sorted(OUT.glob('v*.txt')))
    links.extend({'label':p.stem+' 差异','href':p.name} for p in sorted(OUT.glob('*.diff')))
    links.append({'label':'保留 general 命令及其他规则的核对','href':'scope-verification.json'})
    if (OUT/'dataset-errata.json').exists():links.append({'label':'预留集两条参考冲突说明','href':'dataset-errata.json'})
    links.append({'label':'此前的精细条目缺失消融报告','href':'../general-edit-ablation-2026-11-16/report.html'})
    for filename,label in [('production-change.json','源码更新与已测提示词一致性'),('verification.json','源码与数据验证记录'),('decision.json','最终结论与剩余问题')]:
        if (OUT/filename).exists():links.append({'label':label,'href':filename})
    consistent={}
    for r in all_rows:
        if r['review']['status']=='observation':continue
        key=(r['input_sha256'],r['output_sha256']);status=r['review']['status']
        assert key not in consistent or consistent[key]==status, f'Identical input/output requires consistent adjudication: {r["round"]} {r["id"]}'
        consistent[key]=status
    metadata['cross_round_review_consistency']={'unique_input_output_pairs':len(consistent),'status_conflicts':0}
    iterations=json.loads((OUT/'iterations.json').read_text())['iterations']
    final_tables=[{'title':'提示词逐轮修改了什么','description':'每个候选都与同一完整旧版 v3 同期配对。general 编辑命令、其他精细能力条目和请求参数保持不变；改变的是释字与偏旁部首规则的组织和内容。','columns':['候选','修改内容','对应轮次'],'rows':[[i['id'],i['changes'],i['result']] for i in iterations]}];final_summary=[]
    if 'confirmation' in names:
        final=[r for r in all_rows if r['round']=='confirmation' and r['case']['cohort']!='guard']
        paired={(r['case_id'],r['repeat'],r['variant']):r for r in final};summary=[];stability=[];details=[]
        for split in ('development','holdout','regression'):
            old=[r for r in final if r['variant']=='v3' and r['case']['split']==split]
            new=[paired[(r['case_id'],r['repeat'],'v4d')] for r in old]
            counts=[sum(r['review']['status']=='pass' for r in arm) for arm in (old,new)]
            by_case={cid:[] for cid in dict.fromkeys(r['case_id'] for r in old)}
            for a,b in zip(old,new):by_case[a['case_id']].append(int(b['review']['status']=='pass')-int(a['review']['status']=='pass'))
            diffs=[statistics.mean(v) for v in by_case.values()];rng=random.Random(20261116)
            boots=sorted(statistics.mean(rng.choices(diffs,k=len(diffs))) for _ in range(5000))
            ci=[100*renderer.percentile(boots,p) for p in (.025,.975)]
            uncertain=[sum(r['review']['status']=='uncertain' for r in arm) for arm in (old,new)]
            summary.append([split,len(by_case),len(old),f'{counts[0]}/{len(old)} ({100*counts[0]/len(old):.1f}%)',f'{counts[1]}/{len(new)} ({100*counts[1]/len(new):.1f}%)',f'{uncertain[0]}/{uncertain[1]}',f'{100*(counts[1]-counts[0])/len(old):+.1f}',f'{ci[0]:+.1f} ～ {ci[1]:+.1f}'])
            final_summary.append(f'{split}：旧版 {counts[0]}/{len(old)}，新版 {counts[1]}/{len(new)}。')
            for cid in by_case:
                paircounts=[sum(r['review']['status']=='pass' for r in arm if r['case_id']==cid) for arm in (old,new)]
                case=next(r['case'] for r in old if r['case_id']==cid)
                stability.append({'case_id':cid,'split':split,'v3_pass':paircounts[0],'v4d_pass':paircounts[1],'repeats':len(by_case[cid]),'delta':paircounts[1]-paircounts[0]})
                if paircounts[1]<paircounts[0]:details.append([cid,case['utterance'],f'{paircounts[0]}/3',f'{paircounts[1]}/3'])
        final_tables.extend([{'title':'冻结版本的全量三次重复验收','description':'同一条题重复三次，不冒充三条独立题。通过率分母含待确认项，但不将待确认计为失败；两条综合题真值缺失、两条预留题释字顺序冲突，两版统一处理。区间以样例为单位做 5000 次配对 bootstrap；区间跨 0 不能证明两版等效或绝无退化。','columns':['集合','独立题数','每版请求','旧版通过','新版通过','待确认旧/新','差值百分点','差值95%区间'],'rows':summary},
          {'title':'重复验收中新版通过次数较少的题','description':'所有退化项完整列出，可在下方搜索题号查看三次输出。该清单不只展示改善案例。','columns':['题号','口述','旧版通过','新版通过'],'rows':details}])
        save(OUT/'stability.json',stability);links.append({'label':'每题三次重复的稳定性统计','href':'stability.json'})
        final_summary.append('结论：释字通过率提高；综合回归旧版590/630、新版589/630，整体接近但存在具体退化，不能宣称零退化。所有三次重复中通过次数减少的题已列出。')
        final_summary.append('按截图口述重建的样例旧版0/3、新版3/3；原电脑完整记录不可用，本地原文由现有截图重建。该句进入提示词示例，仅证明已知案例修复。最终 P50 为旧版0.796秒、新版0.840秒；输入 tokens 均值增加约421，当前样本没有显示延迟优势。')
        tokens=[]
        for v in ('v3','v4d'):
            arm=[r for r in final if r['variant']==v];usages=[r['calls'][0]['response'].get('usage',{}) for r in arm]
            total=sum(u.get('input_tokens',0) for u in usages);cached=sum(u.get('input_tokens_details',{}).get('cached_tokens',0) for u in usages)
            tokens.append([v,f'{statistics.mean(u.get("input_tokens",0) for u in usages):.1f}',f'{statistics.mean(u.get("output_tokens",0) for u in usages):.1f}',f'{100*cached/total:.1f}%' if total else '未提供'])
        final_tables.append({'title':'最终验收的 token 与缓存观测','description':'服务端回报的用量；共享服务负载与缓存未控制，因此不能用小幅耗时差异证明提示词本身更快。','columns':['版本','平均输入 tokens','平均输出 tokens','输入缓存占比'],'rows':tokens})
    dataset=json.loads((OUT/'dataset.json').read_text())
    data={'schema_version':1,'evaluation':{'classification':False},'title':'释字、偏旁部首合并规则：多轮综合编辑评测','groups':groups,
      'summary':[f'冻结 {len(dataset["cases"])} 条样例：84 条释字开发、46 条合成预留、216 条综合回归。开发轮一起测试释字与其他编辑功能。',
      '选定 v4d：把释字与偏旁部首统一理解为解释正确写法，按定位、完整解字、原位替换、逐组核对完成纠正。general 编辑要求和其他能力段落保留。',
      *final_summary,
      '每轮新旧提示词使用相同输入与线上参数，按样例配对、随机版本先后、4 个并行 block；每条只请求一次完整正文。没有中间释字预处理或失败自动重试。',
      '通过表示完成有效要求且未见明显错误，不要求逐字命中参考答案；自然标点、合理改写和补充不单独判失败。机械差异仅供排查。',
      '开发轮用于调参，不当作独立泛化证据；预留集在候选确定后运行，没有据此修改提示词。已知截图释字句式进入了示例，截图题仅作回归证据。延迟包含网络与正文处理，不含 ASR/OCR/写回，服务端缓存未控制。各轮请求与判定全部保留。'],
      'methodology':'统一 correctness-v1 宽松口径；单独的模型盲评请求未收到版本和提示词，输入/输出哈希绑定。Codex 参与设计并复核失败与退化项，非独立人工验收。6 条历史 guard 每轮独立观察。',
      'links':links,'metadata':metadata,'cases':normalized,
      'analysis_tables':[*final_tables,{'title':'每轮同期对照','description':'改善/退化按同例同重复的语义判定比较；跨轮输出不合并冒充独立样例。','columns':['轮次','版本','通过','通过率','改善','退化','P50 秒','P95 秒'],'rows':tables},
        {'title':'释字与其他功能分开检查','description':'regression 是包含润色、插入、翻译、创作、结构及组合要求的既有综合集。','columns':['轮次','版本','集合','数量','通过','失败','待确认'],'rows':category_rows},
        {'title':'综合回归各项能力','description':'按冻结数据集的人工任务标注分层，不是模型输出的分类。同一组合样例可能出现在多行；每行检查整条委托是否全部满足。','columns':['轮次','版本','包含能力','数量','通过','失败'],'rows':capability_rows}]}
    rendered,metrics=renderer.render(data)
    options=''
    for key,label in [('split','全部数据集'),('mechanism','全部释字方式'),('change','全部配对变化')]:
        values=list(dict.fromkeys(c[key] for c in normalized));options+='<select id="'+key+'"><option value="all">'+label+'</option>'+''.join('<option value="'+escape(v,quote=True)+'">'+escape(changes.get(v,v))+'</option>' for v in values)+'</select>'
    rendered=rendered.replace('<input id="search"',options+'<input id="search"')
    iterator=iter(normalized)
    def attrs(match):
        c=next(iterator);return match.group(0)[:-1]+''.join(' data-'+k+'="'+escape(c[k],quote=True)+'"' for k in ('split','mechanism','change'))+'>'
    rendered=re.sub(r'<article class="case"[^>]*>',attrs,rendered)
    rendered=re.sub(r'<script>[\s\S]*?</script>','<script>'+FILTER_JS+'</script>',rendered)
    (OUT/'report.html').write_text(rendered);save(OUT/'report.json',data);save(OUT/'report.metrics.json',metrics);save(OUT/'comparison.json',stats)
    print(json.dumps({'requests':len(normalized),'stats':stats},ensure_ascii=False,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('rounds',nargs='+');build(p.parse_args().rounds)
