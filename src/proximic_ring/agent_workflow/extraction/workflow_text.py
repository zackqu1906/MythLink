"""Deterministic natural-language view of a compact workflow; no inference."""

TEXT_VERSION = 'desktop-steps-1'


def natural_workflow(flow):
    """Keep every action, target, check and conditional branch in source order.

    Placeholders remain intact here. The caller substitutes them once when
    preparing a run, so user input containing braces cannot become a template.
    """
    lines = [f"流程：{flow['name']}", f"目标：{flow['goal']}"]
    if flow['parameters']:
        lines += ['', '输入参数：']
        lines += [f"- {p['name']}：{p['description']}" for p in flow['parameters']]
    if flow.get('conditions'):
        lines += ['', '使用条件（执行时确认）：']
        lines += ['- ' + condition for condition in flow['conditions']]
    lines += ['', '操作步骤：']
    for number, step in enumerate(flow['steps'], 1):
        if isinstance(step, str):
            lines.append(f'{number}. {step}')
            continue
        if 'completion' in flow:
            lines.append(f"{number}. {step['action']}")
            if step.get('check'):lines.append('   关键判断：'+step['check'])
            continue
        method = ('执行方法：' + step['method']) if 'method' in step else ('操作目标：' + step['target'])
        lines += [f"{number}. {step['action']}", f"   {method}",
                  f"   到位条件（执行时核验）：{step.get('check', '未提供')}" ]
    if flow.get('completion'):lines+=['','最终完成判断：'+flow['completion']]
    if flow.get('fallbacks'):
        lines += ['', '异常处理（仅在相应条件出现时使用）：']
        lines += [f"- 当 {branch['when']}：{branch['action']}" for branch in flow['fallbacks']]
    return '\n'.join(lines) + '\n'


def natural_document(flows):
    return '\n\n'.join(natural_workflow(flow).rstrip() for flow in flows) + '\n'
