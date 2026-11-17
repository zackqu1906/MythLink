"""Build the paired report with editable text and utterance before all results."""
from __future__ import annotations

from collections import Counter
from html import escape as esc
import json
from pathlib import Path
import re
import random
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from tools import build_character_clarification_report as shared
from tools.general_character_experiment import OUT
from tools.character_clarification_experiment import save, digest

LABELS = {'v4d': 'v4d · 完整细则', 'general_char_v1': 'general＋释字边界 v1'}
SPLITS = {'development': '释字开发集', 'holdout': '既有释字预留集', 'regression': '其他功能回归集'}
CHANGES = {'improved': '新版通过／旧版失败', 'regressed': '新版失败／旧版通过',
           'both_pass': '两版通过', 'both_fail': '两版失败', 'uncertain': '含待确认', 'observation': '边界观察'}
TASKS = {'modify': '定向修改', 'insert': '插入', 'polish': '润色', 'format': '结构与格式',
         'translate': '翻译', 'compose': '创作'}
FILTER_JS = '''
const cards=Array.from(document.querySelectorAll('article.case'));
const filterIds=['result','split','mechanism','repeat','search'];
const controls=filterIds.map(id=>document.getElementById(id));
function matches(c,result,split,mechanism,repeat,q){const d=c.dataset;
return (result==='all'||d.change===result||(result==='any_fail'&&(d.base==='fail'||d.new==='fail'))
||(result==='new_fail'&&d.new==='fail')||(result==='base_fail'&&d.base==='fail'))
&&(split==='all'||d.split===split)&&(mechanism==='all'||d.mechanism===mechanism)
&&(repeat==='all'||d.repeat===repeat)&&(!q||d.search.toLowerCase().includes(q));}
function applyFilters(){const v=controls.map(c=>c.value);v[4]=v[4].trim().toLowerCase();let n=0;
cards.forEach(c=>{c.hidden=!matches(c,...v);if(!c.hidden)n++;});
document.getElementById('count').textContent=`显示 ${n} / ${cards.length} 组配对（每组 2 次请求）`;
document.getElementById('empty').hidden=n!==0;}
controls.forEach(c=>c.addEventListener(c.tagName==='INPUT'?'input':'change',applyFilters));
document.getElementById('reset').addEventListener('click',()=>{controls.forEach(c=>c.value=c.tagName==='INPUT'?'':'all');applyFilters();});applyFilters();
'''


def state(row):
    return 'fail' if row.get('error') else row['review']['status']


def change(a, b):
    x, y = state(a), state(b)
    if x == 'observation': return 'observation'
    if x not in ('pass', 'fail') or y not in ('pass', 'fail'): return 'uncertain'
    return {('pass', 'pass'): 'both_pass', ('fail', 'fail'): 'both_fail',
            ('fail', 'pass'): 'improved', ('pass', 'fail'): 'regressed'}[(x, y)]


def summarize(rows):
    eligible = [r for r in rows if r['case']['cohort'] != 'guard']
    counts = Counter(state(r) for r in eligible)
    latencies = [r['latency_s'] for r in eligible if not r.get('error')]
    return {'n': len(eligible), **{s: counts[s] for s in ('pass', 'fail', 'uncertain', 'pending')},
            'pass_rate': counts['pass'] / len(eligible),
            'p50_s': shared.renderer.percentile(latencies, .5),
            'p95_s': shared.renderer.percentile(latencies, .95)}


def options(id_, label, entries):
    return '<select id="'+id_+'" aria-label="'+label+'"><option value="all">'+label+'</option>'+''.join(
        '<option value="'+esc(str(k), quote=True)+'">'+esc(v)+'</option>' for k, v in entries)+'</select>'


def field(label, text, cls=''):
    return '<div class="field '+cls+'"><div class="label">'+esc(label)+'</div><div class="text">'+esc(text)+'</div></div>'


