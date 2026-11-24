import io
import json
from dataclasses import replace
from hashlib import sha256

import pytest

from proximic_ring.screenshots.context import SavedScreenshotContext
from proximic_ring.text_processing.context_preparation import ContextPreparationProvider
from proximic_ring.text_processing.llm import OpenAICompatibleTextProcessor
from proximic_ring.text_processing.model import (
    DEFAULT_QWEN_MODEL, LLMSettings, ROUTED_EDIT_BOUNDARY, ROUTED_EDIT_ORIGINAL,
)
from proximic_ring.text_processing.original_edit_prompt import ORIGINAL_EDIT_PROMPT
from test_context_preparation import SUMMARY_TEXT
from test_shared_voice_capture import raw_capture


def settings():
    return LLMSettings(enabled=True, provider="bailian", model=DEFAULT_QWEN_MODEL,
                       base_url="https://text.example.test/compatible-mode/v1", api_key="test-key", api_key_env="")


def processor_for(content, *, finish="stop", extra=None):
    calls = []
    def respond(req, **kwargs):
        assert req.full_url == settings().base_url + "/chat/completions"
        assert req.get_header("Authorization") == "Bearer test-key"
        body = json.loads(req.data)
        calls.append(body)
        assert "reasoning_effort" not in body
        assert body["model"] == "qwen3.8-max" and "thinking" not in body
        msg = {"role":"assistant", "content":content, "reasoning_content":"private reasoning", **(extra or {})}
        return io.BytesIO(json.dumps({"choices":[{"message":msg,"finish_reason":finish}]}).encode())
    return OpenAICompatibleTextProcessor(urlopen=respond), calls


@pytest.mark.parametrize("text", ["", " 甲\n乙 ", "."])
def test_original_edit_uses_frozen_prompt_and_decodes_exact_body(text):
    processor,calls = processor_for(json.dumps({"modified_text":text}, ensure_ascii=False))
    final, raw, inp, plan = processor.process_routed_edit_with_trace(
        "改一下", settings(), "原文", {"messages":[]}, variant=ROUTED_EDIT_ORIGINAL)
    body, = calls
    assert body["enable_thinking"] is False and "thinking_budget" not in body
    assert sha256(ORIGINAL_EDIT_PROMPT.encode()).hexdigest() == "3a80a2bb48a2f4bbaade40c5b1c5afbe2dcc5129bfab61db3c79446d4cef0b47"
    assert body["messages"][0]["content"] == ORIGINAL_EDIT_PROMPT
    assert body["response_format"]["json_schema"]["strict"] and "temperature" not in body
    assert json.loads(body["messages"][1]["content"])==inp
    assert plan is None and final==text and json.loads(raw)=={"modified_text":text}


@pytest.mark.parametrize("text,finish,extra", [
    ("{}", "stop", {}), ('{"modified_text":null}', "stop", {}),
    ('{"modified_text":""}', "length", {}), ("", "stop", {}),
    ('{"modified_text":""}', "stop", {"refusal":"declined"}),
    (json.dumps('{"modified_text":""}'), "stop", {}),
])
def test_invalid_final_never_becomes_clear(text,finish,extra):
    processor,_ = processor_for(text,finish=finish,extra=extra)
    with pytest.raises(RuntimeError) as exc:
        processor.process_routed_edit_with_trace("删除",settings(),"旧",variant=ROUTED_EDIT_ORIGINAL)
    assert "private reasoning" not in exc.value.model_output


def test_qwen_legacy_direct_body_still_supports_empty():
    processor,_ = processor_for("")
    assert processor.process_routed_edit_with_trace("删除",settings(),"旧",variant=ROUTED_EDIT_BOUNDARY)[0]==""


