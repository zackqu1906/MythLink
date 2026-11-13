"""OCR preparation ownership, waiting and failure contracts."""
from concurrent.futures import Future
import json
import threading
from types import SimpleNamespace

import pytest

from proximic_ring.text_processing.context_preparation import (
    ContextPreparationProvider, PreparedContext, organize_ocr, parse_context, resolve_model_context)
from proximic_ring.text_processing.model import InputModeRoutingRequest, TextProcessingRequest
from proximic_ring.text_processing.task_routing import RoutingDecision, router_ocr_text
from proximic_ring.text_processing.worker import TextProcessingWorker
from proximic_ring.voice.modification_dataset import ModificationDatasetCollector
from test_routed_edit import settings, processor_returning
from test_shared_voice_capture import raw_capture, finish_asr


SUMMARY = {"scene": "对话", "app_type": "chat", "topic": "联系人员", "context": "对方要求联系张伟。",
           "draft": "输入框草稿", "uncertain": False}
SUMMARY_TEXT = "场景：对话\n主题：联系人员\n相关信息：对方要求联系张伟。\n未发送草稿：输入框草稿\n不确定信息：无"


def test_preparation_starts_after_ocr_once_and_separates_raw_evidence():
    processor, calls = processor_returning(SUMMARY_TEXT)
    provider = ContextPreparationProvider(processor)
    raw = raw_capture()
    raw["application_name"] = "微信"
    capture = Future()
    result = provider.prefetch(capture, settings())
    try:
        assert not result.done() and not calls
        capture.set_result(raw)
        context = result.result(3)
        assert resolve_model_context(result) is context
        assert resolve_model_context(result) is context and len(calls) == 1
        assert context["processing"] == "llm_compact" and context["context"] == SUMMARY["context"]
        request = json.loads(calls[0]["input"][1]["content"][0]["text"])
        assert request["application"] == "com.tencent.xinWeChat"
        assert request["application_name"] == context["application_name"] == "微信"
        assert context["scene"] == "对话"
        assert request["line_format"] == ["x", "y", "w", "h", "confidence", "text"]
        assert request["current_chat"]["messages"] == [{"speaker": "对方", "text": "请联系张伟",
            "ocr_lines": [[.4, .4, .3, .02, .97, "请联系张伟"]]}]
        assert request["composer_ocr"] == [[.4, .85, .3, .02, .95, "输入框草稿"]]
        assert "联系人" not in json.dumps(request, ensure_ascii=False)
        assert calls[0]["thinking"] == {"type": "disabled"}
        assert raw["processing"] == "raw" and len(raw.evidence["ocr_lines"]) == 4
        assert "screenshot_png" not in json.dumps(calls)
        assert "raw_output" not in json.dumps(context)
        assert "对方要求联系张伟" in router_ocr_text(context)
        assert router_ocr_text(context).startswith("场景：对话\n")
        assert "输入框草稿" not in router_ocr_text(context)
    finally:
        provider.close()


def test_general_window_preserves_columns_and_chat_layout_failure_does_not_send_whole_window():
    raw = raw_capture()
    raw["application"] = "example.editor"
    packed = organize_ocr(raw)
    assert "application_name" not in packed  # Old history has only the bundle ID.
    assert "columns" in packed and len(packed["columns"]) == 2
    assert sum(len(group["lines"]) for group in packed["columns"]) == 4
    assert packed["line_format"] == ["x", "y", "w", "h", "confidence", "text"]
    assert [.4, .4, .3, .02, .97, "请联系张伟"] in [line for group in packed["columns"] for line in group["lines"]]
    raw["application"] = "com.tencent.xinWeChat"
    raw["layout_error"] = "无法定位聊天区域"
    processor, calls = processor_returning("unused")
    provider = ContextPreparationProvider(processor)
    try:
        context = provider.prepare(raw, settings())
        assert context["status"] == "unavailable" and not calls
        assert len(raw.evidence["ocr_lines"]) == 4
    finally:
        provider.close()


def test_message_evidence_keeps_each_line_confidence_without_restoring_filtered_regions():
    raw = raw_capture()
    raw.evidence["ocr_lines"].extend([
        {"text": "项目群（8）", "box": [.4, .03, .2, .02], "confidence": .94},
        {"text": "明天上午", "box": [.4, .43, .2, .02], "confidence": .68, "speaker": "other"},
        {"text": "噪声", "box": [.4, .5, .2, .02], "confidence": .1, "speaker": "other"},
        {"text": "收到", "box": [.7, .6, .2, .02], "speaker": "self"},
    ])
    original = json.dumps(raw.evidence, ensure_ascii=False, default=str)
    packed = organize_ocr(raw)
    first, second = packed["current_chat"]["messages"]
    assert first["text"] == "请联系张伟\n明天上午"
    assert [line[-2] for line in first["ocr_lines"]] == [.97, .68]
    assert second["speaker"] == "我" and second["ocr_lines"][0][-2] is None
    assert packed["current_chat"]["title_ocr"] == [[.4, .03, .2, .02, .94, "项目群（8）"]]
    assert all(word not in json.dumps(packed, ensure_ascii=False) for word in ["噪声", "联系人", "16:29"])
    assert json.dumps(raw.evidence, ensure_ascii=False, default=str) == original
    raw["application"] = "example.editor"
    assert any(line[-1] == "收到" and line[-2] is None for column in organize_ocr(raw)["columns"] for line in column["lines"])


