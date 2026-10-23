"""Recover only historical outcomes disproved by their own application events."""
from copy import deepcopy


# These messages describe activation before a new input can be applied. A late
# startup callback used to be attached to the last ASR session by the UI host.
STARTUP_ERRORS = frozenset({
    "输入法未连接到当前文本框，请重新点入文本框",
    "输入法未确认开始听写，请重新点入文本框",
    "请先点入可编辑的文本框",
    "请手动选中 ProxiMic 语音输入法并进入文本框",
    "请手动选中 Mythlink 语音输入法并进入文本框",
    "输入法已连接 · 已就绪",
    "正在切换输入法",
    "已切换应用，本句已取消",
    "无法重新激活原文本应用",
    "输入法交接未完成，当前输入框暂未提供一致的输入状态",
})
TERMINAL_ACTIONS = {"applied", "undone", "cancelled", "native_undo_sent"}


def recover_history_outcome(record, events, *, repaired_at):
    """Return a repaired copy, or None when success/cancellation is unproven.

    Never infer success from recognized text or a model candidate. A genuine
    failed edit, readback, undo or commit remains a failure. Keep original events
    and the replaced summary so repairs can be audited and reversed.
    """
    if record.get("outcome", {}).get("status") not in {"apply_failed", "abandoned"}:
        return None
    terminal = next((i for i in range(len(events)-1, -1, -1)
                     if events[i].get("type") == "application"
                     and events[i].get("action") in TERMINAL_ACTIONS), None)
    if terminal is None:
        return None
    applied = events[terminal]
    failures = []
    for event in events[terminal+1:]:
        kind = event.get("type")
        if kind in {"mode_intent", "llm_started"}:
            return None  # A newer edit really did start after that application.
        if kind not in {"application", "feedback"}:
            continue
        startup = (kind == "application" and event.get("action") == "apply_failed"
                   and event.get("method") == "input_method" and not event.get("restored_dictation")
                   and event.get("error") in STARTUP_ERRORS)
        cancelled_cleanup = (kind == "feedback" and event.get("action") == "abandoned"
                             and applied["action"] in {"cancelled", "undone", "native_undo_sent"}
                             and event.get("error") == "utterance cancelled by user")
        if not (startup or cancelled_cleanup):
            return None
        failures.append(event)
    if not failures:
        return None
    result = deepcopy(record)
    action, mode = applied["action"], str(applied.get("mode") or "")
    result.setdefault("history_repairs", []).append({
        "version": 1, "reason": "unrelated_startup_or_cancel_cleanup",
        "repaired_at": repaired_at, "source_event_at": applied.get("occurred_at"),
        "ignored_event_count": len(failures), "previous_outcome": deepcopy(record["outcome"]),
        "previous_mode": {key: record.get("mode", {}).get(key) for key in (
            "selected", "final_applied", "final_applied_at", "training_target", "training_target_source")},
        "previous_target": deepcopy(record.get("target", {})),
    })
    result["outcome"].update({
        "status": action, "error": applied.get("error"), "final_text": applied.get("final_text"),
        "application_method": applied.get("method", "input_method"),
        "accepted": False if action in {"undone", "cancelled"} else None,
        "acceptance_strength": "pending_undo" if action == "applied" else
                               "unverified_undo" if action == "native_undo_sent" else "explicit",
    })
    if mode:
        result["mode"]["selected"] = mode
    successful_mode = mode if action == "applied" and mode in {"dictation", "edit"} else ""
    result["mode"].update({
        "final_applied": successful_mode,
        "final_applied_at": applied.get("occurred_at") if successful_mode else None,
        "training_target": successful_mode or None,
        "training_target_source": "successful_application" if successful_mode else None,
    })
    for key, source in (("application", "application"), ("key", "target_key")):
        if applied.get(source):
            result.setdefault("target", {})[key] = applied[source]
    result["updated_at"] = repaired_at
    return result
