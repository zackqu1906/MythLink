"""Shared output limits and errors for automatic and manual text processing."""
from __future__ import annotations
from .model import LLMSettings, LLM_PROVIDER_LOCAL, normalize_llm_provider

TEXT_PROCESSING_MAX_OUTPUT_TOKENS = 32768


def _text_output_budget(settings: LLMSettings) -> int:
    # A short source can still request an expanded rewrite. Do not size the
    # output from its character count; keep classification's budget separate.
    if normalize_llm_provider(settings.provider) == LLM_PROVIDER_LOCAL:
        return min(TEXT_PROCESSING_MAX_OUTPUT_TOKENS, settings.local_context_size)
    return TEXT_PROCESSING_MAX_OUTPUT_TOKENS

class LLMResponseProcessingError(RuntimeError):
    """An LLM response was received but could not be safely executed."""

    def __init__(
        self,
        message: str,
        model_output: str,
        model_outputs: tuple[str, ...] | None = None,
    ) -> None:
        super().__init__(message)
        self.model_output = str(model_output or "")
        self.model_outputs = tuple(model_outputs or ()) or (
            (self.model_output,) if self.model_output else ()
        )
