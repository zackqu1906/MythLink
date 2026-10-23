"""从已有审计快照投影参数证据；不主动观察、不改原记录、不执行历史代码。

先读任务原文/补充、参数已引用的证据，再读工具参数。只给模型有限文字，
不发送截图、推理摘要、整页回调或审批日志。完整事件引用保留在本地 blocks。
模型只负责语义映射；validate() 再验证值/引文是源文本的精确子串。
这能防止无来源值，不能单靠字符串证明语义归属，故提示词要求不确定留空。
"""
import ast
import hashlib
import json
import re

from proximic_ring.agent_workflow.extraction.workflow_artifacts import compact_workflows
from proximic_ring.agent_workflow.operations.operation_identity import digest

MAX_CHARS = 80_000
MAX_BLOCK = 20_000


def source_paths(store, source):
    raw = source.get('source_file', '')
    if not raw or not raw.endswith('.audit.json'): raise ValueError('缺少提取审计文件路径')
    audit = (store.directory / raw).resolve()
    if not audit.is_relative_to(store.root.resolve()): raise ValueError('来源路径不在记录目录内')
    return audit, audit.with_name(audit.name.replace('.audit.json', '.input.json'))


def signature(store, source):
    """后台用文件元数据检测补回/修改；请求推荐时只读数据库，不读取原始轨迹。"""
    try:
        return [(str(p.relative_to(store.root.resolve())), p.stat().st_size, p.stat().st_mtime_ns)
                if p.is_file() else (str(p.relative_to(store.root.resolve())), 'missing')
                for p in source_paths(store, source)]
    except (OSError, ValueError): return ['unavailable']


def _read(path):
    if path.stat().st_size > 20 * 1024 * 1024: raise ValueError('来源快照超过参数提取读取上限')
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def _leaves(value, path=''):
    if isinstance(value, str): yield path, value
    elif isinstance(value, dict):
        for key, child in value.items():
            # Binary payloads, transcript reasoning and unrelated metadata never
            # become candidate evidence, even when embedded in a tool result.
            if key in ('event_refs', 'reasoning', 'image', 'images', 'data_url', 'base64'): continue
            yield from _leaves(child, path+'/'+str(key).replace('~','~0').replace('/','~1'))
    elif isinstance(value, list):
        for i, child in enumerate(value): yield from _leaves(child, path+'/'+str(i))


