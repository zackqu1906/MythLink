"""Bridge the edit engine to the selected text service and voice-history trace."""

import json

from ..edit_response import apply_edit_response
from ..edit_tool import EDIT_MODE_FULL, FULL_TEXT_EDIT_TOOL
from ..model import LLM_PROVIDER_VOLCENGINE
from ..processing_common import LLMResponseProcessingError, _text_output_budget
from ..qwen_thinking import edit_thinking_budget
from .engine import edit_with_steps
from .trace import EditPipeline


MODEL_OPERATIONS = {
    "choose": "选择候选位置", "resolve": "定位口述所指的原词", "ordinal": "定位指定次序的原词",
    "review": "复核替换初稿", "confirm": "确认候选改法", "repair": "补做剩余要求",
    "retry": "按校验反馈重做", "location": "按完整口述编辑", "edit": "生成编辑正文",
    "term": "确定术语写法", "facts": "从资料提取信息", "reply": "拟写回复",
    "write": "撰写正文", "compose": "创作正文", "condense": "精简正文",
    "context_style": "结合资料改写", "compound": "执行复合编辑", "style": "调整表达风格",
    "polish": "润色正文", "translate": "翻译正文",
}


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


def process_hybrid_edit(processor, settings, model_input, cancelled, edit_steps, edit_pipeline=None):
    steps = [] if edit_steps is None else edit_steps
    pipeline = edit_pipeline or EditPipeline()
    pipeline.start("rule", "解析口述并匹配编辑规则")
    source = model_input["editable_text"]
    budget = edit_thinking_budget(model_input["utterance"], source)

    def complete(messages, response_format, operation):
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
        if operation in ("review", "confirm"):
            pipeline.describe("定位原词并生成替换初稿", "规则生成初稿")
        elif operation == "choose":
            pipeline.describe("解析口述并列出候选位置", "规则筛选位置")
        elif operation == "term":
            pipeline.describe("定位术语并整理候选写法", "规则定位术语")
        label = MODEL_OPERATIONS.get(operation, "生成编辑正文")
        step = {"name": spec["name"], "operation": operation, "system_prompt": system}
        steps.append(step)
        pipeline.start("model", label, route="模型" + label)
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
        next_rule = {
            "choose": "校验模型回复并核对候选位置", "resolve": "校验模型回复并核对原词",
            "ordinal": "校验模型回复并核对指定次序", "review": "校验复核结果与剩余要求",
            "repair": "核验补做结果中的字词位置", "term": "校验术语写法并原位回填",
        }.get(operation, "校验模型回复并处理结果")
        pipeline.start("rule", next_rule)
        step["output"] = raw
        validate_reply(raw, spec["schema"])
        return raw

    try:
        plan, body, decision = edit_with_steps(model_input, complete)
        mode = plan["mode"]
        outcome = ""
        if mode in ("local", "cancel"):
            pipeline.describe("解析并执行规则 · " + plan["method"], "规则直接处理（" + plan["method"] + "）")
        elif body == source:
            outcome = "保留原文"
            if mode in ("choose", "resolve", "ordinal_scope") and "draft" not in plan:
                outcome += "（" + decision + "）"
        elif body == plan.get("draft"):
            outcome = "采用替换初稿"
        elif mode == "term":
            outcome = "规则回填术语"
        elif "draft" in plan:
            outcome = "采用修订正文"
        pipeline.start("rule", "校验最终正文", route=outcome)
        final = apply_edit_response(source, {"modified_text": body}, EDIT_MODE_FULL)
    except Exception as exc:
        raw = steps[-1].get("output", "") if steps else ""
        failure = LLMResponseProcessingError(str(exc), raw or getattr(exc, "model_output", ""))
        failure.model_input = model_input
        failure.edit_steps = tuple(steps)
        raise failure from exc
    return final, steps[-1]["output"] if steps else "", model_input, None
