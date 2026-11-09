"""Plain-language, output-first history projection. Saved evidence stays intact."""
from __future__ import annotations

import json
from pathlib import Path

from ..text_processing.task_routing import EDIT_TASK_LABELS, LEGACY_EDIT_TASK_LABELS


def local_artifact(directory: Path, filename):
    if not isinstance(filename, str) or Path(filename).name != filename:
        return None
    path = directory / filename
    return path if not path.is_symlink() and path.is_file() else None


def _dict(value):
    return value if isinstance(value, dict) else {}


def _text(value):
    return value if isinstance(value, str) else ""


def _duration(milliseconds):
    if not isinstance(milliseconds, (int, float)) or milliseconds < 0:
        return ""
    return f"{milliseconds / 1000:.2f} 秒"


def _model(name, provider=""):
    value = str(name or provider or "").lower()
    for token, label in (("deepseek", "DeepSeek"), ("clef", "Clef"), ("doubao", "豆包"),
                         ("qwen", "Qwen"), ("whisper", "Whisper"), ("sensevoice", "SenseVoice"),
                         ("fun", "FunASR"), ("volcengine", "火山语音"), ("vision", "系统文字识别")):
        if token in value:
            return label
    return _text(name) or _text(provider)


def _error(value):
    """Never render exception payloads, protocol JSON or stack traces."""
    text = str(value or "").lower()
    for tokens, label in ((('超时', 'timeout'), '处理超时'),
                          (('权限', 'permission'), '未取得所需权限'),
                          (('api key', '401', '403'), '模型服务认证失败'),
                          (('429', '频率'), '模型服务繁忙'),
                          (('网络', '连接', 'connection'), '模型服务连接失败'),
                          (('取消', 'cancel'), '已取消'),
                          (('未完整', '截断', 'incomplete'), '模型输出不完整')):
        if any(token in text for token in tokens):
            return label
    return '未生成可用结果' if text else ''


def _context(value):
    value = _dict(value)
    if not value:
        return ''
    if value.get('status') == 'unavailable':
        return '未取得可用上文'
    lines = []
    if value.get('topic'):
        lines.append('主题：' + _text(value['topic']))
    if value.get('context'):
        lines.append(_text(value['context']))
    speakers = {'self': '自己', 'other': '对方', 'assistant': '助手', 'user': '用户'}
    for message in value.get('messages', []):
        message = _dict(message)
        if message.get('text'):
            speaker = speakers.get(message.get('speaker'), '')
            lines.append((speaker + '：' if speaker else '') + _text(message['text']))
    if value.get('draft'):
        lines.append('未发送草稿：' + _text(value['draft']))
    if value.get('uncertain'):
        lines.append('部分内容无法确定')
    return '\n'.join(lines)


def _input(value):
    if not isinstance(value, dict):
        return '未记录实际输入'
    parts = []
    utterance = value.get('utterance', value.get('instruction'))
    if isinstance(utterance, str):
        parts.append('口述：\n' + (utterance or '（空）'))
    source = value.get('editable_text', value.get('target_text'))
    if isinstance(source, str) and (source or 'editable_text' in value):
        parts.append('原文：\n' + (source or '（空）'))
    if 'screen_context' in value:
        parts.append('上文：\n' + (_context(value['screen_context']) or '未传入'))
    return '\n\n'.join(parts) or '未记录实际输入'


def _ocr_input(value):
    value = _dict(value)
    parts = []
    chat = _context(value.get('current_chat'))
    if chat:
        parts.append(chat)
    groups = value.get('columns', []) or [value]
    for group in groups:
        for line in _dict(group).get('lines', []):
            if isinstance(line, list) and len(line) >= 5 and isinstance(line[4], str):
                parts.append(line[4])
    drafts = [v for v in value.get('composer_ocr', []) if isinstance(v, str)]
    if drafts:
        parts.append('未发送区域：\n' + '\n'.join(drafts))
    return '\n'.join(parts) or '未记录实际输入'


