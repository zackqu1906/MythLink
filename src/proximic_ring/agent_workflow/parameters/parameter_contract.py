"""One launch-time parameter contract for recommendation, native UI and execution.

New definitions can declare required:false. Older immutable definitions are read
without rewriting their hashes; only explicit optional/omission wording is used.
An omitted value stays empty in the ticket/history, never a synthetic default.
"""
import re

VERSION = 'parameter-contract-2'


def required(parameter):
    if 'required' in parameter:
        if type(parameter['required']) is not bool:
            raise ValueError('参数 required 必须是布尔值')
        return parameter['required']
    text = parameter.get('description', '').strip()
    if re.search(r'必填|必须提供|不能留空|不得省略|\brequired\b', text, re.I):
        return True
    # Deliberately not a search for the word 可: "可填写..." alone is not optional.
    optional = (re.match(r'^(?:[（(]?\s*可选|optional\b)', text, re.I)
                or re.search(r'(?:可以|可)不填|允许留空|可省略|未指定时|未提供时|留空时', text))
    return not bool(optional)


def missing_required(flow, values):
    return [p['name'] for p in flow['parameters']
            if required(p) and (not isinstance(values.get(p['name']), str) or not values[p['name']].strip())]


def bind_parameters(flow, values):
    names = {p['name'] for p in flow['parameters']}
    if (not isinstance(values, dict) or set(values) - names
            or any(not isinstance(v, str) for v in values.values())):
        raise ValueError('参数名称或值格式不正确')
    missing = missing_required(flow, values)
    if missing:
        raise ValueError('请填写必填参数：' + '、'.join(missing))
    return {p['name']: values.get(p['name'], '') if values.get(p['name'], '').strip() else ''
            for p in flow['parameters']}


def render_parameters(text, values):
    # Values containing template syntax are data; substitution happens once.
    return re.sub(r'\{\{([a-z][a-z0-9_]*)\}\}',
                  lambda m: values[m[1]] if values[m[1]] else '（未指定）', text)


def omission_guidance(flow, values):
    missing = [p for p in flow['parameters'] if not values[p['name']]]
    if not missing:
        return ''
    return ('\n\n本次未指定的可选输入：\n' +
            '\n'.join('- ' + p['name'] + '：' + p['description'] for p in missing) +
            '\n“未指定”是缺省标记，不是实际输入。按流程已有的缺省或推导方法处理；'
            '如果没有明确处理方式且会影响任务目标，再询问用户。不要把该标记用于搜索、命名或写入。')