def test_positional_evidence_obeys_message_limits_and_does_not_restore_truncated_prefix():
    raw = raw_capture()
    raw.evidence["ocr_lines"] = [{"text": str(i), "box": [.4, .08 + i * .03, .2, .01],
                                 "confidence": .9, "speaker": "other"} for i in range(20)]
    packed = organize_ocr(raw)
    messages = packed["current_chat"]["messages"]
    assert len(messages) == 16
    assert [m["ocr_lines"][0][-1] for m in messages] == [str(i) for i in range(4, 20)]
    raw.evidence["ocr_lines"] = [{"text": "应裁掉的前缀" + "长消息" * 1000, "box": [.4, .4, .3, .02],
                                 "confidence": .9, "speaker": "other"}]
    message = organize_ocr(raw)["current_chat"]["messages"][0]
    assert message["truncated"] and len(message["text"]) == 2400
    assert "应裁掉的前缀" not in json.dumps(message, ensure_ascii=False)
    assert message["ocr_lines"][0][-1] == message["text"]


def test_browser_identity_and_email_scene_survive_downstream_and_history():
    from proximic_ring.voice.history_details import _context
    raw = raw_capture()
    raw.update(application="com.apple.Safari", application_name="Safari")
    response = "场景：写邮件\n主题：演示安排\n相关信息：回复演示安排。\n未发送草稿：未发现\n不确定信息：无"
    processor, calls = processor_returning(response)
    provider = ContextPreparationProvider(processor)
    try:
        context = provider.prepare(raw, settings())
        request = json.loads(calls[0]["input"][1]["content"][0]["text"])
        assert request["application_name"] == "Safari" and "columns" in request
        assert context["application"] == "com.apple.Safari" and context["scene"] == "写邮件"
        assert router_ocr_text(context).startswith("场景：写邮件\n")
        assert _context(context).startswith("场景：写邮件\n")
        assert not _context(SUMMARY | {"scene": ""}).startswith("场景：")
    finally:
        provider.close()


@pytest.mark.parametrize("text", ["普通自然语言摘要，不是JSON。", "场景：浏览器\n相关信息：页面介绍网络服务。"])
def test_unstructured_prose_remains_available_with_uncertain_scene(text):
    context = parse_context(text)
    assert context["scene"] == "无法确定" and context["uncertain"]
    assert context["context"] and context["draft"] is None
    processor, calls = processor_returning(text)
    provider = ContextPreparationProvider(processor)
    try:
        assert provider.prepare(raw_capture(), settings())["status"] == "ok"
        assert len(calls) == 1
    finally:
        provider.close()


def test_natural_sections_keep_multiline_draft_and_quotes_out_of_router_context():
    context = parse_context('**场景：对话（AI助手）**\n## 主题：请求示例\n相关信息：\n- 对方提到 "a" 与 {b}。\n'
                            '## 未发送草稿\n第一行 "hello"\n第二行 {world}\n不确定信息：发言人无法确认')
    assert context["scene"] == "对话"
    assert context["draft"] == '第一行 "hello"\n第二行 {world}'
    assert context["uncertain"] and context["uncertainties"] == "发言人无法确认"
    excerpt = router_ocr_text({"status": "ok", "processing": "llm_compact", **context})
    assert '"a" 与 {b}' in excerpt and "hello" not in excerpt and "world" not in excerpt


def test_uncertain_draft_explanation_is_not_exposed_as_a_draft():
    context = parse_context('- 场景：对话\n相关信息：' + '上文' * 500 + '\n未发送草稿：无法确认（只识别到<）')
    assert context["draft"] is None and context["uncertain"]
    assert "只识别到<" in context["uncertainties"]
    excerpt = router_ocr_text({"status": "ok", "processing": "llm_compact", **context})
    assert len(excerpt) <= 800 and "内容不确定：True" in excerpt


@pytest.mark.parametrize("response,status", [("", "completed"), (SUMMARY_TEXT, "incomplete")])
def test_invalid_or_incomplete_summary_has_no_raw_fallback_or_retry(response, status):
    processor, calls = processor_returning(response, status=status)
    provider = ContextPreparationProvider(processor)
    try:
        context = provider.prepare(raw_capture(), settings())
        assert context["status"] == "unavailable" and context["messages"] == []
        assert router_ocr_text(context) == "" and len(calls) == 1
        assert "联系人" not in json.dumps(context, ensure_ascii=False)
        assert context.evidence["context_preparation"]["error"]
    finally:
        provider.close()


