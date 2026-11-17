#!/usr/bin/env python3
"""Offline, classification-only adapter for the shared evaluation renderer."""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

from check_bailian_six_tasks import EDIT_TASK_LABELS, metrics, timing, write_json

LABELS = {'dictation': '听写', **EDIT_TASK_LABELS}
SKILL_RENDERER = Path('/Users/admin/.codex/skills/llm-eval-report/scripts/render_report.py')


def percentage(n, d):
    return f'{n/d:.1%}（{n}/{d}）' if d else '—'


def task_labels(result):
    return ['dictation'] if result.get('task') == 'dictation' else result.get('tasks', [])


def human(labels):
    return '、'.join(LABELS[x] for x in labels) or '无'


def raw_six_match(row):
    trace = row.get('trace', {})
    return set(trace.get('raw_tasks', [])) == set(row['expected'].get('tasks', [])) and bool(trace.get('stages'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    out = args.directory
    manifest = json.loads((out/'manifest.json').read_text())
    result = json.loads((out/'results.json').read_text())
    baseline = json.loads((out/'baseline-matched.json').read_text())
    assert result['complete'] and result['sources_unchanged']
    rows = result['cases']; old_rows = baseline['cases']
    current = metrics(rows); old = metrics(old_rows)
    assert current == result['metrics'] == metrics([json.loads(l) for l in (out/'records.jsonl').read_text().splitlines()])
    assert [r['id'] for r in rows] == [r['id'] for r in old_rows] == [r['id'] for r in manifest['cases']]
    server = timing([r['evidence']['response_json']['latency_ms']/1000 for r in rows if not r.get('error')])
    raw_six = {key: {'all_correct': sum(raw_six_match(r) for r in values),
                    'edit_correct': sum(raw_six_match(r) for r in values if r['expected']['task']=='edit')}
               for key,values in [('bailian',rows), ('ascend_history',old_rows)]}
    transitions = Counter(('通过' if a['exact_match'] else '失败')+' → '+('通过' if b['exact_match'] else '失败')
                          for a,b in zip(old_rows,rows))
    failures = [r for r in rows if not r['exact_match']]
    fail_types = Counter()
    for r in failures:
        expected = r['expected']; actual = r.get('actual', {})
        if expected['task'] != actual.get('task'):
            fail_types['编辑变听写' if expected['task']=='edit' else '听写变编辑'] += 1
        else:
            miss = set(expected.get('tasks', []))-set(actual.get('tasks', []))
            extra = set(actual.get('tasks', []))-set(expected.get('tasks', []))
            fail_types['同时漏选和多选' if miss and extra else '漏选任务' if miss else '多选任务'] += 1
    n = current['n']; edits = current['edit_n']
    compare_rows = []
    for label, key, denom in [('完整路由集合匹配', 'exact_correct','n'),
        ('仅编辑样本：最终六类集合匹配','edit_exact_correct','edit_n'),
        ('总体听写/编辑判断（原始模型）','root_correct','n'),
        ('听写最终识别','dictation_correct','dictation_n'),
        ('原始全部题目匹配（含并列拒绝）','raw_exact_correct','n')]:
        compare_rows.append([label,percentage(current[key],current[denom]),percentage(old[key],old[denom])])
    compare_rows.extend([
        ['仅编辑样本：原始六类选择匹配（不经总体/并列回退）',
         percentage(raw_six['bailian']['edit_correct'],edits),percentage(raw_six['ascend_history']['edit_correct'],edits)],
        ['六类 Micro-F1',f'{current["micro_f1"]:.1%}',f'{old["micro_f1"]:.1%}'],
        ['请求/解析错误',str(current['errors']),str(old['errors'])]])
    for label,key in [('平均延迟','mean_s'),('P50','p50_s'),('P95','p95_s'),('P99','p99_s'),('最大延迟','max_s')]:
        compare_rows.append([label,f'{current["latency"][key]*1000:.1f} ms',f'{old["latency"][key]*1000:.1f} ms'])
    perclass = [[label,str(m['support']),f'{m["precision"]:.1%}' if m['precision'] is not None else '—',
                 f'{m["recall"]:.1%}',f'{m["f1"]:.1%}',str(m['fp']),str(m['fn'])]
                for task,label in EDIT_TASK_LABELS.items() for m in [current['per_class'][task]]]
    total_tokens = sum(r['evidence']['response_json'].get('usage',{}).get('input_tokens',0) for r in rows)
    warnings = Counter(code for r in rows for code in r['trace'].get('warning_codes',[]))
    comparison = {'bailian':current,'ascend_history':old,'server_reported_latency':server,
                  'raw_six':raw_six,'transitions':dict(transitions),'failure_types':dict(fail_types),
                  'warning_codes':dict(warnings),'input_tokens_main':total_tokens,
                  'p50_historical_ratio':old['latency']['p50_s']/current['latency']['p50_s'],
                  'p95_historical_ratio':old['latency']['p95_s']/current['latency']['p95_s']}
    write_json(out/'comparison.json',comparison)
    report = {'schema_version':1,'evaluation':{'classification':True,'generated_text':False},
        'title':'百炼 decision-model-preview：六类任务分类评测',
        'labels':LABELS,'groups':{'core':'既有六类回归 130 例','correction':'纠错边界回归 24 例'},
        'summary':[
            f'当前 v20 提示词、输入 state、问题 schema 和预期标签逐例与历史快照核对一致，只替换模型和请求地址。154 例全部成功返回；串行每例一次，预检 1 次另存不计分，无自动重试。',
            f'完整路由集合匹配 {percentage(current["exact_correct"],n)}；102 条编辑样本的最终六类集合匹配 {percentage(current["edit_exact_correct"],edits)}。六类可组合，漏选或多选任意一类均判错。',
            f'客户端 P50 {current["latency"]["p50_s"]*1000:.1f} ms、平均 {current["latency"]["mean_s"]*1000:.1f} ms、P95 {current["latency"]["p95_s"]*1000:.1f} ms；服务端 latency_ms 的 P50 {server["p50_s"]*1000:.1f} ms、P95 {server["p95_s"]*1000:.1f} ms。服务端字段定义未拆解，不能直接称为纯模型计算时间。',
            f'与 11 月 6 日相同 154 例历史记录相比，客户端 P50 约为原来的 1/{comparison["p50_historical_ratio"]:.1f}，但完整分类准确率从 {old["exact_correct"]/n:.1%} 降为 {current["exact_correct"]/n:.1%}。历史服务是昇腾部署 Cloudflare/clef-flash（Jev/SystemOne 兼容），不是 TypeSafe 官方 Jev 权重；历史时延含 SSH 转发，本次为百炼 HTTPS，非同期同网络条件，不能把差异全部归因于模型。',
            '主要错误：插入原话多选修改、语气润色多选翻译、删除或释字纠错漏选修改、转述/照录内容被当成编辑，以及多任务组合漏选。54 条错误逐条显示原始题目结果、最终路由和冻结标签差异。',
            f'沿用当前客户端的听写优先与任意题目概率并列回退规则。{warnings.get("tied_probability",0)} 例出现并列，{warnings.get("edit_without_tasks",0)} 例出现总体编辑但任务全跳过；代码没有替模型补分类。原始六类选择分数另列，便于区分模型与后处理。',
            '这是已参与提示词开发的合成/脱敏回归集，非真实用户准确率或独立测试集。未调优百炼提示词，未修改应用默认模型，未运行昇腾 API、正文生成、ASR、OCR 采集或输入法写回。'],
        'methodology':'分类专用报告。调用前冻结标签；Codex 编写离线集合比较程序，按固定标签逐条计分，无独立人工或校验模型评分。页面“输出”指结构化分类结果，不是生成正文。P50/P95/P99 采用 nearest-rank；错误不从准确率分母移除。缓存命中未由响应报告。',
        'latency_label':'客户端完整分类 P50 / P95',
        'links':[{'label':label,'href':name} for label,name in [('完整请求与响应','results.json'),('实验输入与提示词快照','manifest.json'),('指标与历史对比','comparison.json'),('历史配对记录','baseline-matched.json'),('规范化报告数据','report.json')]],
        'metadata':{**{k:v for k,v in result.items() if k not in ('cases','metrics')},
                    **{k:v for k,v in manifest.items() if k!='cases'},
                    'classification_only':True,'comparison':comparison,
                    'renderer':str(SKILL_RENDERER),'renderer_sha256':hashlib.sha256(SKILL_RENDERER.read_bytes()).hexdigest(),
                    'review_scope':'固定分类标签的机械比较；不对生成正文评分。',
                    'network_attempts':'首次沙箱 DNS 失败未到达服务端，单独保存，不计模型请求；联网预检 1 次成功，正式评测 154 次。'},
        'analysis_tables':[
            {'title':'同样本历史对照','description':'新旧提示词、state、schema、标签相同；部署、网络路径和日期不同。所有指标只使用配对的 154 条合成样本。',
             'columns':['指标','百炼本次 · 11 月 16 日','昇腾 Clef 历史 · 11 月 6 日'],'rows':compare_rows},
            {'title':'六类表现（按最终任务集合）','description':'每类按是否被选中计算；各类可组合，支持数不可直接相加当作样本数。',
             'columns':['类别','预期出现数','精确率','召回率','F1','多选数','漏选数'],'rows':perclass},
            {'title':'54 条错误的互斥分组','description':'先按总体最终结果是否正确分组，再区分同为编辑时的漏选和多选。',
             'columns':['错误类型','样本数'],'rows':[[k,str(v)] for k,v in fail_types.items()]},
            {'title':'历史结果 → 本次结果','description':'只比较固定标签下的最终路由集合。',
             'columns':['变化','样本数'],'rows':[[k,str(v)] for k,v in transitions.items()]}
        ],'cases':[]}
    for index,row in enumerate(rows):
        expected = task_labels(row['expected']); actual = task_labels(row.get('actual',{}))
        missing = set(expected)-set(actual); extra = set(actual)-set(expected)
        note = '与调用前冻结的完整标签集合一致。' if row['exact_match'] else '；'.join(
            part for part in ['漏选：'+human(sorted(missing)) if missing else '',
                               '多选：'+human(sorted(extra)) if extra else ''] if part)
        trace = row['trace']
        if trace.get('warning'):
            note += '。现有后处理：'+trace['warning']
        output = json.dumps({'raw_voice_intent':trace.get('raw_model_task'),
                             'raw_tasks':trace.get('raw_tasks'), 'final':row.get('actual')},ensure_ascii=False,indent=2)
        review = {'status':'pass' if row['exact_match'] else 'fail','note':note,
                  'output_sha256':hashlib.sha256(output.encode()).hexdigest()}
        if not row['exact_match']:
            review['issues']=[{'reason':'冻结分类集合不匹配','expected':human(expected),'actual':human(actual)}]
        report['cases'].append({'id':row['id'],'group':'core' if index<130 else 'correction',
            'instruction':row['utterance'],'source_text':row['target'],'context':row['context'],
            'expected_labels':expected,'actual_labels':actual,'expected_result':'固定预期：'+human(expected),
            'output':output,'latency_s':trace['latency_s'],'error':row.get('error',''),'route_note':note,'review':review,
            'fields':[{'label':'原始总体判断','value':trace.get('raw_model_task','无')},
                      {'label':'原始六类选择','value':human(trace.get('raw_tasks',[]))},
                      {'label':'服务端报告耗时','value':str(row['evidence']['response_json']['latency_ms'])+' ms'},
                      {'label':'历史最终分类','value':human(task_labels(old_rows[index].get('actual',{})))}],
            'evidence':{'request':row['evidence']['request_body'],'response':row['evidence']['response_json'],
                        'routing_trace':trace,'historical_trace':old_rows[index]['trace']}})
    write_json(out/'report.json',report)
    spec = importlib.util.spec_from_file_location('skill_eval_renderer',SKILL_RENDERER)
    renderer = importlib.util.module_from_spec(spec);spec.loader.exec_module(renderer)
    html,stats = renderer.render(report)
    # The shared renderer has generation-centric captions. Adapt only UI text for
    # this classification-only experiment, leaving saved raw evidence unchanged.
    replacements = {'分类＋正文均通过':'完整路由匹配','正文语义通过':'固定标签核对通过',
        '<th>正文通过</th>':'<th>标签核对通过</th>','<th>均通过</th>':'<th>匹配</th>',
        '>正文失败</option>':'>标签核对失败</option>',
        '>模型实际正文</div>':'>结构化分类结果</div>',
        '>正文失败原因</strong>':'>分类核对原因</strong>',
        '正文复核：':'分类核对：',
        '请求异常、类别集合不匹配或正文复核失败':'请求异常或冻结类别集合不匹配',
        '机械断言差异单独展示；语义复核可解释合法表达差异。':'本实验仅分类，不评估生成正文。'}
    for before,after in replacements.items():
        html=html.replace(before,after)
    html=re.sub(r'<div class="kpi">固定标签核对通过.*?</div>',
                f'<div class="kpi">总体听写/编辑判断<b>{current["root_correct"]} / {n}</b><small>{current["root_correct"]/n:.1%} · 原始模型判断</small></div>',html,count=1)
    (out/'report.html').write_text(html)
    write_json(out/'report.metrics.json',stats)
    assert stats['route_pass']==current['exact_correct']==stats['overall']['pass']
    assert html.count('<article class="case"')==n
    print(json.dumps(comparison,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