def pair_card(index, a, b):
    c = a['case']; diff = change(a, b)
    search = json.dumps([c, a['final_text'], b['final_text'], a['review'], b['review']], ensure_ascii=False)
    attrs = {'change': diff, 'base': state(a), 'new': state(b), 'split': c['split'],
             'mechanism': c['mechanism'], 'repeat': str(a['repeat']), 'search': search}
    parts = ['<article class="case" id="'+esc(c['id'])+'-r'+str(a['repeat'])+'"'+''.join(
        ' data-'+k+'="'+esc(v, quote=True)+'"' for k, v in attrs.items())+'>',
        '<div class="input-grid grid">', field('可编辑原文', c['target'] or '【空原文】', 'primary-input'),
        field('本次口述', c['utterance'], 'primary-input'), '</div>',
        '<p class="case-id">'+str(index)+' · '+esc(c['id'])+' · 第 '+str(a['repeat'])+' 次 · '+
        esc(SPLITS[c['split']]+' / '+c['mechanism'])+'</p>']
    if c['context']:
        parts += ['<details><summary>给定只读上下文</summary><pre>'+esc(json.dumps(c['context'], ensure_ascii=False, indent=2))+'</pre></details>']
    parts += ['<div class="comparison-heading"><b>'+CHANGES[diff]+'</b><span>同一输入，两次独立请求</span></div><div class="grid result-grid">']
    for row in (a, b):
        rv = row['review']; s = state(row)
        parts += ['<section class="result-block"><h3>'+LABELS[row['variant']]+'</h3><p><span class="badge '+s+'">'+
                  shared.renderer.STATES[s]+'</span> <span class="muted">'+f'{row["latency_s"]:.3f} 秒'+'</span></p>',
                  field('最终正文', row['final_text'] if not row.get('error') else '【请求异常】'),
                  '<div class="review-note '+s+'">'+esc(rv['note'])+'</div>']
        if row.get('error'): parts += ['<p>'+esc(row['error'])+'</p>']
        if rv.get('issues'):
            parts += ['<ul>'+''.join('<li>'+esc(i['reason'])+'<br>应当：'+esc(i['expected'])+'<br>实际：'+esc(i['actual'])+'</li>' for i in rv['issues'])+'</ul>']
        parts += ['</section>']
    parts += ['</div>', '<details><summary>冻结的参考要求（允许其他正确表达）</summary><pre>'+esc(json.dumps(
        {'reference': c.get('reference'), 'review_points': c.get('review_points'), 'mechanical_hints': c['generation_expected']}, ensure_ascii=False, indent=2))+'</pre></details>']
    evidence = {r['variant']: {k: r.get(k) for k in ('id', 'input_sha256', 'output_sha256', 'started_at', 'raw_response', 'checks', 'review')} for r in (a, b)}
    parts += ['<details><summary>本组原始返回、检查与审校记录</summary><pre>'+esc(json.dumps(evidence, ensure_ascii=False, indent=2))+
              '</pre><p>完整线上请求体见 <a href="paired/generations.jsonl">原始请求日志</a>，按上述 id 定位。</p></details></article>']
    return ''.join(parts)


