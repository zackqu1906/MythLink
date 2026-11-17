"""Offline, hash-verified paired model comparison with all individual outputs."""
from __future__ import annotations
from collections import Counter
from html import escape
import json
from pathlib import Path
import random
import re
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'src')]
from tools.compare_astra_v4d import OUT, SOURCE, save
from tools.character_clarification_experiment import digest, jd
from tools.build_character_clarification_report import renderer, load_reviews, FILTER_JS

LABELS = {'deepseek': '火山 DeepSeek · 关闭思考', 'astra_low': '本机 ChatGPT · Astra low'}
CHANGES = {'astra_only': '仅 Astra 通过', 'deepseek_only': '仅 DeepSeek 通过',
           'both_pass': '两者通过', 'both_fail': '两者失败', 'unresolved': '待确认／请求异常／观察'}
TASKS = {'modify':'定向修改', 'insert':'插入', 'polish':'润色',
         'format':'结构与格式', 'translate':'翻译', 'compose':'创作'}
PHASES = {'original-four-workers':'中断前 · 4 并发',
          'memory-bounded-resume':'恢复后 · 单并发／每条退出进程'}
FILTER_JS = FILTER_JS.replace("'change','search'", "'change','phase','repeat','search'").replace(
    'change,q)', 'change,phase,repeat,q)').replace(
    "&&(change==='all'||d.change===change)",
    "&&(change==='all'||d.change===change)&&(phase==='all'||d.phase===phase)&&(repeat==='all'||d.repeat===repeat)").replace('v[5]', 'v[7]')


def load():
    m = json.loads((OUT/'manifest.json').read_text())
    plan = json.loads((OUT/'review-plan.json').read_text())
    assert plan['source_sha256'] == digest((OUT/'generations.jsonl').read_text())
    assert plan['manifest_sha256'] == digest((OUT/'manifest.json').read_text())
    reviews, calls = load_reviews(OUT, plan)
    overrides = {}
    if (OUT/'audit-overrides.json').exists():
        audit = json.loads((OUT/'audit-overrides.json').read_text())
        assert audit['source_sha256'] == plan['source_sha256']
        overrides = audit['reviews']
    rows = [json.loads(s) for s in (OUT/'generations.jsonl').read_text().splitlines()]
    expected = {f'{b["case_id"]}__r{b["repeat"]}__{v}' for b in m['blocks'] for v in b['variants']}
    assert len(rows) == len(expected) == m['planned_requests']
    assert {r['id'] for r in rows} == expected
    cases = {c['id']:c for c in m['cases']}
    for r in rows:
        c = cases[r['case_id']]
        inp = {'utterance':c['utterance'].strip(), 'editable_text':c['target'], 'screen_context':c['context']}
        assert r['model_input'] == inp and r['input_sha256'] == jd(inp)
        assert r['output_sha256'] == digest(r['final_text'])
        if r['variant'] == 'deepseek':
            call, = r['calls']
            payload = call['request']
            assert payload['input'][0]['content'][0]['text'] == m['variants']['deepseek']['prompt']
            assert json.loads(payload['input'][1]['content'][0]['text']) == inp
        elif not r.get('error'):
            assert r['session_request']['baseInstructions'] == m['variants']['astra_low']['prompt']
            assert r['session_response']['model'] == 'gpt-6-astra'
            assert r['session_response']['reasoningEffort'] == r['turn_request']['effort'] == 'low'
            assert not r['session_response']['instructionSources'] and not r['tool_items']
            assert json.loads(r['turn_request']['input'][0]['text']) == inp
        if c['cohort'] == 'guard':
            rv = {'status':'observation','note':'历史防误触发题与确认编辑模式的口径不同，独立展示。','issues':[]}
        elif r.get('error'):
            rv = {'status':'pending','note':'请求异常，无可复核的有效正文。','issues':[]}
        else:
            key = plan['bindings'][r['id']]
            rv = dict(overrides.get(key, reviews[key]))
            assert rv['output_sha256'] == r['output_sha256']
            if key in overrides:
                assert rv['input_sha256'] == r['input_sha256']
            if c['id'] in m['uncertain_case_ids']:
                rv = {'status':'uncertain','note':'沿用调用前冻结的真值争议：指称或参考存在歧义，两模型全部重复统一待确认。',
                      'issues':[], 'original_review':rv}
        rv['output_sha256'] = r['output_sha256']
        if rv['status'] == 'fail':
            assert rv.get('issues')
        if rv['status'] == 'pass':
            assert not rv.get('issues')
        r['review'] = rv
        r['case'] = c
    return m, rows, len(calls)


