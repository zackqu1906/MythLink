"""Goal/steps workflows and lossless, read-only projection of historical formats."""
import copy
import re

FORMAT = 'agent-workflow-v7'
FIELDS = {'name', 'goal', 'parameters', 'steps'}
PARAMETER = re.compile(r'\{\{([a-z][a-z0-9_]*)\}\}')


def is_plain(flow):
    return isinstance(flow, dict) and isinstance(flow.get('steps'), list) and bool(flow['steps']) and all(
        isinstance(step, str) for step in flow['steps'])


def validate_plain(flow):
    if not isinstance(flow, dict) or set(flow) != FIELDS:
        raise ValueError('Workflow 需要 name、goal、parameters、steps')
    def text(value):
        return isinstance(value, str) and bool(value.strip()) and len(value) <= 6000
    if not all(text(flow[k]) for k in ('name', 'goal')):
        raise ValueError('Workflow 名称或目标无效')
    if not is_plain(flow) or len(flow['steps']) > 40 or not all(text(s) for s in flow['steps']):
        raise ValueError('Workflow 需要 1–40 个非空自然语言步骤')
    params = flow['parameters']
    if not isinstance(params, list) or len(params) > 30:
        raise ValueError('Workflow 参数列表无效')
    names = set()
    for p in params:
        if (not isinstance(p, dict) or set(p) not in ({'name', 'description'}, {'name', 'description', 'required'})
                or not text(p.get('description')) or not isinstance(p.get('name'), str)
                or not re.fullmatch('[a-z][a-z0-9_]*', p['name']) or p['name'] in names
                or ('required' in p and type(p['required']) is not bool)):
            raise ValueError('Workflow 参数定义或名称无效')
        names.add(p['name'])
    used = set(PARAMETER.findall(flow['goal'] + '\n' + '\n'.join(flow['steps'])))
    if used != names:
        raise ValueError('Workflow 参数声明与引用不一致')
    return flow


def plain_flow(flow):
    """Preserve every old requirement in text; never mutate source records/hashes."""
    if is_plain(flow):
        return {k: copy.deepcopy(flow[k]) for k in ('name', 'goal', 'parameters', 'steps')}
    steps = []
    for step in flow['steps']:
        parts = [step['action']]
        if step.get('method'): parts.append('执行方法：' + step['method'])
        elif step.get('target'): parts.append('操作目标：' + step['target'])
        if step.get('check'): parts.append('关键判断：' + step['check'])
        steps.append('\n'.join(parts))
    if flow.get('conditions'):
        steps[0] = '使用条件：' + '；'.join(flow['conditions']) + '\n' + steps[0]
    if flow.get('completion'): steps[-1] += '\n最终完成判断：' + flow['completion']
    for f in flow.get('fallbacks', []):
        steps[-1] += '\n当 ' + f['when'] + '：' + f['action']
    return dict(name=flow['name'], goal=flow['goal'],
                parameters=[{k: p[k] for k in ('name', 'description')} for p in flow['parameters']], steps=steps)
