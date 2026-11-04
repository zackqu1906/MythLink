"""Automatic mode only: apply the Clef plan with one composed generation.

Gesture configuration and manual candidate racing do not belong in this module.
Shared HTTP requests, tool validation and local model lifetime live in llm.py.
"""
from __future__ import annotations
import json
from .edit_tool import EDIT_MODE_FULL
from .model import LLM_PROVIDER_LOCAL, normalize_llm_provider, validate_edit_target_text
from .processing_common import LLMResponseProcessingError, _text_output_budget


class AutomaticTextProcessingMixin:
    def process_task_with_trace(self, text, task_type, settings, target_text="", screen_context=None, *, edit_plan=None):
        """Automatic mode: composed rules, one full-result protocol, no racing."""
        from .task_routing import DICTATION, EDIT
        from .automatic_prompts import automatic_system_prompt
        prompt = automatic_system_prompt(task_type, edit_plan)
        settings.validate()
        if not settings.enabled:
            raise ValueError("自动判断模式需要配置文本大模型")
        target = str(target_text or "")
        if task_type == EDIT and (target or not edit_plan.allows_empty_source):
            target = validate_edit_target_text(target)
        raw = str(text or "").strip()
        if not raw:
            raise ValueError("本次口述为空")
        content = json.dumps({"utterance": raw, "editable_text": target,
                              "screen_context": screen_context or {},
                              "edit_plan": edit_plan.as_dict() if edit_plan else None}, ensure_ascii=False)
        if normalize_llm_provider(settings.provider) == LLM_PROVIDER_LOCAL and settings.local_auto_start:
            self._ensure_local_server(settings)
        if task_type == DICTATION:
            response = self._request_chat(settings, system_prompt=prompt,
                                          user_content=content, temperature=0.0,
                                          max_tokens=_text_output_budget(settings), edit_tool=None)
            if not isinstance(response, str) or not response.strip():
                raise LLMResponseProcessingError("听写整理没有返回正文", self._model_output_text(response))
            return response.strip(), response
        final, outputs = self._process_edit_with_retry(settings, target=target, system_prompt=prompt,
            user_content=content, max_tokens=_text_output_budget(settings), edit_mode=EDIT_MODE_FULL,
            instruction=raw, max_attempts=1)
        return final, outputs[-1] if outputs else ""
