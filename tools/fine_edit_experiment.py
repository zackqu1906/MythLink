"""Experimental flat tasks; never imported by the application.

The model receives leaf rules only. Parent labels exist solely for evaluation.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

from proximic_ring.text_processing.automatic_prompts import EDIT_BASE_PROMPT, EDIT_COMBINATION_RULES
from proximic_ring.text_processing.prompts import SCREEN_CONTEXT_RULES
from proximic_ring.text_processing.routed_edit_prompts import replace_once, ROUTED_EDIT_PROMPT_MODULES
from proximic_ring.text_processing.edit_response import apply_edit_response
from proximic_ring.text_processing.edit_tool import EDIT_MODE_FULL
from proximic_ring.text_processing.routed_edit_processing import routed_edit_model_input
from proximic_ring.text_processing.processing_common import _text_output_budget

TAXONOMY_PATH = Path(__file__).parent / 'data/fine_edit_taxonomy.json'
TAXONOMY = json.loads(TAXONOMY_PATH.read_text())
TASKS = {t['id']: t for t in TAXONOMY['tasks']}
VERSION = TAXONOMY['version']


def available_tasks(target):
    return {k: t for k, t in TASKS.items() if target.strip() or not t['requires_source']}


def fine_prompt(target):
    tasks = available_tasks(target)
    base = replace_once(EDIT_BASE_PROMPT,
        'edit_plan 是上游路由选定的任务集合，具体边界和执行方式见随附的任务模块。',
        '编辑任务由你在本次请求内先行确定，具体边界和执行方式见下面的任务模块。')
    base = replace_once(base, '仅调用 submit_text_edit，modified_text 为最终完整正文；',
        '按本提示末尾规定的纯文本格式交付类别和最终完整正文；')
    combination = replace_once(EDIT_COMBINATION_RULES,
        '只提交最终完整正文，不输出操作计划或中间稿。',
        '正文部分只提交最终完整正文，不输出操作计划或中间稿。')
    # Retain the current application's character-grounding instructions verbatim.
    spelling_rules = ROUTED_EDIT_PROMPT_MODULES['modify'].split('\n', 1)[1]
    modules = '\n\n'.join('【' + t['label'] + '】' + t['rule'] +
        ('\n' + spelling_rules if k == 'spelling' else '') for k, t in tasks.items())
    return '\n\n'.join([
        '在一次回复中完成编辑任务分类和正文编辑。用户已经手动选择编辑，不做听写／编辑二分类。'
        '直接从下面平级任务中选择至少一项，再按所选任务完成编辑，不经过任何上级类别。'
        '回复先给类别，再给结果，不展示推理过程或解释。类别名称和最终正文必须相互一致。',
        '【类别判断规则】\n当前口述代表本次请求；可编辑原文是唯一可修改对象；聊天上文是只读 OCR 参考。'
        '结合完整口述、原文和相关上文理解编辑目标。口述可以简短、省略、含糊或用转述说明要求，'
        '自主推断合理的对象、范围和处理方式。按最终有效要求选择平级任务，可以组合多个任务；'
        '同一处操作的具体类别与概括类别不重复选，完成一个目标自然产生的增删换词不额外算任务。'
        '新拟稿件自身的语言、语气、篇幅和格式在该拟写任务内完成；'
        '若另有处理现有内容的独立要求，仍须选择对应任务。'
        '\n本次可用类别：' + '、'.join(t['label'] for t in tasks.values()),
        '【分类后执行规则】\n' + base,
        '【自主完成编辑】\n所有类别都按已经确认的编辑目标主动执行。对象、范围、措辞、目标语言、'
        '篇幅或格式细节不明确时，结合上下文作出合理判断并完成结果，可以发挥语言理解和创作能力。'
        '不要因为指令简短、信息省略或存在歧义就放弃操作、只返回原文或要求用户补充。'
        '用户明确指定的内容和限制优先，未指定的细节由你自主决定。',
        combination,
        '下面列出所有本次可用任务的判断与执行规则。只执行用户实际要求且你选中的任务；'
        '没有选中的模块不构成编辑授权，不要为了用上某模块而增加类别或扩大修改。\n' + modules,
        SCREEN_CONTEXT_RULES,
        '【最终返回格式】\n第一行：分类：中文类别名称\n第二行：正文：\n'
        '第三行起：修改后的完整正文，可包含多行。多个类别用顿号连接。'
        '不要 JSON，不要 Markdown 代码围栏，不要额外说明；正文自身要求的列表、表格等格式应保留。'
        '分类必须包含至少一个本次可用类别；只能使用上述平级任务名称，不输出上级类别。'
        '明确要求清空原文时允许正文为空，此时仍保留“正文：”标题。'
        '分类只有类别名称，不能夹带操作内容；正文中不要包含这两个用于分隔的外层标题。',
    ])


def parse_fine_response(raw, target):
    match = re.fullmatch(r'分类：([^\r\n]+)\r?\n正文：([\s\S]*)', raw) if isinstance(raw, str) else None
    if not match:
        raise ValueError('编辑结果缺少分类行或正文标记')
    labels, body = match.groups()
    lookup = {t['label']: k for k, t in available_tasks(target).items()}
    names = labels.split('、')
    if len(set(names)) != len(names) or any(n not in lookup for n in names):
        raise ValueError('细分类未知、重复或当前原文不可用')
    if body.startswith('\r\n'):
        body = body[2:]
    elif body.startswith('\n'):
        body = body[1:]
    return [lookup[n] for n in names], apply_edit_response(target, {'modified_text': body}, EDIT_MODE_FULL)


def process_fine(processor, case, settings):
    model_input = routed_edit_model_input(case['utterance'], case['target'], case['context'])
    raw = processor._request_chat(settings, system_prompt=fine_prompt(case['target']),
        user_content=json.dumps(model_input, ensure_ascii=False), temperature=0.0,
        max_tokens=_text_output_budget(settings), edit_tool=None,
        require_complete=True, preserve_whitespace=True)
    try:
        tasks, final = parse_fine_response(raw, case['target'])
    except Exception as exc:
        exc.model_output = raw
        raise
    return final, raw, model_input, tasks
