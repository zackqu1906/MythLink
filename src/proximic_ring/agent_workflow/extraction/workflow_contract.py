"""General v6 and legacy v5 contracts; sources stay in audit, not execution text.

Only optional checkpoint text and one final criterion are generated. These are
future checks, never claims of historical success. Structural validation cannot
prove semantics; emitted workflows remain unverified drafts.
"""
import json
import re
from proximic_ring.agent_workflow.task_metadata import COMPACT_FORMATS, validate_profile

FORMAT='agent-workflow-v6'


def validate(value,packet):
    from proximic_ring.agent_workflow.extraction.workflow_input import prepare_model_input
    visible=set(prepare_model_input(packet).selection['trace']['visible_evidence_ids'])
    registry={e['id']:e for e in packet['evidence']}
    def obj(v,required,optional=()):
        if not isinstance(v,dict) or not set(required)<=set(v) or set(v)-set(required)-set(optional):raise ValueError('简洁 Workflow 字段不符合约定')
    def text(v):
        if not isinstance(v,str) or not v.strip() or len(v)>6000:raise ValueError('Workflow 文本字段不合法')
    def array(v,limit=60):
        if not isinstance(v,list) or len(v)>limit:raise ValueError('Workflow 列表不合法')
    def refs(v,call=False,response=False):
        array(v)
        if not v or any(not isinstance(r,str) or r not in visible or r not in registry for r in v):raise ValueError('Workflow 引用了未提供的证据')
        if call and not any(registry[r]['kind']=='call' for r in v):raise ValueError('行动步骤缺少真实工具调用引用')
        if response and not any(is_response(registry[r]) for r in v):raise ValueError('回答步骤缺少实际 Agent 回复引用')
    def is_response(e):
        c=e.get('content')
        return (e['kind']=='message' and isinstance(c,dict) and c.get('type')=='agentMessage'
                and c.get('phase') in (None,'final_answer') and isinstance(c.get('text'),str)
                and bool(c['text'].strip()) and any(r.get('method')=='item/completed' for r in e.get('event_refs',[])))
    def uses_computer_tool(ids):
        return any(registry[r]['kind']=='call' and 'cua' in json.dumps(registry[r].get('content',{})).lower()
                   for r in ids)
    general=isinstance(value,dict) and value.get('format')==FORMAT
    obj(value,('format','workflows','extraction_notes')+(('task_profile',) if general else ()))
    if value['format'] not in COMPACT_FORMATS:raise ValueError('简洁 Workflow 版本错误')
    def profile(p):
        validate_profile(p,with_sources=True);refs(p['sources'])
    if general:profile(value['task_profile'])
    array(value['workflows'],8);array(value['extraction_notes'])
    for s in value['extraction_notes']:text(s)
    for w in value['workflows']:
        obj(w,('name','goal','parameters','steps','completion','conditions','fallbacks')+(('task_profile',) if general else ()))
        if general:profile(w['task_profile'])
        text(w['name']);text(w['goal']);array(w['parameters'],30);names=set()
        for p in w['parameters']:
            obj(p,('name','description','sources'));text(p['description']);refs(p['sources'])
            if not isinstance(p['name'],str) or not re.fullmatch('[a-z][a-z0-9_]*',p['name']) or p['name'] in names:raise ValueError('Workflow 参数名称非法或重复')
            names.add(p['name'])
        array(w['steps'],40)
        if len(w['steps'])<(1 if general else 2):raise ValueError('Workflow 步骤不足')
        for s in w['steps']:
            obj(s,('instruction','sources')+(('kind',) if general else ()),('check',));text(s['instruction'])
            if general and s['kind'] not in ('tool','response'):raise ValueError('Workflow 步骤类型非法')
            refs(s['sources'],call=not general or s['kind']=='tool',response=general and s['kind']=='response')
            if 'check' in s:text(s['check'])
            # Numeric indices can be the actual subject of a coding/explanation
            # task. This legacy guard concerns replaying transient CUA targets.
            if (not general or uses_computer_tool(s['sources'])) and re.search(r'(?:索引|控件编号)\s*[:：=]?\s*\d+|\bclick\(\s*\d+',s['instruction']):raise ValueError('执行步骤不能包含历史控件数字索引')
        obj(w['completion'],('check','sources'));text(w['completion']['check']);refs(w['completion']['sources'])
        array(w['conditions'],20)
        for s in w['conditions']:text(s)
        array(w['fallbacks'],10)
        for f in w['fallbacks']:
            obj(f,('when','instruction','sources'));text(f['when']);text(f['instruction']);refs(f['sources'])
        content={k:v for k,v in w.items() if k!='parameters'}
        used=set(re.findall(r'\{\{([a-z][a-z0-9_]*)\}\}',json.dumps(content,ensure_ascii=False)))
        if used!=names:
            detail=[]
            if names-used:detail.append('声明后未使用：'+', '.join(sorted(names-used)))
            if used-names:detail.append('使用但未声明：'+', '.join(sorted(used-names)))
            raise ValueError('Workflow 参数声明与引用不一致；'+'；'.join(detail))
    if not value['workflows'] and not value['extraction_notes']:raise ValueError('空提取结果必须说明原因')
    return value
