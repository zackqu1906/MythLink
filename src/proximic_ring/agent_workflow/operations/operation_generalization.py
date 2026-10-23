"""有限泛化的候选契约；记录来源覆盖，不把语义建议当成已启用的操作。

本地检查引用、完整覆盖和参数去向，不能证明自然语言语义等价。
此阶段只生成审阅候选，成员、当前定义和实时推荐均保持原样。
"""
import json
import re
import uuid

from proximic_ring.agent_workflow.operations.operation_novelty import pointer
from proximic_ring.agent_workflow.operations.operation_store import encoded
from proximic_ring.agent_workflow.extraction.workflow_store import now


def validate_proposal(decision,incoming,general):
    proposal=decision.get('generalization')
    if (not isinstance(proposal,dict) or set(proposal)!={'goal','new','general'}
        or not isinstance(proposal['goal'],str) or not 0<len(proposal['goal'].strip())<=1500):
        raise ValueError('泛化建议缺少明确目标或双方来源')
    mapping=decision['parameter_map']
    if (not set(mapping)<={p['name'] for p in incoming['parameters']}
        or not set(mapping.values())<={p['name'] for p in general['parameters']}
        or len(set(mapping.values()))!=len(mapping)):
        raise ValueError('泛化建议的部分参数映射无效')
    extra=decision['implementation_parameters']
    if extra['new'] or extra['general'] or decision['novelty'].get('kind')!='none':
        raise ValueError('泛化不能丢弃未映射参数或冒充普通增量')
    for side,flow in (('new',incoming),('general',general)):
        refs=proposal[side]
        if not isinstance(refs,list) or not 1<=len(refs)<=12:raise ValueError('泛化来源引用数量无效')
        seen=set()
        for ref in refs:
            if not isinstance(ref,dict) or set(ref)!={'path','quote'}:raise ValueError('泛化来源字段错误')
            text=pointer(flow,ref['path'])
            if not isinstance(text,str) or not text.strip() or text!=ref['quote'] or ref['path'] in seen:
                raise ValueError('泛化依据必须逐字对应双方来源且不重复')
            seen.add(ref['path'])
        if '/goal' not in seen:raise ValueError('泛化必须明确引用双方目标')


def queue_generalization(store,sample,admission,decision,prototype):
    """Coalesce unpaid proposals for one exact base; paid decisions stay immutable."""
    proposal=dict(sample_id=sample['id'],source_version=sample['version_id'],admission_id=admission['id'],
                  reason=decision['reason'],parameter_map=decision['parameter_map'],
                  generalization=decision['generalization'])
    for row in store.rows('operation_updates'):
        detail=json.loads(row['novelty'])
        if (row['operation_id']==decision['operation_id'] and row['base_revision']==prototype['id']
            and row['status']=='pending' and detail['kind']=='generalization'):
            if proposal not in detail['proposals']:detail['proposals'].append(proposal)
            store.db.execute('UPDATE operation_updates SET novelty=? WHERE id=?',(encoded(detail),row['id']))
            return row['id']
    uid=uuid.uuid4().hex
    detail=dict(kind='generalization',material=True,reason='有来源支持的有限泛化，先生成审阅候选',
                parameter_map={},proposals=[proposal])
    store.db.execute('INSERT INTO operation_updates VALUES (?,?,?,?,?,?,?,?,?)',
        (uid,decision['operation_id'],prototype['id'],sample['version_id'],admission['id'],encoded(detail),'pending',None,now()))
    return uid


def source_requirements(flow):
    """All original goals, input roles and execution/verification/recovery strings."""
    from proximic_ring.agent_workflow.operations.operation_revision_validation import leaves
    return {p:v for p,v in leaves(flow).items() if isinstance(v,str) and
            (p in ('/goal','/completion') or p.startswith(('/steps/','/conditions/','/fallbacks/'))
             or (p.startswith('/parameters/') and p.endswith('/description')))}


