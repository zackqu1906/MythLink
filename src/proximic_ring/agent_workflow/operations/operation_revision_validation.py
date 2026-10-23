"""候选通用 Workflow 的结构、参数边界与逐字段来源校验。

普通增量检查目标/参数不变；有限泛化检查全部来源的覆盖和参数去向。
这些结构检查不能证明自然语言语义蕴含或可执行性。泛化仍需另行审阅与明确合并授权；
普通增量候选仍必须经过精确版本 Trials 验证。
"""
import re
from proximic_ring.agent_workflow.operations.operation_novelty import pointer
from proximic_ring.agent_workflow.operations.operation_identity import digest
from proximic_ring.agent_workflow.operations.operation_flow import validate_flow


def leaves(value,path=''):
    if isinstance(value,dict):
        return {p:v for k,item in value.items() for p,v in leaves(item,path+'/'+k).items()}
    if isinstance(value,list):
        return {p:v for i,item in enumerate(value) for p,v in leaves(item,path+'/'+str(i)).items()}
    return {path:value}


def validate_update(value,packet):
    fields={'outcome','reason','workflow','changes'}
    generalizing=packet.get('material_information',{}).get('kind')=='generalization'
    if generalizing:fields|={'parameter_bindings','coverage'}
    if not isinstance(value,dict) or set(value) not in (fields,fields|{'display'}):raise ValueError('修订输出字段错误')
    if value['outcome'] not in ('candidate','no_change','variant_only') or not isinstance(value['reason'],str) or not value['reason'].strip():raise ValueError('修订结论或依据错误')
    if value['outcome']!='candidate':
        if value['workflow'] is not None or value['changes']:raise ValueError('无候选修订不得包含流程')
        if generalizing and (value['parameter_bindings'] or value['coverage']):raise ValueError('无泛化候选不能包含参数映射或覆盖表')
        return value
    old=packet['current_workflow'];new=value['workflow']
    if not isinstance(new,dict) or set(new)!=set(old):raise ValueError('候选 Workflow 结构改变')
    validate_flow(new)
    fixed=(() if generalizing else ('name','goal','parameters'))+(('task_profile',) if 'task_profile' in old else ())
    for key in fixed:
        if new[key]!=old[key]:raise ValueError('候选不得改变操作目标、名称或参数角色')
    for key in ('steps','conditions','fallbacks'):
        if not isinstance(new[key],list) or (not generalizing and len(new[key])<len(old[key])):raise ValueError('候选不得删除原有路径、条件或恢复信息')
    if len(new['steps'])>40 or len(new['conditions'])>30 or len(new['fallbacks'])>30:raise ValueError('候选过长，需人工整理')
    old_leaves=leaves(old);new_leaves=leaves(new)
    changed={p for p,v in new_leaves.items() if old_leaves.get(p)!=v}
    if not changed or digest(old)==digest(new):raise ValueError('候选没有实质文本变化，应返回 no_change')
    names={p['name'] for p in new['parameters']}
    if any(set(re.findall(r'\{\{([a-z][a-z0-9_]*)\}\}',v))-names for v in new_leaves.values() if isinstance(v,str)):
        raise ValueError('候选引用未定义参数')
    source={s['version_id']:s['workflow'] for s in packet['sources']}
    covered=set()
    if not isinstance(value['changes'],list):raise ValueError('变动来源格式错误')
    for change in value['changes']:
        if not isinstance(change,dict) or set(change)!={'target','sources'}:raise ValueError('变动来源字段错误')
        target=change['target']
        if target not in changed or target in covered:raise ValueError('变动引用不对应实际修改或重复')
        if not isinstance(change['sources'],list) or not change['sources']:raise ValueError('修改缺少来源')
        for ref in change['sources']:
            if not isinstance(ref,dict) or set(ref)!={'version_id','path'} or ref['version_id'] not in source:raise ValueError('修改来源不在给定原始流程中')
            text=pointer(source[ref['version_id']],ref['path'])
            if not isinstance(text,str) or not text.strip():raise ValueError('修改来源须定位到字符串字段')
        covered.add(target)
    if covered!=changed:raise ValueError('每个变动字段均须保留原始来源引用')
    if generalizing:
        from proximic_ring.agent_workflow.operations.operation_generalization import validate_generalization
        validate_generalization(value,packet)
    return value