def test_timeout_becomes_terminal_unavailable_without_fallback():
    def timeout(*args, **kwargs):
        assert args[0].timeout_s <= 10
        raise TimeoutError("测试超时")
    provider = ContextPreparationProvider(SimpleNamespace(_request_chat=timeout))
    try:
        result = provider.prefetch(raw_capture(), settings())
        assert result.result(2)["status"] == "unavailable"
        assert "测试超时" in resolve_model_context(result)["reason"]
    finally:
        provider.close()


def test_uniformly_low_confidence_ocr_does_not_call_model_or_modify_evidence():
    raw = raw_capture()
    for line in raw.evidence["ocr_lines"]:
        line["confidence"] = .3
    before = json.dumps(raw.evidence["ocr_lines"], ensure_ascii=False)
    processor, calls = processor_returning(SUMMARY_TEXT)
    provider = ContextPreparationProvider(processor)
    try:
        context = provider.prepare(raw, settings())
        assert context["status"] == "unavailable" and not calls
        assert router_ocr_text(context) == ""
        assert context.evidence["context_preparation"]["quality_gate"]["reason"] == "all_lines_low_confidence"
        assert json.dumps(raw.evidence["ocr_lines"], ensure_ascii=False) == before
    finally:
        provider.close()


@pytest.mark.parametrize("confidence", [None, .4, .9])
def test_missing_or_mixed_confidence_is_not_rejected_as_uniformly_poor(confidence):
    raw = raw_capture()
    raw["application"] = "example.editor"
    for line in raw.evidence["ocr_lines"]:
        line["confidence"] = .3
    raw.evidence["ocr_lines"][0]["confidence"] = confidence
    processor, calls = processor_returning(SUMMARY_TEXT)
    provider = ContextPreparationProvider(processor)
    try:
        context = provider.prepare(raw, settings())
        assert context["status"] == "ok" and len(calls) == 1
        assert "quality_gate" not in context.evidence["context_preparation"]
    finally:
        provider.close()


@pytest.mark.parametrize("lane", ["auto", "manual_routed_edit"])
@pytest.mark.parametrize("cancel", [False, True])
def test_consumers_wait_for_prepared_result_and_cancellation_never_sends(lane, cancel):
    pending, called, done = PreparedContext(), threading.Event(), threading.Event()
    seen = []
    def classify(speech, target, context, **kwargs):
        seen.append(context); called.set()
        return RoutingDecision("dictation"), "trace"
    class Processor:
        def process_routed_edit_with_trace(self, speech, settings, target, context, **kwargs):
            seen.append(context); called.set()
            return target, "分类：无可执行编辑\n正文：" + target, {"screen_context": dict(context)}, None
    worker = TextProcessingWorker(on_result=lambda r: done.set(), on_routing_result=lambda r: done.set(),
        processor=Processor(), intent_router=SimpleNamespace(classify=classify, close=lambda: None),
        context_provider=SimpleNamespace(close=lambda: None))
    try:
        if lane == "auto":
            worker.submit_routing(InputModeRoutingRequest(1, 7, "口述", settings(), screen_context=pending))
        else:
            worker.submit(TextProcessingRequest(1, 7, "edit", "口述", settings(), "原文",
                routing_mode=lane, screen_context=pending))
        assert not called.wait(.15) and not done.is_set()
        if cancel:
            worker.cancel_request(1)
        context = {"status": "ok", "processing": "llm_compact", **SUMMARY}
        pending.set_result(context)
        if cancel:
            worker.close(wait=True)
            assert not called.is_set() and not done.is_set()
        else:
            assert done.wait(2) and seen == [context]
    finally:
        worker.close(wait=True)


@pytest.mark.parametrize("enabled", [True, False])
def test_history_saves_raw_capture_and_policy_independently(tmp_path, enabled):
    store = ModificationDatasetCollector(tmp_path, "context-test")
    raw, prepared = Future(), PreparedContext()
    identifier = store.record_screen_capture(7, raw, routing="manual_routed_edit")
    store.record_context_preparation(identifier, prepared if enabled else {}, enabled=enabled)
    finish_asr(store)
    raw.set_result(raw_capture())
    record = store._interaction_data(identifier)
    assert record["context_preparation"]["status"] == ("pending" if enabled else "disabled")
    assert len(record["screen_evidence"]["ocr_lines"]) == 4
    store.begin_session(8)
    prepared.set_result({"status": "ok", "processing": "llm_compact", **SUMMARY})
    record = store._interaction_data(identifier)
    assert record["context_preparation"]["status"] == ("ok" if enabled else "disabled")
    assert "context_preparation" not in store._interaction_data(store.interaction_id_for_session(8))
    assert any(row["key"] == "context_preparation" for row in store.load_details(identifier)["sections"])


def test_deleted_record_is_not_recreated_by_late_preparation(tmp_path):
    store = ModificationDatasetCollector(tmp_path, "context-test")
    pending = PreparedContext()
    identifier = store.record_screen_capture(7, raw_capture(), routing="auto")
    store.record_context_preparation(identifier, pending, enabled=True)
    finish_asr(store)
    assert store.delete_entry(identifier)
    pending.set_result({"status": "ok", **SUMMARY})
    assert not store._interaction_path(identifier).exists()
