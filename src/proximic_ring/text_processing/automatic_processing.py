"""Automatic mode only: apply the Clef plan with one composed generation.

Gesture configuration and manual candidate racing do not belong in this module.
Shared HTTP requests, tool validation and local model lifetime live in llm.py.
"""
from __future__ import annotations
import json
from .automatic_inputs import dictation_model_input, edit_model_input
from .automatic_prompts import automatic_system_prompt
from .edit_tool import EDIT_MODE_FULL
from .model import LLM_PROVIDER_LOCAL, normalize_llm_provider, validate_edit_target_text
from .processing_common import LLMResponseProcessingError, _text_output_budget
from .task_routing import DICTATION, EDIT


def _validate_automatic_text(text, settings):
    settings.validate()
    if not settings.enabled:
        raise ValueError("自动判断模式需要配置文本大模型")
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("本次口述为空")
    return raw


class AutomaticTextProcessingMixin:
    def process_task_with_input_trace(self, *args, **kwargs):
        """Return a detached request snapshot, including on processing failure."""
        model_input = None

        def capture(content):
            nonlocal model_input
            model_input = json.loads(content)

        try:
            final, output = self.process_task_with_trace(*args, **kwargs, _on_request_input=capture)
        except Exception as exc:
            exc.model_input = model_input
            raise
        return final, output, model_input

    def process_task_with_trace(self, text, task_type, settings, target_text="", screen_context=None, *, edit_plan=None,
                                _on_request_input=None):
        """Dispatch once; the two model APIs accept different input fields."""
        if task_type == DICTATION:
            if edit_plan is not None:
                raise ValueError("听写不应携带编辑计划")
            return self.process_automatic_dictation_with_trace(
                text, settings, screen_context, _on_request_input=_on_request_input)
        if task_type == EDIT:
            return self.process_automatic_edit_with_trace(
                text, settings, target_text, screen_context, edit_plan=edit_plan,
                _on_request_input=_on_request_input)
        raise ValueError("未知自动判断任务")

    def _automatic_request_content(self, settings, payload, on_request_input):
        content = json.dumps(payload, ensure_ascii=False)
        if normalize_llm_provider(settings.provider) == LLM_PROVIDER_LOCAL and settings.local_auto_start:
            self._ensure_local_server(settings)
        if on_request_input is not None:
            on_request_input(content)
        return content

    def process_automatic_dictation_with_trace(self, text, settings, screen_context=None, *, _on_request_input=None):
        """Clean this utterance with read-only OCR; no original text or edit plan."""
        raw = _validate_automatic_text(text, settings)
        content = self._automatic_request_content(settings, dictation_model_input(raw, screen_context), _on_request_input)
        response = self._request_chat(settings, system_prompt=automatic_system_prompt(DICTATION),
                                      user_content=content, temperature=0.0,
                                      max_tokens=_text_output_budget(settings), edit_tool=None)
        if not isinstance(response, str) or not response.strip():
            raise LLMResponseProcessingError("听写整理没有返回正文", self._model_output_text(response))
        return response.strip(), response

    def process_automatic_edit_with_trace(self, text, settings, target_text, screen_context=None, *, edit_plan,
                                          _on_request_input=None):
        """Apply the composed edit plan to the original source in one request."""
        prompt = automatic_system_prompt(EDIT, edit_plan)
        raw = _validate_automatic_text(text, settings)
        # Source availability belongs to routing. Only enforce the request-size
        # limit here; an empty source must not veto the chosen automatic plan.
        target = validate_edit_target_text(target_text, allow_empty=True)
        content = self._automatic_request_content(
            settings, edit_model_input(raw, target, screen_context, edit_plan=edit_plan), _on_request_input)
        final, outputs = self._process_edit_with_retry(settings, target=target, system_prompt=prompt,
            user_content=content, max_tokens=_text_output_budget(settings), edit_mode=EDIT_MODE_FULL,
            instruction=raw, max_attempts=1)
        return final, outputs[-1] if outputs else ""
