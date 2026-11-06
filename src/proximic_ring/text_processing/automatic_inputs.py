"""Model input contracts shared by automatic generation and its history records."""
from __future__ import annotations


def dictation_model_input(utterance, screen_context=None):
    """Dictation cleans only this utterance; it has no editable source or plan."""
    return {"utterance": str(utterance or "").strip(), "screen_context": screen_context or {}}


def edit_model_input(utterance, editable_text, screen_context=None, *, edit_plan):
    """Editing needs both the spoken instruction and the source it operates on."""
    return {"utterance": str(utterance or "").strip(), "editable_text": str(editable_text or ""),
            "screen_context": screen_context or {},
            "edit_plan": edit_plan.as_dict() if edit_plan else None}
