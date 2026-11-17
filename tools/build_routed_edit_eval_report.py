#!/usr/bin/env python3
"""Adapt an immutable routed-edit run and hash-bound reviews to the HTML renderer."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'src'))
from tools.eval_report import render, validate, metrics
from proximic_ring.text_processing.task_routing import EDIT_TASK_LABELS

ROUTE_NOTES = {
    'v5-combo-triple-modify-polish-compose': '润色作用于已有说明句，另拟感谢句是独立创作；实际只选修改和创作，漏选润色。正文已基本执行这三项。',
    'fidelity-authorized-condition-change': '冻结旧标签将放宽条件与客气表达合并为润色；模型另选修改。新版允许推断式修改，此例存在分类边界重叠，按固定标签记差异，但正文正确，标签口径值得复审。',
    'fidelity-correct-source-date': '本例整体是按素材新拟通知，更新日期是新稿的内容约束，预期只选创作；实际另选修改，正文日期正确。',
    'organize-summary-list': '提炼简短内容和组织成两个要点是两个目标；正文已编号分点，但类别漏选格式整理。',
    'v5-combo-polish-compose-separate': '用户未给出两条建议的原话，需要模型构思，属于创作；实际把创作误标为插入。',
    'v5-combo-modify-compose-separate': '用户给的是感谢大家理解的写作目标，没有提供逐字新增正文；应选创作，模型选成插入。',
    'v5-combo-modify-compose-fiction': '新童话的主体内容由模型构思，属于创作而非插入给定原话；已有署名修改独立保留。',
    'v5-combo-polish-format-summary': '缩短每项内容属于润色，排两条编号属于格式；实际只选择格式整理，正文却已做了缩写。',
}


def build(raw_path, review_path):
    raw=json.loads(raw_path.read_text()); reviews=json.loads(review_path.read_text())
    if reviews['source_sha256'] != hashlib.sha256(raw_path.read_bytes()).hexdigest():
        raise ValueError('Reviews are for another raw report')
    if set(reviews['cases']) != {c['id'] for c in raw['cases']}:
        raise ValueError('Every recorded case needs a review; no extra IDs')
    cases=[]
    for c in raw['cases']:
        review=reviews['cases'][c['id']]
        if review['input_sha256'] != c['input_sha256']:
            raise ValueError('Review/input mismatch: '+c['id'])
        if not c.get('error'):
            assert len(c['calls'])==1
            wire=c['calls'][0]['request']
            actual=json.loads(wire['input'][1]['content'][0]['text'])
            assert actual=={'utterance':c['utterance'].strip(),'editable_text':c['target'],'screen_context':c['context']}
            assert wire['input'][0]['content'][0]['text']==raw['prompts']['nonempty' if c['target'].strip() else 'empty']
        exp=c.get('review_points') or '按本次口述完成任务：'+c['utterance']
        checks=c.get('generation_expected',{})
        if 'modified_text' in checks:
            exp+='\n调用前精确文本断言：'+(checks['modified_text'] or '【空正文】')
        elif checks:
            exp+='\n调用前机械约束：'+json.dumps(checks,ensure_ascii=False)
        cases.append({'id':c['id'],'group':c['cohort'],'features':c.get('features',[]),
            'instruction':c['utterance'],'source_text':c['target'],
            'context':c['context'] if c['context'].get('messages') else '',
            'expected_labels':c['expected_tasks'],'actual_labels':c.get('actual_tasks',[]),
            'expected_result':exp,'output':c.get('final_text',''),'error':c.get('error',''),
            'observation':c['cohort']=='guard','review':review,'route_note':ROUTE_NOTES.get(c['id'],''),
            'latency_s':c['latency_s'],'checks':c.get('checks',[]),
            'evidence':{'id':c['id'],'input_sha256':c['input_sha256'],'raw_labels':c.get('raw_labels'),
                'raw_response':c.get('raw_response'),'source_file':c.get('source_file'),
                'actual_model_input':c.get('model_input'),'calls':c['calls'],'review':review}})
    usages=[call.get('response',{}).get('usage',{}) for c in raw['cases'] for call in c['calls']]
    usage={'requests':sum(len(c['calls']) for c in raw['cases']),
        'input_tokens':sum(u.get('input_tokens',0) for u in usages),
        'output_tokens':sum(u.get('output_tokens',0) for u in usages),
        'cached_requests':sum(u.get('input_tokens_details',{}).get('cached_tokens',0)>0 for u in usages)}
    metadata={k:v for k,v in raw.items() if k!='cases'}
    metadata.update(usage=usage,review_method=reviews['method'],raw_sha256=reviews['source_sha256'],
        review_sha256=hashlib.sha256(review_path.read_bytes()).hexdigest())
    metadata['sampling']='实际 Ark Responses 请求没有 temperature 字段，使用服务端默认；随机种子仅打乱案例顺序。stream=false，thinking=disabled，max_output_tokens=32768。'
    data={'schema_version':1,'title':'手动六类 v5：216 条完整评测','labels':EDIT_TASK_LABELS,
        'groups':{'regression':'既有回归','fresh':'历史新增（现已知）','ambiguity':'省略与含糊指令','combination':'本轮新增多类别组合','guard':'历史边界观察'},
        'cases':cases,'metadata':metadata,'summary':[],
        'methodology':'分类依据调用前冻结的标签；正文由 Codex 逐条离线复核，评审者参与提示词设计，并非独立人工盲评或用户真值。每个评语绑定实际输出及输入哈希。未确认的名称／拼读保留为待确认。一次采样不代表稳定成功率。',
        'links':[{'label':'完整原始请求与响应 JSON','href':raw_path.name},
                 {'label':'逐条语义复核 JSON','href':review_path.name},
                 {'label':'本轮完整提示词','href':'manual-routed-edit-v5-prompts-2026-11-11.html'}]}
    validate(data)
    total=metrics(data['cases']);comb=metrics([c for c in data['cases'] if c['group']=='combination'])
    old=metrics([c for c in data['cases'] if c['group'] not in ('combination','guard')])
    multi=metrics([c for c in data['cases'] if len(c['expected_labels'])>=2])
    data['summary']=[
        f'本轮使用 {raw["model"]}，提示词为六类边界补全版 v5。216 次请求全部完成并成功解析，源码与提示词在运行期间保持不变；每例只调用一次，同时返回类别和正文，无重试。',
        '数据由 168 条既有开发样例和 48 条新增组合案例组成。新增案例覆盖六类的全部 15 种两两组合，以及 12 条三类、4 条四类和 2 条五类组合；包括执行顺序、更新后定位、局部撤销、跨语言范围、固定原话和独立创作。它们是合成开发测试，不是独立真实用户留出集。',
        f'主统计为 210 条编辑，另有 6 条旧边界仅观察。分类集合命中 {total["route_pass"]}/210；正文通过 {total["body"].get("pass",0)}/210、失败 {total["body"].get("fail",0)}、待确认 {total["body"].get("uncertain",0)}。分类和正文同时通过 {total["overall"].get("pass",0)}/210；已知失败 {total["overall"].get("fail",0)} 条。',
        f'新增 48 条组合的分类命中 {comb["route_pass"]}/48，正文通过 {comb["body"].get("pass",0)}/48，同时通过 {comb["overall"].get("pass",0)}/48。连同旧案例，共 {multi["scored"]} 条多类别样例。新拟感谢句、建议和童话有时被标为插入；部分已有文字润色或格式操作也会漏标。分类不匹配的 8 条本轮正文均通过，标签错误与编辑错误应分开看。',
        '正文失败主要涉及未知事实或承诺、弱化时间和责任、释字误改姓氏、篇幅超限、复杂组合丢失范围或语言要求。五类别组合仅两条，一条完整完成，一条丢失中文姓名和翻译要求，不能据这两个样例估计长期可靠性。',
        f'既有 162 条编辑本轮正文通过 {old["body"].get("pass",0)}/162。当前测试不是与 v4 的同期随机配对实验，不将跨轮差异归因于补边界。完整调用延迟包含网络和解析，未测 ASR、OCR 采集或输入法写回；216 次中 {usage["cached_requests"]} 次命中前缀缓存，不能代表纯冷启动。',
    ]
    return data


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('raw',type=Path);parser.add_argument('--reviews',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    data=build(args.raw,args.reviews)
    rendered,stats=render(data)
    args.output.write_text(rendered)
    args.output.with_suffix('.report.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    args.output.with_suffix('.metrics.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(stats,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
