"""操作库的完整 Workflow 契约：兼容历史 v4–v6 与目标/自然语言步骤 v7，不改写来源。

v5 投影保留 completion，步骤为 action + 可选关键 check；v4 的步骤为
action/method/check。以 completion 字段是否存在区分，不能用空 method
补齐新版：补造字段会改变内容哈希、向量文本、来源 Pointer 和执行提示词。
此处只验证结构；入组语义仍由后台复核，新候选仍须审阅后启用。
"""
from proximic_ring.agent_workflow.task_metadata import validate_profile


def text(value):
    return isinstance(value, str) and bool(value.strip())


def validate_flow(flow):
    from proximic_ring.agent_workflow.workflow_content import FIELDS, validate_plain
    if isinstance(flow, dict) and set(flow) == FIELDS:
        return validate_plain(flow)
    if not isinstance(flow, dict) or not all(text(flow.get(k)) for k in ('name', 'goal')):
        raise ValueError('缺少完整 Workflow 名称或目标')
    if 'task_profile' in flow:validate_profile(flow['task_profile'])
    compact = 'completion' in flow
    if compact and not text(flow['completion']):
        raise ValueError('新版 Workflow 的最终完成判断不能为空')
    steps = flow.get('steps')
    if not isinstance(steps, list) or not steps:
        raise ValueError('需要完整 Workflow 步骤')
    for step in steps:
        valid = isinstance(step, dict)
        if valid:
            valid = ('action' in step and set(step) <= {'action', 'check'} if compact else
                     set(step) == {'action', 'method', 'check'})
        if not valid or any(not text(v) for v in step.values()):
            raise ValueError('Workflow 步骤格式错误：新版需 action 和可选 check；旧版需 action/method/check')
    parameters = flow.get('parameters')
    if not isinstance(parameters, list) or any(not isinstance(p, dict) or
            not all(text(p.get(k)) for k in ('name', 'description')) for p in parameters):
        raise ValueError('参数结构无效')
    if len({p['name'] for p in parameters}) != len(parameters):
        raise ValueError('参数名称不能重复')
    if not isinstance(flow.get('conditions'), list) or any(not text(c) for c in flow['conditions']):
        raise ValueError('Workflow 条件格式错误')
    if not isinstance(flow.get('fallbacks'), list) or any(not isinstance(f, dict) or
            set(f) != {'when', 'action'} or not all(text(v) for v in f.values()) for f in flow['fallbacks']):
        raise ValueError('Workflow 恢复格式错误')
    return flow