def normalize_generalization(value,packet):
    """Expand model-authored branch alignment and compute the mechanical diff.

    Raw model output stays in the job record. No semantic edges are invented:
    parameter descriptions use explicit parameter bindings; display names use
    their referenced goal/description. Ordinary updates keep their old format.
    """
    if not isinstance(value,dict) or 'changes' in value:return value
    fields={'outcome','reason','workflow','parameter_bindings','coverage'}
    if set(value) not in (fields,fields|{'display'}):raise ValueError('泛化输出字段错误')
    result=dict(value,changes=[])
    if value['outcome']!='candidate':return result
    from proximic_ring.agent_workflow.operations.operation_flow import validate_flow
    from proximic_ring.agent_workflow.operations.operation_revision_validation import leaves
    flow=validate_flow(value['workflow']);sources={s['version_id']:s['workflow'] for s in packet['sources']}
    bindings=value['parameter_bindings'];raw=value['coverage']
    if not isinstance(bindings,dict) or set(bindings)!=set(sources) or not isinstance(raw,list):
        raise ValueError('泛化缺少来源分支对应或参数去向')
    requirements=source_requirements(flow);coverage=[];seen=set()
    def expand(paths,available):
        if not isinstance(paths,list) or not paths:raise ValueError('分支对应缺少路径')
        expanded=[]
        for path in paths:
            if not isinstance(path,str):raise ValueError('分支路径必须是 JSON Pointer')
            matches=([path] if path in available else
                     [p for p in available if p.startswith(path+'/')]
                     if re.fullmatch(r'/(steps|conditions|fallbacks|parameters)/[0-9]+',path) else [])
            if not matches:raise ValueError('分支引用不存在或范围过宽：'+path)
            if set(matches)&set(expanded):raise ValueError('分支路径重复覆盖')
            expanded.extend(matches)
        return expanded
    for group in raw:
        if not isinstance(group,dict) or set(group)!={'version_id','mappings'}:raise ValueError('来源分支组字段错误')
        vid=group['version_id']
        if vid not in sources or vid in seen:raise ValueError('来源分支组不存在或重复')
        seen.add(vid)
        available={p:v for p,v in source_requirements(sources[vid]).items() if not p.startswith('/parameters/')}
        if not isinstance(group['mappings'],list):raise ValueError('来源分支对应格式错误')
        for item in group['mappings']:
            if not isinstance(item,dict) or set(item)!={'paths','targets','handling','reason'}:raise ValueError('分支对应字段错误')
            paths=expand(item['paths'],available);targets=expand(item['targets'],requirements)
            coverage.extend(dict(version_id=vid,path=p,targets=targets,handling=item['handling'],reason=item['reason']) for p in paths)
    if seen!=set(sources):raise ValueError('来源分支对应遗漏原始版本')
    positions={p['name']:i for i,p in enumerate(flow['parameters'])}
    for vid,source in sources.items():
        mapping=bindings[vid]
        if not isinstance(mapping,dict) or set(mapping)!={p['name'] for p in source['parameters']}:
            raise ValueError('泛化参数映射遗漏来源参数')
        for i,p in enumerate(source['parameters']):
            target=mapping[p['name']]
            if not isinstance(target,str) or target not in positions:raise ValueError('泛化参数引用未知目标')
            coverage.append(dict(version_id=vid,path=f'/parameters/{i}/description',
                targets=[f'/parameters/{positions[target]}/description'],handling='shared',
                reason='依据模型提供的参数对应保留输入角色；具体使用条件见步骤'))
    result['coverage']=coverage
    refs={}
    for item in coverage:
        for target in item['targets']:
            ref=dict(version_id=item['version_id'],path=item['path'])
            if ref not in refs.setdefault(target,[]):refs[target].append(ref)
    refs['/name']=[dict(version_id=vid,path='/goal') for vid in sources]
    for i in range(len(flow['parameters'])):
        refs[f'/parameters/{i}/name']=refs.get(f'/parameters/{i}/description',[])
    previous=leaves(packet['current_workflow'])
    for path,text in leaves(flow).items():
        if previous.get(path)==text:continue
        if not refs.get(path):raise ValueError('候选新增字段缺少分支来源：'+path)
        result['changes'].append(dict(target=path,sources=refs[path]))
    return result


def validate_generalization(value,packet):
    """Audit exhaustive source coverage; do not claim semantic/execution validation."""
    flow=value['workflow'];sources={s['version_id']:s['workflow'] for s in packet['sources']}
    names={p['name'] for p in flow['parameters']}
    if any(set(p)!={'name','description'} or not re.fullmatch(r'[a-z][a-z0-9_]*',p['name']) for p in flow['parameters']):
        raise ValueError('泛化参数只能包含有效名称和说明；可选要求用明确条件表达')
    used=set(re.findall(r'\{\{([a-z][a-z0-9_]*)\}\}',json.dumps(flow,ensure_ascii=False)))
    if used!=names:raise ValueError('泛化参数须全部使用，且不能引用未定义参数')
    bindings=value.get('parameter_bindings')
    if not isinstance(bindings,dict) or set(bindings)!=set(sources):raise ValueError('泛化必须说明每个来源版本的参数去向')
    for vid,source in sources.items():
        mapping=bindings[vid]
        if (not isinstance(mapping,dict) or set(mapping)!={p['name'] for p in source['parameters']}
            or any(not isinstance(v,str) or v not in names for v in mapping.values())):
            raise ValueError('泛化参数映射遗漏来源参数或引用未知目标')
    expected={(vid,path) for vid,source in sources.items() for path in source_requirements(source)}
    coverage=value.get('coverage')
    if not isinstance(coverage,list):raise ValueError('泛化必须提供完整来源覆盖表')
    seen=set()
    for item in coverage:
        if not isinstance(item,dict) or set(item)!={'version_id','path','targets','handling','reason'}:
            raise ValueError('泛化覆盖字段错误')
        key=(item['version_id'],item['path'])
        if key not in expected or key in seen:raise ValueError('泛化覆盖来源不存在或重复')
        if item['handling'] not in ('shared','conditional','alternative') or not isinstance(item['reason'],str) or not item['reason'].strip():
            raise ValueError('泛化覆盖须说明共享、条件或备选路径')
        if not isinstance(item['targets'],list) or not item['targets']:raise ValueError('原要求缺少候选去向')
        for target in item['targets']:
            if target not in source_requirements(flow):raise ValueError('覆盖须指向候选的目标、参数说明、步骤、条件或核验')
        seen.add(key)
    if seen!=expected:raise ValueError('泛化遗漏原目标、参数、步骤、条件或核验要求')
