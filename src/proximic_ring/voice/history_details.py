"""Plain-language, single-level history projection. Saved evidence stays intact."""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..text_processing.task_routing import EDIT_TASK_LABELS, LEGACY_EDIT_TASK_LABELS
from ..text_processing.model import ROUTED_EDIT_BOUNDARY, ROUTED_EDIT_VARIANT_LABELS, normalize_routed_edit_variant


def local_artifact(directory: Path, filename):
    if not isinstance(filename, str) or Path(filename).name != filename:
        return None
    path = directory / filename
    return path if not path.is_symlink() and path.is_file() else None


def _dict(value):
    return value if isinstance(value, dict) else {}


def _text(value):
    return value if isinstance(value, str) else ""


def _list(value):
    return value if isinstance(value, list) else []


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
    original = str(value or "").strip()
    text = original.lower()
    # Preserve the actual validation reason rather than collapsing every failure
    # to "no result". HTTP bodies and traces are still confined to the record.
    http = re.search(r'\bHTTP\s+(\d{3})\b', original, re.IGNORECASE)
    if http:
        code = int(http[1])
        reason = ('认证失败，请检查模型服务配置' if code in (401, 403) else
                  '请求过于频繁，请稍后重试' if code == 429 else
                  '模型服务内部错误' if code >= 500 else '模型服务拒绝了请求')
        return f'{reason}（HTTP {code}）'
    for tokens, label in ((('超时', 'timeout'), '处理超时'),
                          (('权限', 'permission'), '未取得所需权限'),
                          (('api key', '401', '403'), '模型服务认证失败'),
                          (('429', '频率'), '模型服务繁忙'),
                          (('网络', '连接', 'connection'), '模型服务连接失败'),
                          (('取消', 'cancel'), '已取消'),
                          (('未完整', '截断', 'incomplete'), '模型输出不完整')):
        if any(token in text for token in tokens):
            return label
    # Application-authored errors are short Chinese explanations. Do not pass
    # through appended exception payloads, URLs, credentials or tracebacks.
    explanation = re.split(r'[\r\n{\[]|Traceback|https?://', original, maxsplit=1)[0].rstrip(' ：:')
    if re.search(r'[\u4e00-\u9fff]', explanation) and not re.search(
            r'api.?key|authorization|bearer|token\s*[=:]|password|secret', explanation, re.IGNORECASE):
        return explanation[:600]
    return '未生成可用结果' if text else ''


def _context(value):
    value = _dict(value)
    if not value:
        return ''
    if value.get('status') == 'unavailable':
        return '未取得可用上文'
    lines = []
    if value.get('scene'):
        lines.append('场景：' + _text(value['scene']))
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
    if value.get('uncertainties'):
        lines.append('不确定信息：' + _text(value['uncertainties']))
    elif value.get('uncertain'):
        lines.append('部分内容无法确定')
    return '\n'.join(lines)


def _tasks(plan):
    labels = {**EDIT_TASK_LABELS, **LEGACY_EDIT_TASK_LABELS}
    return '、'.join(labels[k] for k in _dict(plan).get('tasks', []) if k in labels)


def _routed_classification(request):
    """Read model evidence even when body validation/incomplete output failed."""
    raw = _text(request.get('raw_output'))
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        payload = None
    if isinstance(payload, dict):
        raw = ''.join(_text(_dict(part).get('text'))
                      for item in _list(payload.get('output')) if isinstance(item, dict)
                      for part in _list(item.get('content')) if isinstance(part, dict)
                      and part.get('type') == 'output_text')
    match = re.match(r'^分类：([^\r\n]{1,160})(?:\r?\n|$)', raw)
    return match[1] if match else ''