def usage(r):
    if r['variant'] == 'deepseek':
        u = r['calls'][0].get('response',{}).get('usage',{})
        return {'input':u.get('input_tokens',0), 'cached':u.get('input_tokens_details',{}).get('cached_tokens',0),
                'output':u.get('output_tokens',0), 'reasoning':u.get('output_tokens_details',{}).get('reasoning_tokens',0)}
    events = [e for e in r.get('events',[]) if e.get('method') == 'thread/tokenUsage/updated']
    if not events:
        return {}
    u = events[-1]['params']['tokenUsage']['total']
    return {'input':u.get('inputTokens',0), 'cached':u.get('cachedInputTokens',0),
            'output':u.get('outputTokens',0), 'reasoning':u.get('reasoningOutputTokens',0)}


def stats(rows):
    rows = [r for r in rows if r['case']['cohort'] != 'guard']
    counts = Counter(r['review']['status'] for r in rows)
    times = [r['latency_s'] for r in rows if not r.get('error')]
    return {'n':len(rows), **{s:counts[s] for s in ['pass','fail','uncertain','pending']},
            'errors':sum(bool(r.get('error')) for r in rows),
            'p50_s':renderer.percentile(times,.5), 'p95_s':renderer.percentile(times,.95),
            'mean_s':statistics.mean(times) if times else None}


def paired_change(a, b):
    sa, sb = a['review']['status'], b['review']['status']
    if a.get('error') or b.get('error'):
        return 'unresolved'
    if sa == sb == 'pass':
        return 'both_pass'
    if sa == sb == 'fail':
        return 'both_fail'
    if sa == 'fail' and sb == 'pass':
        return 'astra_only'
    if sa == 'pass' and sb == 'fail':
        return 'deepseek_only'
    return 'unresolved'