def build():
    shared.OUT = OUT
    manifest, rows, params, judge_calls = shared.load_run('paired')
    frozen = json.loads((OUT / 'freeze.json').read_text())
    by_key = {(r['case_id'], r['repeat'], r['variant']): r for r in rows}
    pairs = [(by_key[(c['id'], repeat, 'v4d')], by_key[(c['id'], repeat, 'general_char_v1')])
             for c in manifest['cases'] for repeat in (1, 2, 3)]
    stats = {v: summarize([r for r in rows if r['variant'] == v]) for v in LABELS}
    paired_counts = dict(Counter(change(a, b) for a, b in pairs))
    paired_delta = statistics.median(b['latency_s'] - a['latency_s'] for a, b in pairs
                                     if a['case']['cohort'] != 'guard' and not a.get('error') and not b.get('error'))
    intervals = {}
    for split in ('all', *SPLITS):
        selected = [(a, b) for a, b in pairs if a['case']['cohort'] != 'guard'
                    and (split == 'all' or a['case']['split'] == split)]
        case_ids = list(dict.fromkeys(a['case_id'] for a, b in selected))
        deltas = [statistics.mean(int(state(b) == 'pass') - int(state(a) == 'pass')
                                  for a, b in selected if a['case_id'] == cid) for cid in case_ids]
        rng = random.Random(2026111603)
        bootstrap = sorted(statistics.mean(rng.choices(deltas, k=len(deltas))) for _ in range(5000))
        intervals[split] = {'unique_cases': len(deltas), 'delta_pp': 100 * statistics.mean(deltas),
                            'ci95_pp': [100 * shared.renderer.percentile(bootstrap, q) for q in (.025, .975)]}
    token_info = {}
    for v in LABELS:
        usage = [call['response'].get('usage', {}) for r in rows if r['variant'] == v for call in r['calls'] if isinstance(call.get('response'), dict)]
        inputs = [u.get('input_tokens', 0) for u in usage]
        cached = [u.get('input_tokens_details', {}).get('cached_tokens', 0) for u in usage]
        token_info[v] = {'responses': len(usage), 'mean_input_tokens': statistics.mean(inputs) if inputs else None,
                         'cached_input_fraction': sum(cached) / sum(inputs) if sum(inputs) else None}
    norm = []
    for r in rows:
        c = r['case']
        norm.append({'id': r['id'], 'group': r['variant'], 'instruction': c['utterance'], 'source_text': c['target'],
                     'context': c['context'], 'expected_result': c.get('reference') or c.get('review_points') or json.dumps(c['generation_expected'], ensure_ascii=False),
                     'output': r['final_text'], 'review': r['review'], 'error': r.get('error', ''),
                     'observation': c['cohort'] == 'guard', 'latency_s': r['latency_s'], 'checks': r.get('checks', []),
                     'features': [c['split'], c['mechanism']], 'evidence': {'raw_record_id': r['id'], 'input_sha256': r['input_sha256']}})
    tables = [{'title': '两版总体对照', 'description': '每版 346 条 × 3 次；6 条历史防误触发观察例排除后，各有 1,020 次计分请求。待确认仍保留在分母。',
               'columns': ['版本', '通过／计分', '通过率', '失败', '待确认', 'P50 秒', 'P95 秒'],
               'rows': [[LABELS[v], f'{s["pass"]}/{s["n"]}', f'{s["pass_rate"]:.1%}', s['fail'], s['uncertain'], f'{s["p50_s"]:.3f}', f'{s["p95_s"]:.3f}'] for v, s in stats.items()]}]
    split_rows = []
    for split in SPLITS:
        for v in LABELS:
            s = summarize([r for r in rows if r['variant'] == v and r['case']['split'] == split])
            split_rows.append([SPLITS[split], LABELS[v], f'{s["pass"]}/{s["n"]}', f'{s["pass_rate"]:.1%}', s['fail'], s['uncertain']])
    tables.append({'title': '释字与其他功能一起检查', 'description': '既有预留集已经用于上一轮实验，本轮不视为全新未见数据。',
                   'columns': ['数据集', '版本', '通过／计分', '通过率', '失败', '待确认'], 'rows': split_rows})
    capability_rows = []
    for task, label in TASKS.items():
        for v in LABELS:
            subset = [r for r in rows if r['variant'] == v and task in r['case'].get('tasks', r['case'].get('expected', []))]
            if not subset:
                subset = [r for r in rows if r['variant'] == v and task in r['case'].get('expected_tasks', [])]
            if subset:
                s = summarize(subset); capability_rows.append([label, LABELS[v], f'{s["pass"]}/{s["n"]}', f'{s["pass_rate"]:.1%}'])
    if capability_rows:
        tables.append({'title': '既有任务要求分组', 'description': '按数据集原有任务标注分组，组合任务可以重叠；统计整例是否通过，不是对单项能力单独打分，也不是模型分类。',
                       'columns': ['要求', '版本', '通过／计分', '通过率'], 'rows': capability_rows})
    tables.append({'title': '配对变化', 'description': '按同一用例、同一次重复配对；重复运行不是独立的新用例。',
                   'columns': ['变化', '配对次数'], 'rows': [[CHANGES[k], n] for k, n in paired_counts.items()]})
    tables.append({'title': '准确率差值与不确定性',
                   'description': '新版减旧版。以独立用例为单位重采样 5,000 次，每条用例的三次重复一起保留；区间跨 0 不证明两版等效或没有退化。',
                   'columns': ['范围', '独立用例数', '差值（百分点）', '95% 配对 bootstrap 区间'],
                   'rows': [['全部计分用例' if k == 'all' else SPLITS[k], s['unique_cases'], f'{s["delta_pp"]:+.2f}',
                             f'{s["ci95_pp"][0]:+.2f} ～ {s["ci95_pp"][1]:+.2f}'] for k, s in intervals.items()]})
    tables.append({'title': '实际输入量与缓存', 'description': '来自全部真实请求返回的 usage；缓存没有独立控制，延迟结论仅限本轮观测。',
                   'columns': ['版本', '提示词字符数', '平均输入 tokens', '输入 tokens 缓存占比'],
                   'rows': [[LABELS[v], frozen['baseline' if v == 'v4d' else 'candidate']['characters'],
                             f'{u["mean_input_tokens"]:.1f}', f'{u["cached_input_fraction"]:.1%}'] for v, u in token_info.items()]})
    summary = [
        '当前 v4d 保持不变。候选版本 general_char_v1 仅保留通用编辑规则、原样的释字专项边界，以及相同的输入与交付约定。',
        f'提示词由 {frozen["baseline"]["characters"]:,} 字符缩短为 {frozen["candidate"]["characters"]:,} 字符。缺失的是精细条目；通用编辑命令仍覆盖全部用户要求，并非取消这些能力。',
        '删除的专项细则：' + '、'.join(frozen['omitted_details']) + '。释字段落逐字不变。',
        f'两版同期运行，346 条用例各重复 3 次，共 {len(rows):,} 次编辑请求；随机交错配对顺序、相同模型与线上参数、相同输入、四并发，每例一次请求。',
        f'新版相对旧版：{paired_counts.get("improved", 0)} 次由失败变通过，{paired_counts.get("regressed", 0)} 次由通过变失败。配对延迟差中位数 {paired_delta:+.3f} 秒（新版减旧版）。',
        f'编辑请求异常 {sum(bool(r.get("error")) for r in rows)} 次。最终审校覆盖所有输出；盲评原判、格式修复记录和 Codex 复核分别保存。',
        '延迟包含网络和服务状态，提示词缓存未受控；本轮观测差异不能直接归因于提示词长度。所有用例均为既有测试数据，不声称真实用户或全新盲测表现。',
    ]
    data = {'schema_version': 1, 'evaluation': {'classification': False}, 'title': 'General 编辑＋释字边界 v1 · 与完整 v4d 对照',
            'groups': LABELS, 'summary': summary,
            'methodology': '使用冻结的宽松 correctness-v1 口径：允许合理表达差异，仅明确改错、遗漏或违令失败。新输出由同模型另行盲评，隐藏版本与编辑提示词；Codex 对争议及差异审校，原评语保留。Codex 参与提示词设计，不是独立人工验收。4 条已知歧义题两版均待确认。',
            'links': [{'label': label, 'href': href} for label, href in [
                ('新版完整提示词', 'general_char_v1.txt'), ('当前 v4d 快照', 'v4d.txt'), ('精确删减差异', 'v4d-to-general_char_v1.diff'),
                ('冻结实验计划', 'freeze.json'), ('完整数据集', 'dataset.json'), ('所有请求与返回', 'paired/generations.jsonl'),
                ('盲评原始记录', 'paired/reviews.jsonl'), ('复核与原判对照', 'paired/audit-overrides.json'),
                ('审校范围', 'audit-scope.json'), ('盲评字段规范化记录', 'paired/review-normalization.json'),
                ('全部配对数据', 'paired-results.json'), ('规范化逐次结果', 'report.json')]],
            'analysis_tables': tables, 'cases': norm,
            'metadata': {'wire_parameters': params, 'judge_calls': judge_calls, 'generation_calls': len(rows),
                         'prompt_freeze': frozen, 'token_usage': token_info, 'manifest': 'paired/manifest.json',
                         'seed_scope': 'Schedule only. No model temperature or seed sent on wire.',
                         'source_drift': json.loads((OUT / 'paired/run-status.json').read_text())['source_drift']}}
    rendered, metrics = shared.renderer.render(data)
    # Keep the skill renderer's validation/summary, replace its single-output cards with paired cards.
    start = rendered.index('<h2 id="cases">')
    end = rendered.index('<section class="panel"><details><summary>评测元数据', start)
    controls = '<h2 id="cases">逐例：先看原文与口述，再看两版结果</h2><div class="filters">'
    controls += options('result', '全部配对结果', [('any_fail', '任一版本失败'), ('new_fail', '新版失败'), ('base_fail', '旧版失败'), *CHANGES.items()])
    controls += options('split', '全部数据集', list(SPLITS.items()))
    controls += options('mechanism', '全部释字方式与回归', [(m, m) for m in dict.fromkeys(a['case']['mechanism'] for a, b in pairs)])
    controls += options('repeat', '全部重复', [(str(i), f'第 {i} 次') for i in (1, 2, 3)])
    controls += '<input id="search" aria-label="搜索原文口述或结果" placeholder="搜索原文、口述、结果、失败原因…"><button id="reset">重置</button><span id="count" role="status"></span></div><p id="empty" hidden>没有匹配的样例。</p>'
    rendered = rendered[:start] + controls + ''.join(pair_card(i, a, b) for i, (a, b) in enumerate(pairs, 1)) + rendered[end:]
    rendered = re.sub(r'<script>[\s\S]*?</script>', lambda _: '<script>'+FILTER_JS+'</script>', rendered)
    css = '''.primary-input .label{font-size:15px;color:#173f49}.primary-input .text{font-size:19px;line-height:1.7;background:#edf3f7;border:1px solid #d4e1e8;min-height:66px}.case-id{font-size:12px;color:#64747e;margin:4px 0 12px}.comparison-heading{display:flex;justify-content:space-between;gap:16px;border-top:1px solid #d9e3e7;padding-top:18px;margin-top:16px}.comparison-heading span{color:#64747e;font-size:12px}.result-grid{margin-top:14px;align-items:start}.result-block{padding:16px;border:1px solid #d9e3e7;border-radius:8px;min-width:0}.review-note{font-size:13px}.review-note.fail{color:#9d302b;background:#fff0ed;padding:10px;border-radius:5px}.result-block ul{padding-left:20px;font-size:13px}.kpis{display:none}'''
    rendered = rendered.replace('</style>', css+'</style>', 1)
    (OUT / 'report.html').write_text(rendered)
    save(OUT / 'report.json', data)
    save(OUT / 'report.metrics.json', metrics)
    save(OUT / 'comparison.json', {'variants': stats, 'paired': paired_counts, 'paired_median_delta_s': paired_delta,
                                 'token_usage': token_info, 'case_bootstrap': intervals})
    save(OUT / 'paired-results.json', [{'case_id': a['case_id'], 'repeat': a['repeat'], 'change': change(a, b), 'case': a['case'],
                                      'results': {r['variant']: {k: r.get(k) for k in ('id', 'final_text', 'latency_s', 'input_sha256', 'output_sha256', 'review')} for r in (a, b)}} for a, b in pairs])
    print(json.dumps({'stats': stats, 'paired': paired_counts, 'judge_calls': judge_calls}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    build()
