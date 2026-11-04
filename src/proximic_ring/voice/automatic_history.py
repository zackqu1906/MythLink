"""Read-only projection of automatic voice evidence for the history UI."""
from __future__ import annotations

import json
from pathlib import Path

from ..text_processing.task_routing import TASK_LABELS, EDIT_TASK_LABELS, LEGACY_EDIT_TASK_LABELS, LEGACY_EDIT_HINT_LABELS


def _probability_text(probabilities, labels):
    return " / ".join(f"{label} {probabilities[key]:.1%}" for key, label in labels.items()
                      if isinstance(probabilities.get(key), (int, float)))


def automatic_history_details(directory: Path, record: dict) -> dict:
    mode = record.get("mode", {})
    if mode.get("routing") != "auto":
        return {}
    trace = mode.get("router_trace")
    if not isinstance(trace, dict):
        try:
            trace = json.loads(mode.get("router_output") or "{}")
        except (ValueError, TypeError):
            trace = {}
    if not isinstance(trace, dict):
        trace = {}
    stages = {stage.get("stage"): stage for stage in trace.get("stages", []) if isinstance(stage, dict)}
    joint = stages.get("joint")
    is_joint = trace.get("classification_type") == "joint"
    first = joint or stages.get("intent", {})
    second = joint or stages.get("edit_details", {})
    first_answer = first.get("answers", {}).get("voice_intent", {})
    task = trace.get("task") or trace.get("raw_model_task") or mode.get("task_type")
    level1 = TASK_LABELS.get(task, "未取得判断结果")
    probabilities = first_answer.get("probabilities", trace.get("probabilities", {}))
    scores = _probability_text(probabilities, TASK_LABELS)
    if scores:
        level1 += "（" + scores + "）"
    if trace.get("decision_source") == "tied_dictation_fallback":
        level1 += "；概率并列，按听写处理"
    elif trace.get("decision_source") == "uncertain_dictation_fallback":
        level1 += "；分类不明确，回退听写"
    elif trace.get("decision_source") == "dictation_priority":
        level1 += "；听写优先"
    warning_text = str(trace.get("warning") or "")
    ignored_tasks = trace.get("ignored_tasks") or []
    plan = mode.get("edit_plan") or trace.get("edit_plan") or {}
    tasks, hints = plan.get("tasks", []), plan.get("hints", [])
    if is_joint and trace.get("consistency") == "rejected":
        level2 = "分类未通过校验，未执行"
    elif task == "dictation":
        level2 = ("按听写处理，未执行：" + "、".join(EDIT_TASK_LABELS.get(k, k) for k in ignored_tasks)
                  if ignored_tasks else "全部任务跳过" if is_joint else "已跳过：听写不进入二级分类")
    elif tasks:
        level2 = "、".join({**EDIT_TASK_LABELS, **LEGACY_EDIT_TASK_LABELS}.get(key, key) for key in tasks)
    elif second:
        level2 = "已返回，未通过编辑计划校验"
    else:
        level2 = "未取得任务结果"
    hint_text = "、".join(LEGACY_EDIT_HINT_LABELS.get(key, key) for key in hints) or (
        "无" if tasks else "不适用" if task == "dictation" else "未确认")
    if is_joint:
        hint_text = ""
    answer_lines = []
    for key, label in {**EDIT_TASK_LABELS, **LEGACY_EDIT_TASK_LABELS, **LEGACY_EDIT_HINT_LABELS}.items():
        answer = second.get("answers", {}).get(key)
        if isinstance(answer, dict):
            choice = "启用" if answer.get("choice") == "apply" else "不启用"
            if key in ignored_tasks:
                choice = "模型选启用，实际已忽略"
            scores = _probability_text(answer.get("probabilities", {}), {"apply": "启用", "skip": "不启用"})
            answer_lines.append(f"{label}：{choice}（{scores}）")
    timings = []
    for label, stage in ([("联合分类", joint or {})] if is_joint else [("一级", first), ("二级", second)]):
        if isinstance(stage.get("latency_s"), (int, float)):
            timings.append(f"{label} {stage['latency_s'] * 1000:.0f} ms")
    if mode.get("router_latency_ms") is not None:
        timings.append(f"路由总计 {mode['router_latency_ms']:.0f} ms")
    context = mode.get("screen_context") or {}
    evidence = record.get("screen_evidence") or {}
    image_url = ""
    filename = evidence.get("screenshot_file")
    # Only this interaction's own image may be loaded by the QML view.
    if isinstance(filename, str) and Path(filename).name == filename:
        path = directory / filename
        if path.suffix == ".png" and not path.is_symlink() and path.is_file():
            image_url = path.resolve().as_uri()
    screenshot_text = "当次目标窗口截图；图片仅本地保存，模型接收 OCR 文字"
    if not image_url:
        screenshot_text = "截图未留存" + ("：" + str(evidence["screenshot_error"]) if evidence.get("screenshot_error") else "（旧记录或本次未取得截图）")
    if evidence.get("captured_at"):
        from datetime import datetime
        try:
            captured = datetime.fromisoformat(evidence["captured_at"]).astimezone().strftime("%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            captured = str(evidence["captured_at"])
        screenshot_text += f" · {captured} · {evidence.get('image_width', '?')} × {evidence.get('image_height', '?')}"
    status = {"ok": "已取得上文", "empty": "未识别到有效上文", "unavailable": "上文不可用"}.get(context.get("status"), "未记录上文")
    if context.get("reason"):
        status += "：" + str(context["reason"])
    if context.get("capture_ms") is not None:
        status += f"；截图 {context['capture_ms']} ms / OCR {context.get('ocr_ms', 0)} ms"
    messages = context.get("messages", [])
    requests = [item for item in record.get("llm", {}).get("requests", []) if item.get("task_type")]
    last_request = requests[-1] if requests else {}
    if messages:
        status += "；路由上文最多 4 块 / 360 字符"
        status += "（路由已有返回）" if first else "（未取得有效路由返回）"
    if last_request:
        request_status = {"completed": "已完成", "processing": "已准备，尚无完成记录",
                          "failed": "失败", "cancelled": "已取消"}.get(last_request.get("status"), "状态未知")
        status += "；文本模型请求：" + request_status
    else:
        status += "；尚未创建文本模型请求"
    speakers = {"self": "自己", "other": "对方", "unknown": "未知发言人"}
    ocr_text = "\n".join(f"{speakers.get(m.get('speaker'), '未知发言人')}：{m.get('text', '')}" for m in messages)
    dispositions = {"passed_filter": "通过规则，之后仍受长度上限限制", "merged": "并入相邻文本块",
                    "empty_or_symbols_or_low_confidence": "过滤：空文字、符号或低置信度", "sidebar": "过滤：侧栏",
                    "header": "过滤：顶部", "conversation_title": "保留为会话标题",
                    "outside_history": "过滤：聊天记录区域外", "timestamp": "过滤：时间格式"}
    raw_text = "\n".join(f"[{dispositions.get(line.get('disposition'), '未知')}] {line.get('text', '')}"
                         for line in evidence.get("ocr_lines", []))
    model_name = trace.get("model") or mode.get("router_model", "")
    return {"level1Text": level1, "level2Text": level2, "hintText": hint_text,
            "probabilityText": "\n".join(answer_lines), "timingText": " · ".join(timings),
            "modelText": str(model_name), "errorText": str(mode.get("router_error") or trace.get("error") or ""),
            "warningText": warning_text, "warningLevel": "yellow" if warning_text else "",
            "warningBadge": ("听写优先 · 分类有冲突" if "dictation_with_tasks" in trace.get("warning_codes", [])
                             else "分类不明确 · 已回退听写") if warning_text else "",
            "contextText": status, "ocrText": ocr_text, "rawOcrText": raw_text,
            "screenshotUrl": image_url, "screenshotText": screenshot_text,
            "layout": context.get("layout") or {}, "routerState": trace.get("input_state", ""),
            "systemPrompt": last_request.get("system_prompt", ""),
            "modelInput": json.dumps(last_request["input"], ensure_ascii=False, indent=2) if last_request.get("input") else ""}
