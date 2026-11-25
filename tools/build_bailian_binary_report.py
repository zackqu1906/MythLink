#!/usr/bin/env python3
"""Render the frozen 300-case binary results offline with full evidence."""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import re

import check_bailian_binary as base
from check_bailian_six_tasks import ROOT, write_json
from data.bailian_binary_cases_20261116 import FAMILIES

RENDERER=Path(__file__).with_name('eval_report.py')
LABELS=base.LABELS
NAMES={'compact-v1':'规则在问题中＋对象 state','boundary-v2':'强化边界＋对象 state',
       'shared-rules-v3':'共同规则在 state 中','text-state-v4':'强化边界＋文本 state',
       'short-v5':'简洁问题与判据','examples-v6':'成对示例＋英文标签','chinese-labels-v7':'成对示例＋中文标签'}


def pct(n,d):return f'{n/d:.1%}（{n}/{d}）' if d else '—'


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path);args=parser.parse_args();out=args.directory
    manifest=json.loads((out/'manifest.json').read_text());dataset=json.loads((out/'dataset.json').read_text())
    selection=json.loads((out/'selection.json').read_text());run=json.loads((out/'run.json').read_text())
    assert run['complete'] and run['sources_unchanged']
    rows=run['cases'];assert len(rows)==300
    assert [r['id'] for r in rows]==[r['id'] for r in dataset['cases']]
    assert rows==[json.loads(line) for line in (out/'run.jsonl').read_text().splitlines()]
    assert hashlib.sha256((out/'dataset.json').read_bytes()).hexdigest()==manifest['dataset_sha256']
    assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in manifest['source_sha256'].items())
    groups={'regression':'既有回归 120 条','new':'新造边界 180 条'}
    scores={'all':base.score(rows),'regression':base.score([r for r in rows if r['cohort']=='regression']),
            'new':base.score([r for r in rows if r['cohort']=='new']),
            'development_overlap':base.score([r for r in rows if r['id'] in manifest['dev_ids']]),
            'other_regression':base.score([r for r in rows if r['cohort']=='regression' and r['id'] not in manifest['dev_ids']])}
    by_family={f:base.score([r for r in rows if r['family']==f]) for f in FAMILIES}
    pair_rows={pid:[r for r in rows if r.get('pair_id')==pid] for pid in sorted({r['pair_id'] for r in rows if r.get('pair_id')})}
    assert len(pair_rows)==90 and all(len(p)==2 and {r['expected_task'] for r in p}==set(LABELS) for p in pair_rows.values())
    pairs=Counter(sum(r['correct'] for r in p) for p in pair_rows.values())
    dev=[json.loads((out/f).read_text()) for f in ['dev.json','followup-dev.json','final-dev.json']]
    dev_calls=sum(len(d['cases']) for d in dev)
    assert dev_calls==420
    usage=sum(r.get('response',{}).get('usage',{}).get('input_tokens',0) for r in rows)
    development_usage=sum(r.get('response',{}).get('usage',{}).get('input_tokens',0) for d in dev for r in d['cases'])
    stats=scores['all'];matrix=stats['confusion']
    metrics={'main':scores,'families':by_family,'contrast_pairs':{'n':90,'both_correct':pairs[2],
        'one_correct':pairs[1],'neither_correct':pairs[0]},'development':selection['development_metrics'],
        'selected':selection['selected'],'requests':{'development':dev_calls,'final':300,'total':dev_calls+300},
        'input_tokens':{'development':development_usage,'final':usage},
        'dataset_sha256':manifest['dataset_sha256']}
    write_json(out/'metrics.json',metrics)
    main_table=[]
    for name,label in [('all','完整 300 条'),('regression','旧回归 120 条'),('new','新造 180 条'),
                       ('development_overlap','旧回归中参与选型的 60 条'),('other_regression','旧回归中未参与本轮选型的 60 条')]:
        s=scores[name];main_table.append([label,pct(s['correct'],s['n']),str(s['confusion']['dictation']['edit']),
            str(s['confusion']['edit']['dictation']),f'{s["latency"]["p50_s"]*1000:.1f} ms',f'{s["latency"]["p95_s"]*1000:.1f} ms'])
    dev_table=[]
    for name,s in selection['development_metrics'].items():
        dev_table.append([NAMES[name]+('（选中）' if name==selection['selected'] else ''),pct(s['correct'],s['n']),
            str(s['confusion']['dictation']['edit']),str(s['confusion']['edit']['dictation']),f'{s["latency"]["p50_s"]*1000:.1f} ms'])
    family_table=[]
    for family,label in FAMILIES.items():
        s=by_family[family];both=sum(all(r['correct'] for r in p) for p in pair_rows.values() if p[0]['family']==family)
        family_table.append([label,pct(s['correct'],s['n']),str(s['confusion']['dictation']['edit']),
            str(s['confusion']['edit']['dictation']),f'{both}/9'])
    worst=sorted(FAMILIES,key=lambda f:by_family[f]['accuracy'])[:3]
    summary=[
        f'百炼 decision-model-preview 单题二分类实测：300 条，听写和编辑各 150 条。正确 {pct(stats["correct"],300)}，请求/解析错误 {stats["errors"]}。直接采用接口 choice，没有根据关键词或测试标签纠正模型输出。',
        f'客户端平均 {stats["latency"]["mean_s"]*1000:.1f} ms，P50 {stats["latency"]["p50_s"]*1000:.1f} ms，P95 {stats["latency"]["p95_s"]*1000:.1f} ms；服务端报告 latency_ms 的 P50 {stats["server_latency"]["p50_s"]*1000:.1f} ms。客户端计时包含连接、网络和完整响应读取，不含语音识别、OCR 采集、正文生成和写回。',
        f'听写误判为编辑 {matrix["dictation"]["edit"]}/150；编辑漏判为听写 {matrix["edit"]["dictation"]}/150。听写召回率 {stats["per_class"]["dictation"]["recall"]:.1%}，编辑召回率 {stats["per_class"]["edit"]["recall"]:.1%}。',
        f'120 条既有回归为 {pct(scores["regression"]["correct"],120)}；180 条新造边界为 {pct(scores["new"]["correct"],180)}。新增样本组成 90 组对照，其中两条都答对 {pairs[2]}/90；同一口述更换原文、命令改为转述或照录、全部撤销改为部分保留等有意造成相反标签。',
        '边界覆盖：直接操作、隐式纠错/释字/拼读、更新与续写、引用与照录、转述与采纳、计划与否定、取消与恢复、空原文/OCR/资料中的指令、跨语言/礼貌请求、组合任务与局部范围。新造样本中较弱的三组是：'+'；'.join(FAMILIES[f]+' '+pct(by_family[f]['correct'],18) for f in worst)+'。',
        f'先在固定 60 条旧回归上测试 7 个候选，共 420 次开发请求，按事先约定的准确率优先规则选出 {selection["selected"]}（{NAMES[selection["selected"]]}）。开发最好为 {pct(selection["development_metrics"][selection["selected"]]["correct"],60)}。选中版本在正式测试前冻结，随后 300 条每条只测一次；没有使用新增 180 条的模型结果调参。所有候选失败记录均保留。',
        '7 个开发候选的分数接近且都不高，单轮结果不能证明选中版本稳定优于其他版本。请求组织、规则长度、示例和标签语言的有限尝试未解决所有语义边界，不能因接口返回成功就认为模型适合直接替换现有自动路由。',
        '120 条既有样本整合自 10 个历史样例文件；180 条为本轮 Codex 构造的合成对照。Codex 同时设计提示词和标注数据，没有独立人工验收。它们是有针对性的测试，不代表真实用户分布；成对样本相互关联，不能把 300 条当作 300 个完全独立随机观测。',
        '全部取消、保持原样、空原文且无法执行删除/替换时，沿用现有二分类路由的听写回退标签。这只是路由评分定义，不等于应该把取消指令写进正文。标注与提示词在调用前固定，未按本次输出修改标签。']
    normalized={'schema_version':1,'evaluation':{'classification':True,'generated_text':False},
        'title':'百炼听写／编辑二分类 · 300 条边界测试','labels':LABELS,'groups':groups,'summary':summary,
        'methodology':'仅分类，不生成正文。真实请求为 model + state + 单个 choice 问题，选项为 dictation/edit（中文标签候选另有固定映射）。提示词规则在选中方案的 state 前缀，输入在末尾；预期标签、样例 ID 和标注理由均不发送给模型。概率并列只记录，仍采用接口 choice；不做阈值重判。分类核对由固定标签的离线相等比较完成，并非独立人工评分。P50/P95 使用 nearest-rank；缓存命中未报告。',
        'latency_label':'完整二分类请求 P50 / P95',
        'links':[{'label':label,'href':path} for label,path in [('300 条可复用数据集','dataset.json'),
            ('完整正式请求与响应','run.json'),('准确率、混淆矩阵和延迟','metrics.json'),('最终提示词与选型依据','selection.json'),
            ('初始开发记录','dev.json'),('布局开发记录','followup-dev.json'),('示例/标签开发记录','final-dev.json'),
            ('冻结清单与哈希','manifest.json'),('规范化报告数据','report.json')]],
        'metadata':{'manifest':manifest,'selection':selection,'metrics':metrics,
            'started_at':run['created_at'],'finished_at':run['finished_at'],'endpoint':run['endpoint'],
            'source_hashes_unchanged':run['sources_unchanged'],'renderer_sha256':hashlib.sha256(RENDERER.read_bytes()).hexdigest(),
            'visual_verification':'未进行浏览器视觉验收：此前浏览器策略拒绝 file URL；本轮仅做文件内容、指标、链接及离线筛选脚本验证，不尝试绕过。'},
        'analysis_tables':[
            {'title':'正式测试：整体与来源分组','description':'开发请求不混入正式 300 条的延迟与准确率。',
             'columns':['范围','准确率','听写→编辑','编辑→听写','P50','P95'],'rows':main_table},
            {'title':'混淆矩阵','description':'行是真实标签，列是模型标签。',
             'columns':['预期','实际听写','实际编辑','请求错误'],
             'rows':[[LABELS[k],str(matrix[k]['dictation']),str(matrix[k]['edit']),str(matrix[k]['error'])] for k in LABELS]},
            {'title':'新增边界家族','description':'每组 18 条，听写与编辑各 9 条；两条都正确才算一组成对通过。',
             'columns':['边界','准确率','听写→编辑','编辑→听写','成对全对'],'rows':family_table},
            {'title':'开发过程：7 个候选，均使用相同 60 条旧样本','description':'按准确数最高、再按听写误触发更少、再按提示词更短选型。这里不是新增测试集成绩。',
             'columns':['候选','准确率','听写→编辑','编辑→听写','P50'],'rows':dev_table},
        ],'cases':[]}
    for r in rows:
        expected=r['expected_task'];actual=r.get('actual_task');output=LABELS.get(actual,'')
        note=(('命中冻结标签。' if r['correct'] else f'模型判为{LABELS.get(actual,"请求错误")}，应为{LABELS[expected]}。')
              +' 标注依据：'+r['label_reason'])
        review={'status':'pass' if r['correct'] else 'fail','note':note,
                'output_sha256':hashlib.sha256(output.encode()).hexdigest()}
        if not r['correct']:review['issues']=[{'reason':'二分类与冻结标签不符','expected':LABELS[expected]+'；'+r['label_reason'],'actual':LABELS.get(actual,'请求错误')}]
        normalized['cases'].append({'id':r['id'],'group':r['cohort'],
            'features':[FAMILIES.get(r['family'],'既有回归'),r.get('pair_id') or '历史样本'],
            'instruction':r['utterance'],'source_text':r['target'],'context':r['context'],
            'expected_labels':[expected],'actual_labels':[actual] if actual else [],
            'expected_result':LABELS[expected]+'：'+r['label_reason'],'output':output,'review':review,
            'route_note':note,'error':r.get('error',''),'latency_s':r['latency_s'],
            'fields':[{'label':'两类概率','value':json.dumps(r.get('probabilities',{}),ensure_ascii=False)},
                      {'label':'置信度／并列','value':str(r.get('confidence'))+' / '+str(r.get('tied',False))},
                      {'label':'样例来源','value':r['provenance']},
                      {'label':'服务端报告耗时','value':str(r.get('response',{}).get('latency_ms'))+' ms'}],
            'evidence':{'request':r['request'],'response':r.get('response'),
                        'started_at':r['started_at'],'finished_at':r['finished_at']}})
    write_json(out/'report.json',normalized)
    spec=importlib.util.spec_from_file_location('eval_skill_renderer',RENDERER);renderer=importlib.util.module_from_spec(spec);spec.loader.exec_module(renderer)
    html,render_stats=renderer.render(normalized)
    replacements={'分类＋正文均通过':'二分类正确','正文语义通过':'固定标签核对通过','<th>正文通过</th>':'<th>标签核对通过</th>',
        '>正文失败</option>':'>标签核对失败</option>','>模型实际正文</div>':'>模型分类结果</div>',
        '>正文失败原因</strong>':'>分类失败原因</strong>','正文复核：':'分类核对：',
        '请求异常、类别集合不匹配或正文复核失败':'请求异常或冻结类别不匹配',
        '机械断言差异单独展示；语义复核可解释合法表达差异。':'本实验仅分类，不对生成正文评分。'}
    for a,b in replacements.items():html=html.replace(a,b)
    html=re.sub(r'<div class="kpi">固定标签核对通过.*?</div>',
        f'<div class="kpi">听写误触发编辑<b>{matrix["dictation"]["edit"]} / 150</b><small>编辑漏判听写 {matrix["edit"]["dictation"]} / 150</small></div>',html,count=1)
    html=re.sub(r'<div class="kpi">预期类别集合命中.*?</div>',
        f'<div class="kpi">新增边界准确率<b>{scores["new"]["correct"]} / 180</b><small>{scores["new"]["accuracy"]:.1%} · 未用于本轮调参</small></div>',html,count=1)
    # Every binary example has exactly one label; a task-count selector/table
    # would suggest a multi-label evaluation that this experiment does not run.
    html=re.sub(r'<h3 style="margin-top:20px">按预期类别数</h3><table>.*?</table>','',html,count=1)
    html=re.sub(r'<select id="tasks".*?</select>','',html,count=1)
    (out/'report.html').write_text(html);write_json(out/'report.metrics.json',render_stats)
    assert render_stats['route_pass']==stats['correct']==render_stats['overall'].get('pass',0)
    assert html.count('<article class="case"')==300
    print(json.dumps({'scores':scores,'pairs':metrics['contrast_pairs'],'weakest_families':worst,
                      'requests':metrics['requests']},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
