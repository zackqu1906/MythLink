"""Task semantics are model inferred; observed tools are recorded separately."""

TASK_TYPES = {
    'question_answering': '问答', 'research': '检索研究', 'writing': '写作',
    'analysis': '分析', 'calculation': '计算', 'coding': '代码开发',
    'file_processing': '文件处理', 'app_operation': '应用操作',
    'communication': '沟通发送', 'other': '其他',
}
OUTPUT_KINDS = {
    'answer': '文字回答', 'document': '文稿', 'code': '代码', 'data': '数据',
    'file': '文件', 'app_state': '应用状态', 'external_action': '外部动作', 'other': '其他',
}
COMPACT_FORMATS = {'desktop-workflow-v5', 'agent-workflow-v6'}


def validate_profile(value, *, with_sources=False):
    keys = {'task_types', 'output_kinds'} | ({'sources'} if with_sources else set())
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError('任务分类字段不符合约定')
    for key, vocabulary in (('task_types', TASK_TYPES), ('output_kinds', OUTPUT_KINDS)):
        entries = value[key]
        if (not isinstance(entries, list) or not 1 <= len(entries) <= len(vocabulary)
                or any(not isinstance(x, str) or x not in vocabulary for x in entries)
                or len(set(entries)) != len(entries)):
            raise ValueError('任务分类标签非法、为空或重复')
    return value


def profile_text(profile):
    if not profile:
        return '任务分类：尚未后台分类'
    return ('任务类型（模型推断）：' + '、'.join(TASK_TYPES.get(x, x) for x in profile['task_types'])
            + '；输出：' + '、'.join(OUTPUT_KINDS.get(x, x) for x in profile['output_kinds']))


def compact_profile(profile):
    return {key: list(profile[key]) for key in ('task_types', 'output_kinds')}
