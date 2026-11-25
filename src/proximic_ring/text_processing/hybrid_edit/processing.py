"""Bridge the edit engine to the selected text service and voice-history trace."""

import json

from ..edit_response import apply_edit_response
from ..edit_tool import EDIT_MODE_FULL, FULL_TEXT_EDIT_TOOL
from ..model import LLM_PROVIDER_VOLCENGINE
from ..processing_common import LLMResponseProcessingError, _text_output_budget
from ..qwen_thinking import edit_thinking_budget
from .engine import edit


EDIT_RESULT = {
    "type": "json_schema",
    "json_schema": {
        "name": "edit_result",
        "strict": True,
        "schema": FULL_TEXT_EDIT_TOOL["function"]["parameters"],
    },
}


def validate_reply(raw, schema):
    """Every edit schema is a closed object of required strings, enums or integers."""
    fields = schema["properties"]
    data = json.loads(raw)
    if not isinstance(data, dict) or data.keys() != fields.keys():
        raise ValueError("编辑回复的字段与当前步骤不符")
    for name, field in fields.items():
        value = data[name]
        expected = int if field["type"] == "integer" else str
        if type(value) is not expected or ("enum" in field and value not in field["enum"]):
            raise ValueError(f"编辑回复中的 {name} 无效")


def process_hybrid_edit(processor, settings, model_input, cancelled, edit_steps):
    steps = [] if edit_steps is None else edit_steps
    source = model_input["editable_text"]
    budget = edit_thinking_budget(model_input["utterance"], source)

    def complete(messages, response_format):
        if cancelled():
            raise RuntimeError("编辑已取消")
        response_format = response_format or EDIT_RESULT
        spec = response_format["json_schema"]
        system = messages[0]["content"]
        if settings.provider == LLM_PROVIDER_VOLCENGINE:
            # Ark's existing Responses text transport uses prompt-specified JSON.
            # Include the actual per-step shape (selection, review, or final text).
            system += "\n只返回符合以下结构的 JSON 对象。\n" + json.dumps(
                spec["schema"], ensure_ascii=False
            )
        step = {"name": spec["name"], "system_prompt": system}
        steps.append(step)
        raw = processor._request_chat(
            settings,
            system_prompt=system,
            user_content=messages[1]["content"],
            followup_messages=tuple(messages[2:]),
            temperature=0.0,
            max_tokens=_text_output_budget(settings),
            require_complete=True,
            preserve_whitespace=True,
            allow_empty_text=True,
            response_format=response_format,
            thinking_budget=budget,
        )
        step["output"] = raw
        validate_reply(raw, spec["schema"])
        return raw

    try:
        _, body, _ = edit(model_input, complete)
        final = apply_edit_response(source, {"modified_text": body}, EDIT_MODE_FULL)
    except Exception as exc:
        raw = steps[-1].get("output", "") if steps else ""
        failure = LLMResponseProcessingError(str(exc), raw or getattr(exc, "model_output", ""))
        failure.model_input = model_input
        failure.edit_steps = tuple(steps)
        raise failure from exc
    return final, steps[-1]["output"] if steps else "", model_input, None