def _tasks(plan):
    labels = {**EDIT_TASK_LABELS, **LEGACY_EDIT_TASK_LABELS}
    return '、'.join(labels[k] for k in _dict(plan).get('tasks', []) if k in labels)


def _request_output(request):
    status = request.get('status')
    if request.get('error') or status == 'failed':
        return _error(request.get('error')) or '未生成可用结果'
    if status == 'processing':
        return '正在处理…'
    candidate = request.get('candidate_text')
    if isinstance(candidate, str):
        return candidate if candidate else '（输出为空）'
    # Read older saved results without exposing the surrounding response schema.
    raw = _text(request.get('raw_output'))
    try:
        parsed = json.loads(raw)
        body = _dict(parsed).get('modified_text')
        if isinstance(body, str):
            return body or '（输出为空）'
    except (ValueError, TypeError):
        if raw.startswith('分类：') and '\n正文：' in raw:
            return raw.split('\n正文：', 1)[1].lstrip('\n') or '（输出为空）'
        if raw and not raw.lstrip().startswith(('{', '[')):
            return raw
    return '已取消' if status == 'cancelled' else '未保存可读输出'


def history_details(directory: Path, record: dict) -> dict:
    sections = []

    def section(key, title, output, *, model='', latency=None, status='', description='',
                input_text='', alternatives='', collapsed=False):
        sections.append({'key': key, 'title': title, 'text': output,
                         'metaText': ' · '.join(v for v in (model, _duration(latency), status) if v),
                         'description': description, 'inputText': input_text,
                         'alternativesText': alternatives, 'collapsed': collapsed})

    mode, asr, evidence = (_dict(record.get(key)) for key in ('mode', 'asr', 'screen_evidence'))
    capture, preparation = (_dict(record.get(key)) for key in ('screen_capture', 'context_preparation'))
    trace = _dict(mode.get('router_trace'))
    if not trace:
        try:
            trace = _dict(json.loads(mode.get('router_output') or '{}'))
        except (ValueError, TypeError):
            pass
    requests = [r for r in _dict(record.get('llm')).get('requests', []) if isinstance(r, dict)]
    # Most recent body result first. Other calls remain readable, without prompts
    # or branch transport details; duplicate branch results need no second copy.
    for index in reversed(range(len(requests))):
        request = requests[index]
        kind = request.get('task_type') or request.get('mode')
        routed = request.get('routing_mode') == 'manual_routed_edit' and kind == 'edit'
        title = '编辑分类与结果' if routed else {'edit': '编辑结果', 'dictation': '听写整理', 'polish': '润色结果'}.get(kind, '文字处理')
        state = {'completed': '已完成', 'processing': '处理中', 'failed': '未完成', 'cancelled': '已取消'}.get(request.get('status'), '')
        output = _request_output(request)
        other_outputs = []
        for branch in request.get('branches', []):
            branch = _dict(branch)
            text = branch.get('candidate_text')
            if (branch.get('validation') == 'valid' and isinstance(text, str)
                    and (text or '（输出为空）') != output and text not in other_outputs):
                other_outputs.append(text)
        alternatives = '\n\n'.join(f'其他结果 {i + 1}：\n' + (text or '（输出为空）') for i, text in enumerate(other_outputs))
        plan = request.get('edit_plan') or (mode.get('edit_plan') if routed else {})
        section('llm_' + str(index), title, output,
                model=_model(request.get('model'), request.get('provider')), latency=request.get('latency_ms'), status=state,
                description=('编辑类别：' + _tasks(plan)) if _tasks(plan) else '',
                input_text=_input(request.get('request_input', request.get('input'))), alternatives=alternatives)

    if mode.get('routing') == 'auto' and any(key in mode for key in ('router_request_id', 'router_output', 'task_type')):
        task = mode.get('task_type') or trace.get('task')
        labels = _tasks(mode.get('edit_plan') or trace.get('edit_plan'))
        result = '听写' if task == 'dictation' else '编辑' + ('：' + labels if labels else '') if task == 'edit' else '尚未取得判断结果'
        if mode.get('router_error') or trace.get('error'):
            result = _error(mode.get('router_error') or trace.get('error'))
        router_input = {'utterance': mode.get('router_input') or asr.get('final_text', '')}
        if 'router_target_text' in mode:
            router_input['editable_text'] = mode['router_target_text']
        if isinstance(trace.get('ocr_text', mode.get('router_ocr_text')), str):
            router_input['screen_context'] = {'context': trace.get('ocr_text', mode.get('router_ocr_text'))}
        elif 'screen_context' in mode:
            router_input['screen_context'] = mode['screen_context']
        section('decision', '听写／编辑判断', result, model=_model(mode.get('router_model') or trace.get('model')),
                latency=mode.get('router_latency_ms'), input_text=_input(router_input))

    if preparation:
        state = preparation.get('status')
        if not preparation.get('enabled', True) or state == 'disabled':
            output = '上文传入已关闭，本次未调用整理模型。'
        elif state == 'pending':
            output = '正在整理屏幕上文…'
        elif state == 'ok':
            output = _context(preparation.get('context')) or '未提取到有效上文'
        else:
            output = '未取得可用上文' + (' · ' + _error(preparation.get('error')) if preparation.get('error') else '')
        section('context_preparation', '屏幕上文整理', output,
                model=_model(preparation.get('model'), preparation.get('provider')), latency=preparation.get('latency_ms'),
                input_text=_ocr_input(preparation['input']) if isinstance(preparation.get('input'), dict) else '')

    if asr:
        source = '本次录音'
        duration = _duration(asr.get('audio_duration_ms') or _dict(record.get('audio')).get('duration_ms'))
        if duration:
            source += '（' + duration + '）'
        context = _dict(asr.get('context'))
        context_text = '\n'.join(_text(_dict(v).get('text')) for v in context.get('context_data', [])).strip()
        if context_text:
            source += '\n\n识别参考：\n' + context_text
        section('asr', '语音识别', _text(asr.get('final_text')) or (_error(asr.get('error')) if asr.get('error') else '未取得识别文字'),
                model=_model(asr.get('model'), asr.get('backend')), latency=asr.get('latency_ms'), input_text=source,
                description='直接使用识别结果，未调用听写整理模型。' if mode.get('dictation_cleanup_enabled') is False else '')

    raw_ocr = '\n'.join(_text(_dict(line).get('text')) for line in evidence.get('ocr_lines', [])).strip()
    if capture or raw_ocr:
        section('raw_ocr', '屏幕文字识别', raw_ocr or ('正在识别屏幕文字…' if capture.get('status') == 'pending' else '未取得屏幕文字'),
                model='系统文字识别', latency=capture.get('ocr_ms'), input_text='录音开始时的目标窗口截图', collapsed=True)

    screenshot = local_artifact(directory, evidence.get('screenshot_file'))
    if screenshot and screenshot.suffix != '.png':
        screenshot = None
    caption = '录音开始时的窗口截图' if screenshot else '截图采集中' if capture.get('status') == 'pending' else '本次未留存截图'
    outcome = _dict(record.get('outcome'))
    outcome_label = {'collecting': '采集中', 'recognized': '已识别', 'applied': '已写入', 'confirm': '已确认',
                     'cancelled': '已取消', 'undone': '已撤回', 'native_undo_sent': '已发送撤销',
                     'apply_failed': '写入失败', 'abandoned': '未完成', 'interrupted': '已中断'}.get(outcome.get('status'), '')
    warning = ''
    if trace.get('warning'):
        warning = '判断结果不一致，本次按听写处理。' if (mode.get('task_type') or trace.get('task')) == 'dictation' else '自动判断存在不确定性，请核对结果。'
    return {'summary': ' · '.join(v for v in ({'auto': '自动判断', 'manual_routed_edit': '手动＋编辑路由'}.get(mode.get('routing'), '手动转换'), outcome_label) if v),
            'warningText': warning, 'screenshotUrl': screenshot.resolve().as_uri() if screenshot else '',
            'screenshotText': caption, 'sections': sections}
