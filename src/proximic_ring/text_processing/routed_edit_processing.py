"""Third mode only: manual edit intent, one DeepSeek classification/body request."""
from __future__ import annotations

import json
import re

from .edit_response import apply_edit_response
from .edit_tool import EDIT_MODE_FULL
from .model import LLM_PROVIDER_VOLCENGINE, validate_edit_target_text
from .processing_common import LLMResponseProcessingError, _text_output_budget
from .routed_edit_prompts import NO_TASK, single_pass_prompt
from .task_routing import EDIT_TASK_LABELS, EditPlan, available_edit_tasks


def routed_edit_model_input(utterance, editable_text, screen_context=None):
    return {"utterance": str(utterance or "").strip(), "editable_text": editable_text,
            "screen_context": screen_context or {}}


def parse_routed_edit_response(raw, target):
    """Accept the two observed body layouts, never repair unknown categories."""
    match = re.fullmatch(r"分类：([^\r\n]+)\r?\n正文：([\s\S]*)", raw) if isinstance(raw, str) else None
    if not match:
        raise ValueError("编辑结果缺少分类行或正文标记")
    labels, body = match.groups()
    if body.startswith("\r\n"):
        body = body[2:]
    elif body.startswith("\n"):
        body = body[1:]
    if labels == NO_TASK:
        if body != target:
            raise ValueError("无可执行编辑时模型改变了原文")
        return None, body
    inverse = {label: key for key, label in EDIT_TASK_LABELS.items()}
    names = labels.split("、")
    if any(name not in inverse for name in names):
        raise ValueError("模型返回了约定六类之外的编辑类别")
    plan = EditPlan(tuple(inverse[name] for name in names))
    if not set(plan.tasks) <= set(available_edit_tasks(target)):
        raise ValueError("空原文不能选择修改任务")
    # Reuse full-body validation (including prompt leak checks), without
    # requiring the model to emit JSON or making a second model request.
    body = apply_edit_response(target, {"modified_text": body}, EDIT_MODE_FULL)
    return plan, body


class RoutedEditTextProcessingMixin:
    def process_routed_edit_with_trace(self, text, settings, target_text, screen_context=None):
        settings.validate()
        if (not settings.enabled or settings.provider != LLM_PROVIDER_VOLCENGINE
                or not settings.model.lower().startswith("deepseek")):
            raise ValueError("手动＋编辑路由需要在服务设置中选择火山方舟 DeepSeek 模型")
        raw = str(text or "").strip()
        if not raw:
            raise ValueError("本次编辑口述为空")
        target = validate_edit_target_text(target_text, allow_empty=True)
        model_input = routed_edit_model_input(raw, target, screen_context)
        # Detach the exact serialized input from mutable capture/evidence data.
        model_input = json.loads(json.dumps(model_input, ensure_ascii=False))
        output = ""
        try:
            output = self._request_chat(
                settings, system_prompt=single_pass_prompt(target),
                user_content=json.dumps(model_input, ensure_ascii=False),
                temperature=0.0, max_tokens=_text_output_budget(settings), edit_tool=None,
                require_complete=True, preserve_whitespace=True,
            )
            plan, body = parse_routed_edit_response(output, target)
        except Exception as exc:
            failure = LLMResponseProcessingError(str(exc), self._model_output_text(output) if output else str(getattr(exc, "model_output", "")))
            failure.model_input = model_input
            raise failure from exc
        return body, output, model_input, plan
