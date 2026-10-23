"""阶段 5：实质新增信息的证据门槛；不以编辑距离或长度冒充语义判断。

第一层由 admission prompt 判断“已有操作是否缺少这一信息”；第二层在本文件
检查原文定位、允许变化类别、重复内容。模型宣称 novel 但没有有效新增证据时，
仍可保留 SAME 入组结论，但不排入更新队列，并记录抑制原因。
这不能数学证明语义正确，因此生成的新版只能是候选，不覆盖可用版。
"""
import json
from proximic_ring.agent_workflow.operations.operation_identity import normalize

KINDS={'none','condition','fallback','path','verification'}


def pointer(value,path):
    if not isinstance(path,str) or not path.startswith('/'):raise ValueError('来源引用必须是 JSON Pointer')
    try:
        for key in path[1:].split('/'):
            key=key.replace('~1','/').replace('~0','~')
            value=value[int(key)] if isinstance(value,list) and key.isdigit() else value[key]
        return value
    except (KeyError,IndexError,TypeError,ValueError):raise ValueError('来源引用不存在') from None


def material_information(novelty,new_flow,general):
    if not isinstance(novelty,dict) or set(novelty)!={'kind','reason','evidence'}:raise ValueError('新增信息字段错误')
    kind=novelty['kind'];evidence=novelty['evidence']
    if kind not in KINDS or not isinstance(novelty['reason'],str) or not novelty['reason'].strip():raise ValueError('新增信息分类或说明无效')
    if not isinstance(evidence,list) or len(evidence)>8:raise ValueError('新增信息证据过多或格式错误')
    if kind=='none':
        if evidence:raise ValueError('无新增信息不能附带更新证据')
        return dict(novelty,material=False,suppressed_reason='')
    existing=normalize(json.dumps(general,ensure_ascii=False))
    allowed={'condition':('/conditions/',),'fallback':('/fallbacks/',),
             'path':('/steps/',),'verification':('/steps/',)}[kind]
    useful=[]
    for item in evidence:
        if not isinstance(item,dict) or set(item)!={'path','quote'}:raise ValueError('新增证据字段错误')
        text=pointer(new_flow,item['path'])
        if not isinstance(text,str) or not text.strip() or text!=item['quote']:raise ValueError('新增证据必须逐字对应来源字段')
        # v5 moves the final check to /completion; it is just as traceable as
        # /steps/N/check. Do not accept arbitrary completion-prefixed pointers.
        final_check=kind=='verification' and item['path']=='/completion'
        if not final_check and not item['path'].startswith(allowed):raise ValueError('新增证据与变化类别不匹配')
        if kind=='verification' and not (final_check or item['path'].endswith('/check')):
            raise ValueError('核验新增须引用步骤 check 或最终 completion 字段')
        if normalize(text) not in existing:useful.append(item)
    return dict(novelty,material=bool(useful),evidence=useful,
                suppressed_reason='' if useful else '没有超出当前通用流程的有效来源证据，不生成新版')


def validate_decisions(value,packet):
    if not isinstance(value,dict) or set(value) not in ({'decisions'},{'decisions','new_operation_display'}):raise ValueError('入组复核格式错误')
    expected={c['operation_id']:c for c in packet['candidates']}
    items=value['decisions']
    if not isinstance(items,list) or len(items)!=len(expected):raise ValueError('入组复核必须覆盖全部候选')
    seen=set();result=[]
    for d in items:
        fields={'operation_id','relation','reason','parameter_map','implementation_parameters','novelty'}
        if not isinstance(d,dict) or set(d) not in (fields,fields|{'generalization'}):raise ValueError('入组复核字段错误')
        oid=d['operation_id']
        if oid not in expected or oid in seen or d['relation'] not in ('same','generalizable','different','uncertain'):raise ValueError('候选或关系错误')
        seen.add(oid)
        if not isinstance(d['reason'],str) or not 0<len(d['reason'])<=3000:raise ValueError('复核依据无效')
        mapping=d['parameter_map'];extra=d['implementation_parameters']
        if not isinstance(mapping,dict) or any(not isinstance(v,str) for v in mapping.values()):raise ValueError('参数映射无效')
        if (not isinstance(extra,dict) or set(extra)!={'new','general'} or any(not isinstance(v,list) or
            any(not isinstance(n,str) for n in v) or len(v)!=len(set(v)) for v in extra.values())):raise ValueError('实现参数无效')
        general=expected[oid]['workflow'];incoming=packet['new_workflow']
        if d['relation']=='same':
            left={p['name'] for p in incoming['parameters']};right={p['name'] for p in general['parameters']}
            if (len(set(mapping.values()))!=len(mapping) or set(mapping)&set(extra['new']) or set(mapping.values())&set(extra['general'])
                or set(mapping)|set(extra['new'])!=left or set(mapping.values())|set(extra['general'])!=right):
                raise ValueError('每侧参数必须完整且无重复地映射或标记为实现参数')
        elif d['relation']=='generalizable':
            from proximic_ring.agent_workflow.operations.operation_generalization import validate_proposal
            validate_proposal(d,incoming,general)
        elif mapping or extra['new'] or extra['general'] or d['novelty'].get('kind')!='none':raise ValueError('非同类不能更新或映射参数')
        if d['relation']!='generalizable' and 'generalization' in d:raise ValueError('只有可泛化关系能提出泛化依据')
        result.append(dict(d,novelty=material_information(d['novelty'],incoming,general)))
    return result