@pytest.mark.parametrize("enabled", [False, True])
def test_screen_and_saved_screenshot_keep_separate_protocols(tmp_path, enabled):
    prefs = replace(settings(), enabled=False, qwen_thinking_enabled=enabled)
    processor,calls = processor_for(SUMMARY_TEXT)
    context=ContextPreparationProvider(processor)
    try:
        result=context.prepare(raw_capture(),prefs)
        assert result["status"]=="ok" and "联系张伟" in result["context"]
    finally:
        context.close()
    assert len(calls)==1 and "response_format" not in calls[0]
    assert calls[0]["enable_thinking"] is enabled
    assert calls[0].get("thinking_budget") == (24 if enabled else None)
    ocr_text = "周五开会。" * 20
    cleanup,calls=processor_for(ocr_text)
    store=SavedScreenshotContext(tmp_path/"slot",processor=cleanup,ocr=lambda _: ocr_text)
    try:
        path=tmp_path/"image.png"
        path.write_bytes(b"test image")
        saved=store.submit(path,prefs).result(4)
        assert saved["status"]=="ok" and saved["text"]==ocr_text
        assert len(calls)==1 and "response_format" not in calls[0]
        assert calls[0]["enable_thinking"] is enabled
        assert calls[0].get("thinking_budget") == (128 if enabled else None)
    finally:
        store.close()


@pytest.mark.parametrize("spoken,source,budget", [
    ("改成下午见", "上午见", 24),
    ("杰是杰出的杰", "张洁", 128),
    ("去掉标题，然后分行", "标题、甲、乙", 128),
    ("精简", "甲" * 99, 24),
    ("精简", "甲" * 100, 128),
    ("改成两行", "原文写着「明天见」", None),
    ("引号里的名字逐字保留", "张洁", None),
])
def test_thinking_switch_routes_only_source_and_speech(spoken, source, budget):
    processor, calls = processor_for('{"modified_text":"成稿"}')
    final, raw, _, _ = processor.process_routed_edit_with_trace(
        spoken, replace(settings(), qwen_thinking_enabled=True), source,
        {"context": "“资料”" * 100}, variant=ROUTED_EDIT_ORIGINAL)
    body, = calls
    assert body["enable_thinking"] is (budget is not None)
    assert body.get("thinking_budget") == budget
    assert body["messages"][0]["content"] == ORIGINAL_EDIT_PROMPT
    assert final == "成稿" and json.loads(raw) == {"modified_text": "成稿"}


def test_qwen_switch_does_not_change_deepseek_payload():
    calls = []
    def respond(req, **kwargs):
        calls.append(json.loads(req.data))
        payload = {"status": "completed", "output": [{"type": "message", "role": "assistant",
                   "content": [{"type": "output_text", "text": '{"modified_text":"成稿"}'}]}]}
        return io.BytesIO(json.dumps(payload).encode())

    processor = OpenAICompatibleTextProcessor(urlopen=respond)
    prefs = replace(settings(), provider="volcengine", model="deepseek-v4-pro-ga-260813")
    for enabled in (False, True):
        processor.process_routed_edit_with_trace("精简", replace(prefs, qwen_thinking_enabled=enabled),
                                                 "原文", variant=ROUTED_EDIT_ORIGINAL)
    assert calls[0] == calls[1] and calls[0]["thinking"] == {"type": "disabled"}


@pytest.mark.parametrize("enabled", [False, True])
def test_manual_tool_edit_follows_qwen_thinking_preference(enabled):
    arguments = json.dumps({"modified_text": "下午见"})
    extra = {"tool_calls": [{"type": "function", "function": {"name": "submit_text_edit", "arguments": arguments}}]}
    processor, calls = processor_for(None, finish="tool_calls", extra=extra)
    result = processor.process("改成下午", "edit", replace(settings(), qwen_thinking_enabled=enabled),
                               target_text="上午见", edit_mode="full")
    assert result == "下午见"
    body, = calls
    assert body["tool_choice"] == ("auto" if enabled else "required")
    assert body.get("thinking_budget") == (24 if enabled else None)
