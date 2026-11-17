"""Offline snapshot of saved requests, without treating unrun jobs as failures."""
from collections import Counter
from html import escape
import json
from pathlib import Path
import re
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'src')]
from tools.build_astra_v4d_report import OUT, LABELS, CHANGES, PHASES, FILTER_JS, usage, stats, paired_change, renderer
from tools.character_clarification_experiment import digest, jd, save


def build():
    m = json.loads((OUT/'manifest.json').read_text())
    cases = {c['id']:c for c in m['cases']}
    audit = json.loads((OUT/'audit-candidates.json').read_text())
    manual = json.loads((OUT/'codex-new-output-reviews.json').read_text())
    assert audit['generations_sha256'] == digest((OUT/'generations.jsonl').read_text())
    reviews = {}
    for c in audit['candidates']:
        rv = dict(manual.get(c['candidate_id']) or c.get('historical_review') or {})
        assert rv and rv['output_sha256'] == c['output_sha256']
        assert rv.get('input_sha256',c['input_sha256']) == c['input_sha256']
        rv['input_sha256'] = c['input_sha256']
        rv['method'] = ('Codex 本次逐例复核；参与实验，非独立人工' if c['candidate_id'] in manual
                        else '输入与输出哈希均一致，沿用历史逐例复核；不是按题号继承')
        for rid in c['records']:
            reviews[rid] = rv
    rows = [json.loads(s) for s in (OUT/'generations.jsonl').read_text().splitlines()]
    assert len({r['id'] for r in rows}) == len(rows)
    expected = {f'{b["case_id"]}__r{b["repeat"]}__{v}' for b in m['blocks'] for v in b['variants']}
    assert {r['id'] for r in rows} <= expected
    for r in rows:
        c = cases[r['case_id']]
        inp = {'utterance':c['utterance'].strip(),'editable_text':c['target'],'screen_context':c['context']}
        assert r['model_input'] == inp and r['input_sha256'] == jd(inp)
        assert r['output_sha256'] == digest(r['final_text'])
        if r['variant']=='deepseek':
            assert r['calls'][0]['request']['input'][0]['content'][0]['text'] == m['variants']['deepseek']['prompt']
        else:
            assert r['session_request']['baseInstructions'] == m['variants']['astra_low']['prompt']
            assert r['session_response']['model']=='gpt-6-astra'
            assert r['session_response']['reasoningEffort']==r['turn_request']['effort']=='low'
            assert not r['session_response']['instructionSources'] and not r['tool_items']
        if c['cohort']=='guard':
            rv = {'status':'observation','note':'历史防误触发口径与确认编辑不同，独立观察、不计准确率。','issues':[]}
        elif r.get('error'):
            rv = {'status':'pending','note':'请求异常，未取得有效正文。','issues':[]}
        elif c['id'] in m['uncertain_case_ids']:
            rv = {'status':'uncertain','note':'调用前冻结的真值争议，两模型全部重复统一待确认。','issues':[],
                  'original_review':reviews[r['id']]}
        else:
            rv = dict(reviews[r['id']])
        rv.update(input_sha256=r['input_sha256'],output_sha256=r['output_sha256'])
        r.update(case=c,review=rv)
    paired = {(r['case_id'],r['repeat'],r['variant']):r for r in rows}
    first = [r for r in rows if r['repeat']==1]
    assert len(first)==len(cases)*2
    first_stats = {v:stats([r for r in first if r['variant']==v]) for v in LABELS}
    first_pairs = Counter(paired_change(paired[(c['id'],1,'deepseek')],paired[(c['id'],1,'astra_low')])
                          for c in m['cases'] if c['cohort']!='guard')
    summary_table = [[LABELS[v],f'{s["pass"]}/{s["n"]}',f'{s["pass"]/s["n"]:.2%}',s['fail'],s['uncertain'],
                      f'{s["p50_s"]:.3f}',f'{s["p95_s"]:.3f}'] for v,s in first_stats.items()]
    repeat_stats = {str(k):{v:stats([r for r in rows if r['repeat']==k and r['variant']==v]) for v in LABELS}
                    for k in sorted({r['repeat'] for r in rows})}
    progress = [[k,LABELS[v],sum(r['repeat']==int(k) and r['variant']==v for r in rows),
                 f'{s["pass"]}/{s["n"]}',s['fail'],s['uncertain']] for k,arms in repeat_stats.items() for v,s in arms.items()]
    split_table = []
    for split in ['development','holdout','regression']:
        for v in LABELS:
            s = stats([r for r in first if r['variant']==v and r['case']['split']==split])
            split_table.append([split,LABELS[v],f'{s["pass"]}/{s["n"]}',s['fail'],s['uncertain']])
    phase_table = []
    for phase,label in PHASES.items():
        for v in LABELS:
            arm = [r for r in rows if r['variant']==v and r.get('run_phase','original-four-workers')==phase
                   and (other:=paired.get((r['case_id'],r['repeat'],'deepseek' if v=='astra_low' else 'astra_low')))
                   and other.get('run_phase','original-four-workers')==phase]
            s = stats(arm)
            if s['n']:phase_table.append([label,LABELS[v],s['n'],f'{s["p50_s"]:.3f}',f'{s["p95_s"]:.3f}'])
    normalized = []
    for r in sorted(rows,key=lambda r:(r['case_id'],r['repeat'],r['variant'])):
        c = r['case'];v = r['variant'];other = paired.get((c['id'],r['repeat'],'deepseek' if v=='astra_low' else 'astra_low'))
        change = paired_change(r,other) if other and v=='deepseek' else paired_change(other,r) if other else 'unresolved'
        phase = r.get('run_phase','original-four-workers')
        fields = [{'label':'同题同轮另一模型','value':LABELS[other['variant']]+'：'+other['final_text'] if other else '尚未运行／未落盘；不算失败'},
                  {'label':'另一模型判定','value':other['review']['status']+'：'+other['review']['note'] if other else '暂无'},
                  {'label':'配对差异','value':CHANGES[change]}, {'label':'资源阶段','value':PHASES[phase]},
                  {'label':'复核方式','value':r['review'].get('method','冻结争议／观察规则')},
                  {'label':'输入、输出 tokens','value':json.dumps(usage(r),ensure_ascii=False)}]
        if r.get('peak_evaluation_rss_mb') is not None:
            fields.append({'label':'本请求评测进程峰值 RSS','value':f'{r["peak_evaluation_rss_mb"]:.1f} MiB'})
        normalized.append({'id':r['id'],'group':v,'split':c['split'],'mechanism':c['mechanism'],
            'change':change,'phase':phase,'repeat':str(r['repeat']),
            'features':['第 '+str(r['repeat'])+' 轮',c['split'],c['mechanism'],CHANGES[change]],
            'instruction':c['utterance'],'source_text':c['target'],'context':c['context'],
            'expected_result':c.get('review_points','完成有效要求且无明显错误；不要求唯一表达。')+'\n参考（不是唯一答案）：'+c.get('reference',json.dumps(c['generation_expected'],ensure_ascii=False)),
            'output':r['final_text'],'review':r['review'],'error':r.get('error',''),
            'latency_s':r['latency_s'],'observation':c['cohort']=='guard','checks':r.get('checks',[]),'fields':fields,
            'evidence':{'raw_record':'generations.jsonl: '+r['id'],'input_sha256':r['input_sha256'],
                        'output_sha256':r['output_sha256'],'prompt_sha256':m['prompt_sha256']}})
    peaks = [r['peak_evaluation_rss_mb'] for r in rows if r.get('peak_evaluation_rss_mb') is not None]
    token_table = []
    for v in LABELS:
        us = [usage(r) for r in first if r['variant']==v]
        token_table.append([LABELS[v],*[f'{statistics.mean(u.get(k,0) for u in us):.1f}' for k in ['input','output','reasoning']],
                            f'{sum(u.get("cached",0) for u in us)/sum(u.get("input",0) for u in us):.1%}'])
    a,b = first_stats['deepseek'],first_stats['astra_low']
    data = {'schema_version':1,'evaluation':{'classification':False},'groups':LABELS,
        'title':'当前阶段报告：v4d · Astra low 与 DeepSeek（带运行时上下文）',
        'summary':[
            f'当前已保存 {len(rows)} 次请求，覆盖原有 346 道题；第一轮两个模型均完整运行，第二轮尚未完整，第三轮未开始。原计划 346 × 2 模型 × 3 次＝2076 次请求；这不是 2076 道不同题。',
            f'主要结果只看完整第一轮：Astra {b["pass"]}/{b["n"]} ({b["pass"]/b["n"]:.2%})，DeepSeek {a["pass"]}/{a["n"]} ({a["pass"]/a["n"]:.2%})。Astra 明确失败 {b["fail"]} 条，DeepSeek {a["fail"]} 条；各有 4 条冻结争议题。',
            f'同题配对：仅 Astra 通过 {first_pairs["astra_only"]} 条，仅 DeepSeek 通过 {first_pairs["deepseek_only"]} 条。第一轮 Astra 完整结果 P50 {b["p50_s"]:.2f} 秒、DeepSeek {a["p50_s"]:.2f} 秒。本次方案显示更好的指令执行，但明显更慢，未达到与 DeepSeek 延迟匹配的目标。',
            '关键限制：每道题都是独立新会话，没有前题历史；但 Astra 的本机 app-server 会附带代理运行时上下文。它不等于只含 v4d 和当前题的纯 API 请求。用户要求去掉这部分上下文后，当前批量已停止；本报告只作阶段证据，不能唯一归因于模型本身。',
            '题目组成：84 道释字开发题＋46 道释字合成预留题＋216 道综合回归题（126 基础、12 歧义、48 组合、24 新构造、6 防误触发观察）。这些题之前已经使用，不是新独立未见测试。每轮准确率分母是 340；6 道观察题另列，4 道争议题保留在分母但不算通过或失败。',
            f'内存保护：944 条之后恢复为单并发，每条 Astra 完成即退出进程。已记录的续跑评测进程峰值 {max(peaks):.1f} MiB，上限 768 MiB，每 0.5 秒采样；中断前峰值未知。不同并发阶段的延迟分开列出。',
            '当前复核为 Codex 逐例复核：293 个新输入输出组合逐条查看，329 个与历史输入及输出哈希完全相同的组合沿用已复核判定。新的独立模型盲评尚未运行，因此属于阶段结论。按没有明显错误的宽松口径，合理润色、礼貌问句、同义表达不判错。'],
        'methodology':'提示词与输入逐字核对；Astra gpt-6-astra / low，火山 deepseek-v4-1-flash-260910 / thinking disabled。未改提示词、未修复输出、未按答题好坏重试。未落盘中断请求不计入结果，未运行请求不计失败。首轮为4并发，恢复后单并发；报告全部输出可按模型、轮次、题型、结果及配对差异筛选。',
        'metadata':{'provisional':True,'source_sha256':audit['generations_sha256'],'saved_requests':len(rows),
                    'planned_requests':m['planned_requests'],'first_round_complete':True,'new_blind_review_performed':False,
                    'reviewed_new_unique':len(manual),'peak_resume_rss_mib':max(peaks),
                    'prompt_sha256':m['prompt_sha256'],'uncertain_cases':m['uncertain_case_ids'],
                    'actual_models':m['variants'],'limitations':m['limitations']},
        'links':[{'label':label,'href':name} for name,label in [('current-report.json','全部阶段结果 JSON'),
            ('current-comparison.json','阶段指标'),('dataset.json','346 道固定题'),('v4d.txt','两模型共用 v4d'),
            ('generations.jsonl','全部原始请求与输出'),('codex-new-output-reviews.json','新输出逐例复核'),
            ('audit-candidates.json','输入输出与历史判定绑定'),('manifest.json','原冻结计划'),
            ('execution-notes.json','中断与停止说明')]],
        'analysis_tables':[
            {'title':'完整第一轮：主要结果','description':'两个模型各 346 道，去掉 6 道观察题后各 340 条。完整结果耗时含网络及会话准备，不含进程启动、ASR、OCR 或写回。','columns':['模型','通过','通过率','明确失败','待确认','P50 秒','P95 秒'],'rows':summary_table},
            {'title':'每一轮的已完成部分','description':'第二轮是部分题，不能拿它的通过率与完整第一轮作直接比较。未运行题不在分母中。','columns':['轮次','模型','已运行／346','通过／已计分','失败','待确认'],'rows':progress},
            {'title':'第一轮的释字与综合回归','description':'同一固定题集，两模型配对比较。','columns':['集合','模型','通过','失败','待确认'],'rows':split_table},
            {'title':'第一轮全部配对结果','description':'每个配对为同题两个模型；待确认题独立列出。','columns':['结果','题数'],'rows':[[CHANGES[k],first_pairs[k]] for k in CHANGES]},
            {'title':'相同资源阶段内的延迟','description':'包括已完成的后续题，只纳入两边均在同阶段完成的配对；各阶段题目组成不同，不跨阶段声称因果。','columns':['阶段','模型','配对请求数','P50 秒','P95 秒'],'rows':phase_table},
            {'title':'第一轮 token 与缓存实测','description':'本机 Astra 含代理运行时上下文，两家分词器不同。这是本次不能作为纯模型对照的关键限制。','columns':['模型','平均输入','平均输出','平均推理 tokens','输入缓存占比'],'rows':token_table}],
        'cases':normalized}
    html,metrics = renderer.render(data)
    # Do not headline pooled accuracy or latency across two different models.
    html = re.sub(r'<div class="kpis">[\s\S]*?(?=<div class="panel"><h3>样例分布与表现)', '', html)
    html = html.replace('样例分布与表现','全部已保存请求的汇总（混合轮次，仅作进度参考）')
    options = ''
    keys = ['split','mechanism','change','phase','repeat']
    for key,label in zip(keys,['全部数据集','全部题型','全部配对差异','全部资源阶段','全部轮次']):
        values = list(dict.fromkeys(c[key] for c in normalized))
        options += '<select id="'+key+'"><option value="all">'+label+'</option>'+''.join(
            '<option value="'+escape(v,quote=True)+'">'+escape('第 '+v+' 轮' if key=='repeat' else PHASES.get(v,CHANGES.get(v,v)))+'</option>' for v in values)+'</select>'
    html = html.replace('<input id="search"',options+'<input id="search"')
    iterator = iter(normalized)
    def attrs(match):
        c = next(iterator)
        return match.group(0)[:-1]+''.join(' data-'+k+'="'+escape(c[k],quote=True)+'"' for k in keys)+'>'
    html = re.sub(r'<article class="case"[^>]*>',attrs,html)
    html = re.sub(r'<script>[\s\S]*?</script>','<script>'+FILTER_JS+'</script>',html)
    (OUT/'current-report.html').write_text(html)
    save(OUT/'current-report.json',data)
    save(OUT/'current-report.metrics.json',metrics)
    comparison = {'first_round':first_stats,'first_round_pairs':dict(first_pairs),'repeats':repeat_stats,
                  'saved_requests':len(rows),'planned_requests':m['planned_requests'],'provisional':True}
    save(OUT/'current-comparison.json',comparison)
    print(json.dumps(comparison,ensure_ascii=False,indent=2))


if __name__=='__main__':
    build()