def build():
    m, rows, judge_calls = load()
    paired = {(r['case_id'],r['repeat'],r['variant']):r for r in rows}
    all_stats = {v:stats([r for r in rows if r['variant']==v]) for v in LABELS}
    repeat_stats = {str(k):{v:stats([r for r in rows if r['variant']==v and r['repeat']==k])
                           for v in LABELS} for k in range(1,m['repeats']+1)}
    phase_stats = {}
    phase_table = []
    for phase,label in PHASES.items():
        phase_stats[phase] = {}
        for v in LABELS:
            arm = [r for r in rows if r['variant']==v and r.get('run_phase','original-four-workers')==phase
                   and paired[(r['case_id'],r['repeat'],'astra_low' if v=='deepseek' else 'deepseek')].get('run_phase','original-four-workers')==phase]
            s = stats(arm)
            phase_stats[phase][v] = s
            if s['n']:
                phase_table.append([label,LABELS[v],s['n'],f'{s["p50_s"]:.3f}',f'{s["p95_s"]:.3f}'])
    summary_rows = []
    for v,s in all_stats.items():
        summary_rows.append([LABELS[v], f'{s["pass"]}/{s["n"]}', f'{s["pass"]/s["n"]:.1%}',
            s['fail'],s['uncertain'],s['errors'],f'{s["p50_s"]:.3f}',f'{s["p95_s"]:.3f}'])
    split_rows = []
    intervals = {}
    for split in ['development','holdout','regression']:
        sub = [r for r in rows if r['case']['split']==split and r['case']['cohort']!='guard']
        ss = {v:stats([r for r in sub if r['variant']==v]) for v in LABELS}
        case_ids = sorted({r['case_id'] for r in sub})
        deltas = [statistics.mean(int(paired[(cid,k,'astra_low')]['review']['status']=='pass') -
                                  int(paired[(cid,k,'deepseek')]['review']['status']=='pass')
                                  for k in range(1,m['repeats']+1)) for cid in case_ids]
        rng = random.Random(20261116)
        boots = sorted(statistics.mean(rng.choices(deltas,k=len(deltas))) for _ in range(5000))
        ci = [100*renderer.percentile(boots,p) for p in [.025,.975]]
        intervals[split] = ci
        a,b = ss['deepseek'],ss['astra_low']
        split_rows.append([split,len(case_ids),f'{a["pass"]}/{a["n"]} ({a["pass"]/a["n"]:.1%})',
                           f'{b["pass"]}/{b["n"]} ({b["pass"]/b["n"]:.1%})',
                           f'{100*(b["pass"]-a["pass"])/a["n"]:+.1f}',f'{ci[0]:+.1f} ～ {ci[1]:+.1f}'])
    pair_counts = Counter()
    stability = []
    historical_rows = [c for c in json.loads((SOURCE/'report.json').read_text())['cases']
                       if c['group']=='confirmation:v4d']
    previous_failures = {c['id'].split('__')[1] for c in historical_rows if c['review']['status']=='fail'}
    for c in m['cases']:
        entry = {'case_id':c['id'], 'split':c['split'], 'instruction':c['utterance']}
        for v in LABELS:
            entry[v+'_pass'] = sum(paired[(c['id'],k,v)]['review']['status']=='pass' for k in range(1,m['repeats']+1))
        entry['delta'] = entry['astra_low_pass']-entry['deepseek_pass']
        stability.append(entry)
        for k in range(1,m['repeats']+1):
            a,b = paired[(c['id'],k,'deepseek')],paired[(c['id'],k,'astra_low')]
            if c['cohort'] != 'guard':
                pair_counts[paired_change(a,b)] += 1
    normalized = []
    for r in sorted(rows,key=lambda r:(r['case_id'],r['repeat'],r['variant'])):
        c = r['case']
        other = paired[(r['case_id'],r['repeat'],'deepseek' if r['variant']=='astra_low' else 'astra_low')]
        a,b = paired[(r['case_id'],r['repeat'],'deepseek')],paired[(r['case_id'],r['repeat'],'astra_low')]
        change = paired_change(a,b)
        expected = c.get('review_points','完成所有有效要求，无明显改错。')
        expected += '\n参考（不是唯一答案）：'+c.get('reference',json.dumps(c['generation_expected'],ensure_ascii=False))
        fields = [{'label':'同题另一个模型', 'value':LABELS[other['variant']]+'：'+other['final_text']},
                  {'label':'另一模型判定', 'value':other['review']['status']+'：'+other['review']['note']},
                  {'label':'配对差异', 'value':CHANGES[change]},
                  {'label':'重复次数', 'value':str(r['repeat'])}]
        phase = r.get('run_phase','original-four-workers')
        fields.append({'label':'资源运行阶段','value':PHASES[phase]})
        if r.get('peak_evaluation_rss_mb') is not None:
            fields.append({'label':'本请求评测进程树峰值 RSS','value':f'{r["peak_evaluation_rss_mb"]:.1f} MiB'})
        if r.get('client_startup_s') is not None:
            fields.append({'label':'进程启动／包含启动的总耗时','value':f'{r["client_startup_s"]:.3f} / {r["full_route_s"]:.3f} 秒'})
        if r['variant']=='astra_low' and not r.get('error'):
            fields += [{'label':'会话准备', 'value':f'{r["session_setup_s"]:.3f} 秒'},
                       {'label':'提交后完整正文', 'value':f'{r["generation_latency_s"]:.3f} 秒'},
                       {'label':'首段正文', 'value':f'{r["first_text_s"]:.3f} 秒' if r['first_text_s'] is not None else '未记录'}]
        normalized.append({'id':r['id'], 'group':r['variant'], 'split':c['split'],
            'mechanism':c['mechanism'], 'change':change, 'phase':phase, 'repeat':str(r['repeat']),
            'features':[c['split'],c['mechanism'],CHANGES[change],f'重复 {r["repeat"]}',
                        *[TASKS[t] for t in c.get('expected_tasks',[]) if t in TASKS]],
            'instruction':c['utterance'], 'source_text':c['target'], 'context':c['context'],
            'expected_result':expected, 'output':r['final_text'], 'review':r['review'],
            'error':r.get('error',''), 'latency_s':r['latency_s'], 'observation':c['cohort']=='guard',
            'checks':r.get('checks',[]), 'fields':fields,
            'evidence':{'record':'generations.jsonl: '+r['id'], 'input_sha256':r['input_sha256'],
                        'output_sha256':r['output_sha256'], 'prompt_sha256':m['prompt_sha256'],
                        'usage':usage(r)}})
    tokens = []
    timing = []
    capabilities = []
    for v in LABELS:
        sub = [r for r in rows if r['variant']==v and r['case']['cohort']!='guard' and not r.get('error')]
        uu = [usage(r) for r in sub]
        total = sum(u.get('input',0) for u in uu)
        cached = sum(u.get('cached',0) for u in uu)
        tokens.append([LABELS[v],f'{statistics.mean(u.get("input",0) for u in uu):.1f}',
                       f'{statistics.mean(u.get("output",0) for u in uu):.1f}',
                       f'{statistics.mean(u.get("reasoning",0) for u in uu):.1f}',
                       f'{cached/total:.1%}' if total else '未提供'])
        for task,label in TASKS.items():
            s = stats([r for r in sub if task in r['case'].get('expected_tasks',[])])
            if s['n']:
                capabilities.append([LABELS[v],label,f'{s["pass"]}/{s["n"]}',f'{s["pass"]/s["n"]:.1%}'])
    for key,label in [('session_setup_s','Astra 会话准备'),('first_text_s','Astra 提交至首段正文'),
                      ('generation_latency_s','Astra 提交至完整正文')]:
        values = [r[key] for r in rows if r['variant']=='astra_low' and not r.get('error') and r.get(key) is not None and r['case']['cohort']!='guard']
        timing.append([label,len(values),f'{renderer.percentile(values,.5):.3f}',f'{renderer.percentile(values,.95):.3f}'])
    a,b = all_stats['deepseek'],all_stats['astra_low']
    resource_peaks = [r['peak_evaluation_rss_mb'] for r in rows if r.get('peak_evaluation_rss_mb') is not None]
    evidence_notes = []
    notes_path = OUT/'conclusions.json'
    if notes_path.exists():
        evidence_notes = json.loads(notes_path.read_text())['notes']
    data = {'schema_version':1,'evaluation':{'classification':False},
        'title':'同一 v4d 提示词：本机 ChatGPT Astra low 与火山 DeepSeek 对比',
        'groups':LABELS, 'latency_label':'端到端完整结果耗时 P50 / P95',
        'summary':[
            f'固定 346 条题，各模型每题 {m["repeats"]} 次，共 {len(rows)} 次正式请求。84 条释字开发题、46 条原合成预留题、216 条综合回归题；所有题此前已用于评测，不称为新的独立未见测试。',
            f'同一份 v4d 提示词与输入：DeepSeek 通过 {a["pass"]}/{a["n"]} ({a["pass"]/a["n"]:.1%})；Astra low 通过 {b["pass"]}/{b["n"]} ({b["pass"]/b["n"]:.1%})。按同题同次配对，仅 Astra 通过 {pair_counts["astra_only"]} 次，仅 DeepSeek 通过 {pair_counts["deepseek_only"]} 次。',
            '运行过程中用户报告内存压力并中断。944 条已落盘结果保留；恢复后改为单并发，每条 Astra 请求完成即退出整个进程组，监控评测进程树 RSS，上限 768 MiB。提示词、测试输入和模型推理档不变。不同并发阶段的延迟单独展示；跨中断配对不进入分阶段耗时表。',
            '用户确认以 Astra low 代替不存在的 Astra 无思考即时档。Astra 通过本机已登录 ChatGPT 的官方 app-server 调用，DeepSeek 通过现有火山接口。相同的是编辑提示词、数据、次数及评分；推理模式、服务路由、客户端附加上下文、传输方式和 token 上限并不完全相同。',
            '通过表示完成有效要求且无明确错误，不要求逐字匹配参考答案。6 条历史 guard 独立观察；4 条输入真值争议题在两个模型的所有重复中统一 uncertain，仍在主分母但不算通过或失败。',
            *evidence_notes,
            '该实验能判断同一编辑提示词在两个实际服务方案上的表现差异，不能把所有差异唯一归因于模型权重。重复调用不等于新增独立样例；模型盲评后由参与实验的 Codex 复核，不是独立人工验收。'],
        'methodology':'调用前冻结 v4d、346 条输入、正确性规则和 4 条争议题。3 次重复，随机题序与模型先后；开始为4并发，内存保护恢复后为单并发且每条退出进程。每个 Astra 请求创建空白临时会话；无输出修复。生成完成后按输入输出哈希去重盲评，再按记录复核；未按题号继承旧结果。',
        'metadata':{'manifest':'manifest.json','prompt_sha256':m['prompt_sha256'],
                    'judge_calls':judge_calls,'judge_model':m['variants']['deepseek']['model'],
                    'excluded_connectivity_probes':2,
                    'deepseek_wire_parameters':{k:v for k,v in next(r for r in rows if r['variant']=='deepseek')['calls'][0]['request'].items() if k!='input'},
                    'astra_parameters':{'model':'gpt-6-astra','effort':'low','service_tier':'default',
                                        'temperature':'not sent by adapter', 'seed':'not sent by adapter',
                                        'output_token_limit':'not exposed by this app-server route'},
                    'local_transport':'installed ChatGPT-authenticated Codex app-server (stdio)',
                    'controls':[s for s in m['controls'] if s not in ['four paired workers','no prompt tuning, output repair or application-level retries']]
                               +['no prompt tuning or output repair; interrupted unsaved jobs resumed with the same input, never retried based on answer quality'],
                    'resource_phases':PHASES,
                    'resource_measurement':{'resume_sampled_requests':len(resource_peaks),
                                            'resume_peak_rss_mib':max(resource_peaks,default=None),
                                            'scope':'runner plus current app-server process tree; sampled every 0.5 seconds',
                                            'limit_mib':768,'original_phase_peak':'not measured'},
                    'limitations':m['limitations']+['Interrupted after 944 saved rows; concurrency reduced from 4 to 1 and app-server lifetime reduced to one request'],
                    'official_docs':['https://developers.openai.com/api/docs/models/gpt-6-astra',
                                     'https://learn.chatgpt.com/docs/app-server'],
                    'historical_results_are_not_concurrent_baseline':True},
        'links':[{'label':label,'href':name} for name,label in [
            ('manifest.json','冻结计划与真实模型条件'),('v4d.txt','两模型共用完整 v4d 提示词'),
            ('dataset.json','全部固定输入'),('generations.jsonl','全部请求和原始输出'),
            ('review-plan.json','盲评规则及候选绑定'),('reviews.jsonl','原始盲评返回'),
            ('comparison.json','指标与配对差异'),('stability.json','每题三次通过次数'),
            ('run-status.json','运行完成及源码漂移检查'),('execution-notes.json','中断与未落盘请求说明')]],
        'cases':normalized,
        'analysis_tables':[
            {'title':'整体正确性与汇总耗时','description':'主分母排除观察题；uncertain 保留在分母。此处耗时混合两个运行阶段，只作描述，公平比较应看下一张分阶段表。耗时不含 ASR、OCR、写回和 app-server 进程启动，逐条另记完整路径耗时。','columns':['模型','通过','通过率','失败','待确认','请求异常','汇总P50 秒','汇总P95 秒'],'rows':summary_rows},
            {'title':'相同运行阶段内的配对延迟','description':'只纳入两模型均在同一阶段完成的配对，排除跨中断的配对；不将4并发与单并发混为相同条件。','columns':['阶段','模型','有效请求','P50 秒','P95 秒'],'rows':phase_table},
            {'title':'分别看每一轮，不把重复当成新题','description':'每轮仍是相同的 346 道题；其中 340 道进入准确率分母，6 道仅观察。可在下方按轮次筛选全部输出。','columns':['轮次','模型','通过','失败','待确认'],'rows':[[k,LABELS[v],f'{s["pass"]}/{s["n"]}',s['fail'],s['uncertain']] for k,arms in repeat_stats.items() for v,s in arms.items()]},
            {'title':'恢复后的内存保护实测','description':'只统计恢复后有监控记录的 Astra 请求；峰值为每 0.5 秒采样所得，不包含其他用户应用，也无法还原中断前的峰值。','columns':['监控请求数','评测进程峰值 MiB','停止阈值 MiB','并发数'],'rows':[[len(resource_peaks),f'{max(resource_peaks,default=0):.1f}',768,1]]},
            {'title':'释字与综合回归分层','description':'95% 区间以题目为单位做 5000 次配对 bootstrap，保留同题重复之间的关联。','columns':['集合','不同题数','DeepSeek 通过','Astra 通过','差值百分点','差值95%区间'],'rows':split_rows},
            {'title':'所有配对结果','description':'每个配对包含同一题、同一重复的两个模型输出。下方筛选可直接查看对应结果。','columns':['结果','配对次数'],'rows':[[CHANGES[k],pair_counts[k]] for k in CHANGES]},
            {'title':'Astra 路径耗时拆分','description':'首段正文为本机收到首个正文 delta 的时刻，不是服务端计算耗时；DeepSeek 非流式，因此不提供伪造的首 token 比较。','columns':['测量','次数','P50 秒','P95 秒'],'rows':timing},
            {'title':'token 与缓存观测','description':'两家分词器和运行时上下文不同；不能把 token 数差异都解释为提示词长度。缓存和服务负载未控制。','columns':['模型','平均输入','平均输出','平均推理 tokens','输入缓存占比'],'rows':tokens},
            {'title':'综合回归各项能力','description':'用冻结的人工任务标注分层，每项仍按整条委托判定；组合题可重复进入多行，不是模型返回的分类。','columns':['模型','包含能力','通过','通过率'],'rows':capabilities},
            {'title':'Astra 通过次数更少的全部题','description':'完整列出相对退化题，可搜索题号查看所有重复。','columns':['题号','口述','DeepSeek 通过','Astra 通过'],'rows':[[s['case_id'],s['instruction'],s['deepseek_pass'],s['astra_low_pass']] for s in stability if s['delta']<0]},
            {'title':'上次 v4d 出现过失败的 45 条题，本次分别怎样','description':'历史结果仅用于定位关注题；准确率和延迟的主对照使用本次同期请求。表中每个数字均为本次三次重复中的通过次数。','columns':['题号','口述','本次 DeepSeek','本次 Astra'],'rows':[[s['case_id'],s['instruction'],s['deepseek_pass'],s['astra_low_pass']] for s in stability if s['case_id'] in previous_failures]},
        ]}
    if (OUT/'audit-overrides.json').exists():
        data['links'].append({'label':'Codex 复核与原判对照','href':'audit-overrides.json'})
    data['links'] += [{'label':'资源保护续跑条件 '+p.stem,'href':str(p.relative_to(OUT))}
                      for p in sorted((OUT/'resume-phases').glob('*.json'))]
    rendered, metrics = renderer.render(data)
    options = ''
    for key,label in [('split','全部数据集'),('mechanism','全部题型'),('change','全部配对差异'),('phase','全部资源阶段'),('repeat','全部轮次')]:
        values = list(dict.fromkeys(c[key] for c in normalized))
        options += '<select id="'+key+'"><option value="all">'+label+'</option>'+''.join(
            '<option value="'+escape(v,quote=True)+'">'+escape('第 '+v+' 轮' if key=='repeat' else PHASES.get(v,CHANGES.get(v,v)))+'</option>' for v in values)+'</select>'
    rendered = rendered.replace('<input id="search"',options+'<input id="search"')
    iterator = iter(normalized)
    def attrs(match):
        c = next(iterator)
        return match.group(0)[:-1]+''.join(' data-'+k+'="'+escape(c[k],quote=True)+'"' for k in ['split','mechanism','change','phase','repeat'])+'>'
    rendered = re.sub(r'<article class="case"[^>]*>',attrs,rendered)
    rendered = re.sub(r'<script>[\s\S]*?</script>','<script>'+FILTER_JS+'</script>',rendered)
    (OUT/'report.html').write_text(rendered)
    save(OUT/'report.json',data)
    save(OUT/'report.metrics.json',metrics)
    save(OUT/'comparison.json',{'models':all_stats,'paired':dict(pair_counts),'split_intervals':intervals,'phases':phase_stats,'repeats':repeat_stats})
    save(OUT/'stability.json',stability)
    print(json.dumps({'models':all_stats,'paired':dict(pair_counts)},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    build()
