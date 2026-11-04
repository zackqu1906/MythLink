"""Compatibility-only chat-model classifier; the current automatic UI uses Clef.

Retain this API for existing callers/tests. It is neither gesture classification
nor a fallback from the current two-stage Clef router.
"""
from __future__ import annotations
import json
from typing import Any
from .model import INPUT_MODE_DICTATION, INPUT_MODE_EDIT, LLM_PROVIDER_LOCAL, LLMSettings, normalize_llm_provider
from .prompts import INPUT_MODE_ROUTER_PROMPT
from .processing_common import LLMResponseProcessingError

INPUT_MODE_MAX_OUTPUT_TOKENS = 16


class LegacyInputRoutingMixin:
    def classify_input_mode(self, text: str, settings: LLMSettings) -> str:
        mode, _model_output = self.classify_input_mode_with_trace(text, settings)
        return mode

    def classify_input_mode_with_trace(
        self,
        text: str,
        settings: LLMSettings,
    ) -> tuple[str, str]:
        """Classify a completed utterance as dictation or an edit instruction."""

        settings.validate()
        raw_text = str(text or "").strip()
        if not raw_text:
            return INPUT_MODE_DICTATION, ""
        if not settings.enabled:
            raise ValueError("自动判断模式需要启用大模型")
        if (
            normalize_llm_provider(settings.provider) == LLM_PROVIDER_LOCAL
            and settings.local_auto_start
        ):
            self._ensure_local_server(settings)
        response = self._request_chat(
            settings,
            system_prompt=INPUT_MODE_ROUTER_PROMPT,
            user_content=f"<用户语音>\n{raw_text}\n</用户语音>",
            temperature=0.0,
            max_tokens=INPUT_MODE_MAX_OUTPUT_TOKENS,
            edit_tool=None,
        )
        model_output = self._model_output_text(response)
        try:
            return self._parse_input_mode_response(response), model_output
        except ValueError as exc:
            raise LLMResponseProcessingError(str(exc), model_output) from exc

    @staticmethod
    def _parse_input_mode_response(response: str | dict[str, Any]) -> str:
        payload: object = response
        if isinstance(response, str):
            text = response.strip()
            if text.startswith("```") and text.endswith("```"):
                lines = text.splitlines()
                text = "\n".join(lines[1:-1]).strip()
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = text.strip().strip('"').lower()
        if isinstance(payload, dict):
            payload = payload.get("mode")
        value = str(payload or "").strip().lower()
        aliases = {
            "dictation": INPUT_MODE_DICTATION,
            "听写": INPUT_MODE_DICTATION,
            "input": INPUT_MODE_DICTATION,
            "edit": INPUT_MODE_EDIT,
            "instruction": INPUT_MODE_EDIT,
            "指令": INPUT_MODE_EDIT,
            "编辑": INPUT_MODE_EDIT,
        }
        mode = aliases.get(value)
        if mode is None:
            raise ValueError("大模型没有返回有效的听写/编辑分类 JSON")
        return mode