def prepare_evidence(store, sample, revision):
    source = json.loads(sample['source'])
    audit_path, input_path = source_paths(store, source)
    audit, audit_hash = _read(audit_path)
    src = audit['source']
    if any(src.get(k) != source.get(k) for k in ('session_id','episode_id')):
        raise ValueError('参数来源任务标识不一致')
    if src['episode']['status'] != 'completed' or audit.get('source_changed_after_extraction'):
        raise ValueError('任务未完成或提取来源已变化')
    index = source.get('workflow_index', 0)
    flow = compact_workflows(audit)['workflows'][index]
    if digest(flow) != sample['version_id']: raise ValueError('参数来源 Workflow 版本不一致')
    blocks = []; warnings = []; size = 0

    def add(text, role, path, evidence=None, *, transform=None, file=input_path, sha=''):
        nonlocal size
        if not isinstance(text,str) or not text.strip(): return
        if len(text)>MAX_BLOCK or size+len(text)>MAX_CHARS:
            warnings.append(dict(path=path, reason='文本超限，未作为值依据')); return
        if text.startswith('data:image/') or re.fullmatch(r'[A-Za-z0-9+/=\s]{2000,}',text): return
        blocks.append(dict(id='B'+str(len(blocks)+1), role=role, text=text, field=path,
            source=dict(file=str(file.relative_to(store.directory.parent.resolve())), sha256=sha,
                json_pointer=path, evidence_id=(evidence or {}).get('id'),
                event_refs=(evidence or {}).get('event_refs',[]),
                call_id=(evidence or {}).get('call_id'), transform=transform)))
        size += len(text)

    add(src['episode']['goal'], 'task', '/source/episode/goal', file=audit_path, sha=audit_hash)
    if input_path.exists():
        packet, input_hash = _read(input_path)
        if any(packet['source'].get(k) != source.get(k) for k in ('session_id','episode_id')):
            raise ValueError('原始输入快照的任务标识不一致')
        workflow = audit['workflows'][index]
        referenced = {eid for p in workflow.get('parameters',[]) for eid in p.get('sources',p.get('evidence_ids',[]))}
        ordered = sorted(enumerate(packet.get('evidence',[])), key=lambda pair:
            (0 if pair[1]['id'] in referenced else 1 if pair[1]['kind'] in ('message','control_event') else 2, pair[0]))
        for i,e in ordered:
            content=e.get('content'); kind=e['kind']; path=f'/evidence/{i}/content'
            if not isinstance(content,dict): continue
            if e.get('truncated'):
                if kind in ('call','control_event') or (kind=='message' and content.get('type')=='userMessage'):
                    warnings.append(dict(path=path,reason='参数相关来源已截断，不能据此确定唯一取值'))
                continue
            if kind=='call':
                data=content.get('data',{}); value=data.get('arguments', data.get('command'))
                path += '/data/' + ('arguments' if 'arguments' in data else 'command'); role='tool_input'
            elif kind=='message' and content.get('type')=='userMessage':
                value=content.get('content',[]); path+='/content'; role='user_input'
            elif kind=='control_event' and any(r.get('method') in ('input','answer_input') for r in e.get('event_refs',[])):
                value=content.get('text'); path+='/text'; role='user_input'
            elif kind in ('observation','result') and e['id'] in referenced:
                value=content; role='referenced_result'
            else: continue
            for field,text in _leaves(value,path):
                add(text,role,field,e,sha=input_hash)
                # Only decode quoted literals inside recorded code/command. ast
                # literal_eval/json.loads never runs JS, Python, shell or escapes
                # via a subprocess. Keep the exact original string span for audit.
                if kind=='call' and field.rsplit('/',1)[-1] in ('code','command'):
                    for match in re.finditer(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',text):
                        try: literal=json.loads(match[0]) if match[0].startswith('"') else ast.literal_eval(match[0])
                        except (ValueError,SyntaxError): continue
                        if isinstance(literal,str) and literal!=match[0][1:-1]:
                            add(literal,'decoded_literal',field,e,sha=input_hash,
                                transform=dict(kind='quoted_literal',start=match.start(),end=match.end(),literal=match[0]))
    else: warnings.append(dict(reason='缺少原始输入快照；只采用审计中保存的任务原文'))
    model_packet=dict(general_workflow=revision['flow'],source_workflow=flow,
        evidence=[{k:b[k] for k in ('id','role','field','text')} for b in blocks],warnings=warnings)
    return model_packet, blocks


def validate(value, flow, blocks):
    from .parameter_rules import count_parameter, normalized_value
    definitions={p['name']:p for p in flow['parameters']}
    names=set(definitions); registry={b['id']:b for b in blocks}
    if not isinstance(value,dict) or set(value)!={'parameters'} or not isinstance(value['parameters'],list):
        raise ValueError('参数提取输出格式错误')
    seen=set(); output=[]
    for entry in value['parameters']:
        if (not isinstance(entry,dict) or not {'name','status'}<=set(entry)
                or set(entry)-{'name','status','value','evidence','reason'}):raise ValueError('参数实例字段错误')
        entry=dict(entry)
        name=entry['name']
        if not isinstance(name,str) or name not in names or name in seen:raise ValueError('参数实例名称重复或不存在')
        seen.add(name)
        if entry['status'] not in ('observed','missing','uncertain'):raise ValueError('参数实例状态错误')
        # Empty optional JSON fields are a transport detail, not a paid retry.
        if entry['status']!='observed':
            if entry.get('value') is None:entry['value']=''
            if entry.get('evidence') is None:entry['evidence']=[]
        if entry.get('reason') is None:
            entry['reason']='来源支持的实际值' if entry['status']=='observed' else '记录未提供或无法确定'
        if count_parameter(definitions[name]) and type(entry.get('value')) is int:
            entry['value']=str(entry['value'])
        if not isinstance(entry['reason'],str) or not entry['reason'].strip() or len(entry['reason'])>2000:raise ValueError('缺少参数映射依据')
        if not isinstance(entry.get('value'),str) or not isinstance(entry.get('evidence'),list):raise ValueError('参数值或引用格式错误')
        refs=[]; covered=False
        for ref in entry['evidence']:
            if not isinstance(ref,dict) or set(ref)!={'block_id','quote'}:raise ValueError('参数证据引用格式错误')
            b=registry.get(ref['block_id']); quote=ref['quote']
            if not b or not isinstance(quote,str) or not quote or quote not in b['text']:raise ValueError('参数引用不是来源原文')
            if entry['value'] and entry['value'] in quote: covered=True
            start=b['text'].index(quote)
            refs.append(dict(**ref,source=b['source'],quote_start=start,quote_end=start+len(quote)))
        if entry['status']=='observed':
            normalization=normalized_value(definitions[name],entry['value'],[r['quote'] for r in refs])
            if not entry['value'].strip() or len(entry['value'])>MAX_BLOCK or not (covered or normalization):raise ValueError('参数值缺少精确原文依据')
            if '{{' in entry['value'] and '}}' in entry['value']:raise ValueError('模板占位符不能作为历史参数')
            if normalization and normalization['raw_value']!=normalization['value']:
                entry.update(value=normalization['value'],normalization=normalization)
        elif entry['value'] or refs:raise ValueError('不确定/缺失参数不能附带可选值')
        output.append(dict(entry,evidence=refs))
    if seen!=names:raise ValueError('参数实例未覆盖全部参数')
    return output
