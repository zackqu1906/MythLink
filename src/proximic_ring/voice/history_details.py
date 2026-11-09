"""On-demand, lossless display of a saved voice interaction and its evidence."""
from __future__ import annotations

import json
from pathlib import Path


def local_artifact(directory: Path, filename):
    if not isinstance(filename, str) or Path(filename).name != filename:
        return None
    path = directory / filename
    return path if not path.is_symlink() and path.is_file() else None


def history_details(directory: Path, record: dict) -> dict:
    from .automatic_history import automatic_history_details
    sections = []

    def section(key, title, value, description=""):
        sections.append({"key": key, "title": title, "description": description,
                         "text": json.dumps(value, ensure_ascii=False, indent=2) if value is not None else "未记录"})

    mode = record.get("mode") or {}
    evidence = record.get("screen_evidence") or {}
    capture = record.get("screen_capture") or {}
    context = (record.get("asr") or {}).get("context") or {}
    automatic = automatic_history_details(directory, record)
    screenshot = local_artifact(directory, evidence.get("screenshot_file"))
    if screenshot and screenshot.suffix != ".png":
        screenshot = None
    caption = "当次目标窗口截图"
    if not screenshot:
        caption = "截图未留存：" + str(evidence.get("screenshot_error") or capture.get("reason") or
                                   ("正在采集" if capture.get("status") == "pending" else "旧记录或本次未取得截图"))
    elif evidence.get("captured_at"):
        caption += f" · {evidence['captured_at']} · {evidence.get('image_width', '?')} × {evidence.get('image_height', '?')}"

    if context:
        context_status = f"ASR 上下文：{context.get('sent_char_count', 0)} 字符 · {context.get('status', 'unknown')}"
        if context.get("reason"):
            context_status += " · " + str(context["reason"])
        if not context.get("context_data"):
            context_status += "（未保存有效上下文文字）"
    else:
        context_status = "ASR 上下文：未记录"

    section("screen_capture", "截图与 OCR 采集", {"capture": capture or None,
            "evidence": {key: value for key, value in evidence.items() if key not in {"ocr_lines", "ocr_filter_lines"}}},
            "原始采集独立于任务判断；状态、目标、时间和图像尺寸均保留。")
    section("raw_ocr", "原始 OCR · 文字、位置与置信度", evidence.get("ocr_lines"),
            "整张目标窗口的识别行，未做正文过滤。" if evidence.get("ocr_scope") == "target_window"
            else "未取得识别行；请查看采集状态和失败原因。" if capture
            else "旧记录展示当时留存的识别行，采集范围可能仅为聊天区域。")
    section("context_preparation", "OCR 上文整理 · 开关、输入与结果", record.get("context_preparation"),
            "DeepSeek 根据分区 OCR 提炼只读上文；开启时下游等待，关闭时不传入。原始截图和 OCR 独立保留。")
    section("asr_context", "ASR 上下文", context or None,
            "实际保存的上下文片段与来源。状态为 sent 也可能没有有效文字。")
    section("input_context", "录音开始时的输入上下文", record.get("input_context"),
            "保存当次输入法已返回的文字、版本与范围；缺失字段表示当时未取得。")
    if mode.get("routing") == "auto":
        sections.append({"key": "decision", "title": "自动判断结果", "description": "",
                         "text": "\n".join(automatic.get(key, "") for key in
                                             ("level1Text", "level2Text", "probabilityText", "timingText", "errorText")
                                             if automatic.get(key))})
        legacy_filter = evidence.get("ocr_lines") if any("disposition" in line for line in evidence.get("ocr_lines", [])) else None
        section("ocr_filter", "自动模式 · OCR 过滤结果", evidence.get("ocr_filter_lines", legacy_filter),
                "保留、合并、排除原因；原始 OCR 不被覆盖。")
    if mode.get("routing") == "manual_routed_edit":
        from ..text_processing.task_routing import EDIT_TASK_LABELS
        plan = mode.get("edit_plan") or {}
        names = "、".join(EDIT_TASK_LABELS.get(k, k) for k in plan.get("tasks", []))
        section("decision", "手动＋编辑路由", {
            "用户意图": mode.get("user_intent", mode.get("selected")),
            "编辑类别": names or ("无可执行编辑" if mode.get("edit_routing_type") and not mode.get("edit_routing_error") else "未取得"),
            "编辑处理总耗时_ms（含剩余上文等待）": mode.get("edit_routing_latency_ms"),
            "错误": mode.get("edit_routing_error"),
        }, "手势决定听写或编辑；编辑分类与正文在同一次 DeepSeek 请求中返回，没有分类概率或独立分类耗时。")
        section("ocr_filter", "编辑路由 · OCR 过滤结果", evidence.get("ocr_filter_lines"),
                "与自动模式共用 OCR 过滤规则；原始文字保留在上方。")
    section("mode", "自动判断、任务概率与编辑计划" if mode.get("routing") == "auto" else "手动选择与模式反馈", mode)
    section("asr", "语音识别结果", {key: value for key, value in (record.get("asr") or {}).items() if key != "context"})
    section("llm", "文字处理 · 输入、提示词、输出与候选分支", record.get("llm"),
            "空 requests 表示没有正文模型请求；失败或取消时以各请求状态为准。")
    section("outcome", "写回结果与接受状态", record.get("outcome"))
    section("feedback", "用户反馈与人工修正", {"feedback": record.get("feedback", []),
            "manual_results": record.get("manual_results", [])})
    section("capture", "设备、音频与目标应用", {key: record.get(key) for key in ("capture", "audio", "target")})
    section("sensors", "近场检测与 IMU", {key: record.get(key) for key in ("near_field", "imu")},
            "null 为未记录，不能据此推断评分或传感器结果。")
    section("associations", "记录关联", {key: record.get(key, []) for key in ("association_ids", "association_memberships")})
    shown = {"screen_capture", "screen_evidence", "context_preparation", "input_context", "mode", "asr", "llm", "outcome", "feedback",
             "manual_results", "capture", "audio", "target", "near_field", "imu", "association_ids", "association_memberships"}
    section("metadata", "记录标识与其他字段", {key: value for key, value in record.items() if key not in shown})
    for key, title, filename in (
        ("asr_updates", "ASR 逐次识别记录", (record.get("asr") or {}).get("updates_file", "asr_updates.jsonl")),
        ("events", "操作与运行事件 · 按记录顺序", record.get("events_file", "events.jsonl")),
        ("imu_samples", "IMU 原始样本", (record.get("imu") or {}).get("samples_file")),
    ):
        path = local_artifact(directory, filename)
        try:
            text = path.read_text(encoding="utf-8") if path else "未记录"
        except (OSError, UnicodeError) as exc:
            text = f"读取失败：{exc}"
        sections.append({"key": key, "title": title, "description": "完整保留文件中的逐条数据。", "text": text or "无记录"})
    outcome = str(record.get("outcome", {}).get("status", ""))
    outcome_label = {"collecting": "采集中", "recognized": "已识别", "applied": "已写入", "confirm": "已确认",
                     "cancelled": "已取消", "undone": "已撤回", "native_undo_sent": "已发送撤销",
                     "apply_failed": "写入失败", "abandoned": "未完成", "interrupted": "已中断"}.get(outcome, outcome)
    return {"summary": {"auto": "自动判断", "manual_routed_edit": "手动＋编辑路由"}.get(mode.get("routing"), "手动判断") + " · " + outcome_label,
            "warningText": automatic.get("warningText", ""),
            "asrContextStatus": context_status, "screenshotUrl": screenshot.resolve().as_uri() if screenshot else "",
            "screenshotText": caption, "sections": sections}