def _edit_classification(request, *, routed, legacy_plan=None):
    if routed and request.get('routed_edit_variant') == ROUTED_EDIT_BOUNDARY:
        return '', ''
    labels = _tasks(request.get('edit_plan'))
    if labels:
        return labels, ''
    if routed:
        returned = _routed_classification(request)
        if returned:
            names = returned.split('、')
            valid = all(name in EDIT_TASK_LABELS.values() for name in names) and len(set(names)) == len(names)
            note = ('模型已返回类别，编辑结果未通过校验' if request.get('error') or request.get('status') == 'failed' else '')
            if not valid:
                note = ('旧版模型返回' if returned == '无可执行编辑' and request.get('status') == 'completed'
                        else '模型返回的类别不符合约定')
            return returned, note
        if request.get('status') == 'processing':
            return '正在分类…', ''
        # A record-level plan belongs only to the latest successful legacy call.
        if 'edit_plan' not in request and request.get('status') == 'completed' and not request.get('error'):
            labels = _tasks(legacy_plan)
            if labels:
                return labels, ''
        return '未取得分类' if request.get('error') or request.get('status') == 'failed' else '未记录分类', ''
    return '', ''


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
    mode, asr, evidence = (_dict(record.get(key)) for key in ('mode', 'asr', 'screen_evidence'))
    capture, preparation = (_dict(record.get(key)) for key in ('screen_capture', 'context_preparation'))
    trace = _dict(mode.get('router_trace'))
    if not trace:
        try:
            trace = _dict(json.loads(mode.get('router_output') or '{}'))
        except (ValueError, TypeError):
            pass
    requests = [r for r in _list(_dict(record.get('llm')).get('requests')) if isinstance(r, dict)]
    edit_record = (any((r.get('task_type') or r.get('mode')) == 'edit' for r in requests)
                   or any(mode.get(key) == 'edit' for key in ('user_intent', 'selected', 'task_type')))

    def item(key, text='', *, model='', latency=None, status='', **fields):
        return dict(key=key, text=text, metaText=' · '.join(v for v in (model, _duration(latency), status) if v),
                    **fields)

    def section(key, title, items=None, **fields):
        return dict(key=key, title=title, items=items or [], **fields)

    results, classifications = [], []
    # Preserve each attempt's own classification and failure. A later successful
    # retry must not lend its plan to an earlier failed call.
    for index in reversed(range(len(requests))):
        request = requests[index]
        kind = request.get('task_type') or request.get('mode')
        if edit_record and kind != 'edit':
            continue
        routed = request.get('routing_mode', mode.get('routing')) == 'manual_routed_edit' and kind == 'edit'
        classification, note = _edit_classification(
            request, routed=routed, legacy_plan=mode.get('edit_plan') if index == len(requests) - 1 else None)
        model = _model(request.get('model'), request.get('provider'))
        latency = request.get('latency_ms')
        state = {'completed': '已完成', 'processing': '处理中', 'failed': '未完成', 'cancelled': '已取消'}.get(request.get('status'), '')
        failed = bool(request.get('error') or request.get('status') == 'failed')
        key = 'llm_' + str(index)
        title = '编辑请求 ' + str(index + 1) if edit_record and len(requests) > 1 else ''
        if routed and request.get('routed_edit_variant') == ROUTED_EDIT_BOUNDARY:
            classifications.append(item(key, '无分类边界 v1 · 直接生成正文', title=title, model=model))
        elif classification:
            classifications.append(item(key, title=title, model=model,
                                        classificationText=classification, classificationNote=note))
        output = _request_output(request)
        other_outputs = []
        for branch in _list(request.get('branches')):
            branch = _dict(branch)
            text = branch.get('candidate_text')
            if (branch.get('validation') == 'valid' and isinstance(text, str)
                    and (text or '（输出为空）') != output and text not in other_outputs):
                other_outputs.append(text)
        alternatives = '\n\n'.join(f'其他结果 {i + 1}：\n' + (text or '（输出为空）') for i, text in enumerate(other_outputs))
        results.append(item(key, output, title=title, model=model, latency=latency, status=state,
                            errorText=(_error(request.get('error') or request.get('end_reason')) or '未记录具体失败原因') if failed else '',
                            outputVisible=not failed, alternativesText=alternatives,
                            description=' · '.join(v for v in (
                                '重新执行的编辑请求' if kind == 'edit' and request.get('reexecuted') else '',
                                ROUTED_EDIT_VARIANT_LABELS[normalize_routed_edit_variant(request.get('routed_edit_variant'))] if routed else '') if v)))

    route = []
    if mode.get('routing') == 'auto':
        task = mode.get('task_type') or trace.get('task')
        labels = _tasks(mode.get('edit_plan') or trace.get('edit_plan'))
        result = '听写' if task == 'dictation' else '编辑' + ('：' + labels if labels else '') if task == 'edit' else '尚未取得判断结果'
        if mode.get('router_error') or trace.get('error'):
            result = '路由判断失败：' + _error(mode.get('router_error') or trace.get('error'))
        route.append(item('decision', result, model=_model(mode.get('router_model') or trace.get('model')),
                          latency=mode.get('router_latency_ms')))
    if classifications:
        route.extend(classifications)
    elif edit_record and mode.get('routing') != 'auto':
        if mode.get('routing') == 'manual_routed_edit':
            route.append(item('edit_classification', classificationText='未取得分类',
                              classificationNote='本记录没有编辑模型调用结果'))
        else:
            route.append(item('decision', '手动选择编辑，未进行自动路由判断。'))

    state = preparation.get('status')
    if not preparation:
        prepared_text = '本次未记录 LLM 整理的 OCR。'
    elif not preparation.get('enabled', True) or state == 'disabled':
        prepared_text = '上文传入已关闭，本次未调用整理模型。'
    elif state == 'pending':
        prepared_text = '正在整理屏幕上文…'
    elif state == 'ok':
        prepared_text = _context(preparation.get('context')) or '未提取到有效上文'
    else:
        prepared_text = '未取得可用上文' + (' · ' + _error(preparation.get('error')) if preparation.get('error') else '')
    context_section = section('context_preparation', 'LLM 整理的 OCR', [item(
        'context_preparation', prepared_text, model=_model(preparation.get('model'), preparation.get('provider')),
        latency=preparation.get('latency_ms'))])

    screenshot = local_artifact(directory, evidence.get('screenshot_file'))
    if screenshot and screenshot.suffix != '.png':
        screenshot = None
    screenshot_url = screenshot.resolve().as_uri() if screenshot else ''
    caption = '录音开始时的窗口截图' if screenshot else '截图采集中' if capture.get('status') == 'pending' else '本次未留存截图'
    screenshot_section = section('screenshot', 'OCR 截图', kind='image', imageUrl=screenshot_url, caption=caption,
        errorText=_error(capture.get('reason')) if capture.get('status') == 'unavailable' else '')

    outcome = _dict(record.get('outcome'))
    outcome_label = {'collecting': '采集中', 'recognized': '已识别', 'applied': '已写入', 'confirm': '已确认',
                     'cancelled': '已取消', 'undone': '已撤回', 'native_undo_sent': '已发送撤销',
                     'apply_failed': '写入失败', 'abandoned': '未完成', 'interrupted': '已中断'}.get(outcome.get('status'), '')
    latest = requests[-1] if requests else {}
    latest_error = _error(latest.get('error') or latest.get('end_reason'))
    if (edit_record and (latest.get('task_type') or latest.get('mode')) == 'edit'
            and (latest.get('error') or latest.get('status') == 'failed')
            and outcome.get('status') in {'collecting', 'recognized', 'applied', 'confirm'}):
        outcome_label = '编辑未完成'
    outcome_error = _error(outcome.get('error'))
    if not outcome_error and outcome.get('status') in {'apply_failed', 'abandoned', 'interrupted'}:
        feedback = next((_dict(entry) for entry in reversed(_list(record.get('feedback')))
                         if _dict(entry).get('action') == outcome.get('status') and _dict(entry).get('error')), {})
        outcome_error = _error(feedback.get('error'))
    if outcome_error == latest_error:
        outcome_error = ''
    warning = ''
    if trace.get('warning'):
        warning = '判断结果不一致，本次按听写处理。' if (mode.get('task_type') or trace.get('task')) == 'dictation' else '自动判断存在不确定性，请核对结果。'

    sections = []
    if route:
        sections.append(section('decision', '路由判断', route, warningText=warning))
    if edit_record:
        if not results:
            results = [item('edit_result', '本次未取得编辑结果。')]
        sections.append(section('edit_result', '编辑结果与失败信息', results,
                                statusText=outcome_label, errorText=outcome_error))
    else:
        if results:
            sections.append(section('processing', '文字处理结果', results,
                                    statusText=outcome_label, errorText=outcome_error))
        if asr:
            sections.append(section('asr', '语音识别', [item('asr', _text(asr.get('final_text')) or
                (_error(asr.get('error')) if asr.get('error') else '未取得识别文字'),
                model=_model(asr.get('model'), asr.get('backend')), latency=asr.get('latency_ms'))],
                errorText=outcome_error if not results else ''))
        elif not results and outcome_error:
            sections.append(section('outcome', '失败信息', errorText=outcome_error))
        raw_ocr = '\n'.join(_text(_dict(line).get('text')) for line in _list(evidence.get('ocr_lines'))).strip()
        if capture or raw_ocr:
            sections.append(section('raw_ocr', '屏幕文字识别', [item('raw_ocr', raw_ocr or
                ('正在识别屏幕文字…' if capture.get('status') == 'pending' else
                 _error(capture.get('reason')) or '未取得屏幕文字'),
                model='系统文字识别', latency=capture.get('ocr_ms'))]))
    sections.append(screenshot_section)
    if preparation or edit_record:
        sections.append(context_section)
    return {'summary': ' · '.join(v for v in ({'auto': '自动判断', 'manual_routed_edit': '手动＋编辑路由'}.get(mode.get('routing'), '手动转换'), outcome_label) if v),
            'outcomeError': outcome_error, 'outcomeTitle': outcome_label or '本次处理未完成',
            'warningText': warning, 'screenshotUrl': screenshot_url, 'screenshotText': caption, 'sections': sections}
