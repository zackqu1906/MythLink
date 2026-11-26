"""Manual edit intent with a selectable editor and output protocol."""
from __future__ import annotations

import json
import re

from .edit_response import apply_edit_response
from .edit_tool import EDIT_MODE_FULL, FULL_TEXT_EDIT_TOOL
from .model import (ROUTED_EDIT_HYBRID, ROUTED_EDIT_ORIGINAL, validate_edit_target_text,
                    ROUTED_EDIT_BODY_VARIANTS, normalize_routed_edit_variant)
from .processing_common import LLMResponseProcessingError, _text_output_budget
from .qwen_thinking import edit_thinking_budget
from .routed_edit_prompts import routed_edit_prompt
from .task_routing import EDIT_TASK_LABELS, EditPlan


def routed_edit_model_input(utterance, editable_text, screen_context=None, saved_context=None):
    result = {"utterance": str(utterance or "").strip(), "editable_text": editable_text,
              "screen_context": screen_context or {}}
    if saved_context is not None:
        from ..screenshots.context import saved_context_text
        result["saved_context"] = saved_context_text(saved_context)
    return result


def parse_routed_edit_response(raw, target):
    """Accept the two observed body layouts, never repair unknown categories."""
    match = re.fullmatch(r"分类：([^\r\n]+)\r?\n(?:[ \t]*\r?\n)*正文：([\s\S]*)", raw) if isinstance(raw, str) else None
    if not match:
        raise ValueError("编辑结果缺少分类行或正文标记")
    labels, body = match.groups()
    if body.startswith("\r\n"):
        body = body[2:]
    elif body.startswith("\n"):
        body = body[1:]
    if labels.strip() == "无可执行编辑":
        raise ValueError("用户已确认编辑，但模型未选择编辑类别")
    inverse = {label: key for key, label in EDIT_TASK_LABELS.items()}
    names = [name.strip() for name in labels.split("、")]
    if any(name not in inverse for name in names):
        raise ValueError("模型返回了约定六类之外的编辑类别")
    plan = EditPlan(tuple(inverse[name] for name in names))
    # Reuse full-body validation (including prompt leak checks), without
    # requiring the model to emit JSON or making a second model request.
    body = apply_edit_response(target, {"modified_text": body}, EDIT_MODE_FULL)
    return plan, body


class RoutedEditTextProcessingMixin:
    def process_routed_edit_with_trace(self, text, settings, target_text, screen_context=None,
                                      *, variant=ROUTED_EDIT_HYBRID, saved_context=None,
                                      cancelled=lambda: False, edit_steps=None, edit_hotwords=(), edit_pipeline=None):
        settings.validate()
        if not settings.enabled or not settings.supports_routed_edit:
            raise ValueError("手动＋编辑路由需要在服务设置中选择百炼 Qwen 或火山方舟 DeepSeek 模型")
        raw = str(text or "").strip()
        if not raw:
            raise ValueError("本次编辑口述为空")
        target = validate_edit_target_text(target_text, allow_empty=True)
        variant = normalize_routed_edit_variant(variant)
        boundary_only = variant in ROUTED_EDIT_BODY_VARIANTS
        model_input = routed_edit_model_input(raw, target, screen_context, saved_context)
        # Detach the exact serialized input from mutable capture/evidence data.
        model_input = json.loads(json.dumps(model_input, ensure_ascii=False))
        if variant == ROUTED_EDIT_HYBRID:
            from .hybrid_edit.processing import process_hybrid_edit
            from .hybrid_edit.vocabulary import use_edit_hotwords
            with use_edit_hotwords(edit_hotwords):
                return process_hybrid_edit(self, settings, model_input, cancelled, edit_steps, edit_pipeline)
        output = ""
        try:
            structured = variant == ROUTED_EDIT_ORIGINAL
            options = {}
            if structured:
                options["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": "edit_result", "strict": True, "schema": FULL_TEXT_EDIT_TOOL["function"]["parameters"]}}
            output = self._request_chat(
                settings, system_prompt=routed_edit_prompt(target, variant),
                user_content=json.dumps(model_input, ensure_ascii=False),
                temperature=0.0, max_tokens=_text_output_budget(settings), edit_tool=None,
                require_complete=True, preserve_whitespace=True,
                thinking_budget=edit_thinking_budget(raw, target),
                allow_empty_text=boundary_only, **options,
            )
            if structured:
                plan = None
                result = json.loads(output)
                if not isinstance(result, dict):
                    raise ValueError("编辑结果必须是 JSON 对象")
                body = apply_edit_response(target, result, EDIT_MODE_FULL)
            elif boundary_only:
                plan = None
                body = apply_edit_response(target, {"modified_text": output}, EDIT_MODE_FULL)
            else:
                plan, body = parse_routed_edit_response(output, target)
        except Exception as exc:
            failure = LLMResponseProcessingError(str(exc), self._model_output_text(output) if output else str(getattr(exc, "model_output", "")))
            failure.model_input = model_input
            raise failure from exc
        return body, output, model_input, plan
