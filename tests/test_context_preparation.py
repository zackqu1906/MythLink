"""OCR preparation ownership, waiting and failure contracts."""
from concurrent.futures import Future
import json
import threading
from types import SimpleNamespace

import pytest

from proximic_ring.text_processing.context_preparation import (
    ContextPreparationProvider, PreparedContext, organize_ocr, resolve_model_context)
from proximic_ring.text_processing.model import InputModeRoutingRequest, TextProcessingRequest
from proximic_ring.text_processing.task_routing import RoutingDecision, router_ocr_text
from proximic_ring.text_processing.worker import TextProcessingWorker
from proximic_ring.voice.modification_dataset import ModificationDatasetCollector
from test_routed_edit import settings, processor_returning
from test_shared_voice_capture import raw_capture, finish_asr


SUMMARY = {"app_type": "chat", "topic": "联系人员", "context": "对方要求联系张伟。",
           "draft": "输入框草稿", "uncertain": False}


def test_preparation_starts_after_ocr_once_and_separates_raw_evidence():
    processor, calls = processor_returning(json.dumps(SUMMARY, ensure_ascii=False))
    provider = ContextPreparationProvider(processor)
    raw = raw_capture()
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
        assert request["current_chat"]["messages"] == [{"speaker": "other", "text": "请联系张伟"}]
        assert request["composer_ocr"] == ["输入框草稿"]
        assert "联系人" not in json.dumps(request, ensure_ascii=False)
        assert calls[0]["thinking"] == {"type": "disabled"}
        assert raw["processing"] == "raw" and len(raw.evidence["ocr_lines"]) == 4
        assert "screenshot_png" not in json.dumps(calls)
        assert "raw_output" not in json.dumps(context)
        assert "对方要求联系张伟" in router_ocr_text(context)
        assert "输入框草稿" not in router_ocr_text(context)
    finally:
        provider.close()


def test_general_window_preserves_columns_and_chat_layout_failure_does_not_send_whole_window():
    raw = raw_capture()
    raw["application"] = "example.editor"
    packed = organize_ocr(raw)
    assert "columns" in packed and len(packed["columns"]) == 2
    assert sum(len(group["lines"]) for group in packed["columns"]) == 4
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


@pytest.mark.parametrize("response,status", [("not json", "completed"),
    (json.dumps(SUMMARY), "incomplete"),
    (json.dumps({**SUMMARY, "uncertain": "false"}), "completed")])
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


@pytest.mark.parametrize("lane", ["auto", "manual_routed_edit"])
@pytest.mark.parametrize("cancel", [False, True])
def test_consumers_wait_for_prepared_result_and_cancellation_never_sends(lane, cancel):
    pending, called, done = PreparedContext(), threading.Event(), threading.Event()
    seen = []
    def classify(speech, target, context, **kwargs):
        seen.append(context); called.set()
        return RoutingDecision("dictation"), "trace"
    class Processor:
        def process_routed_edit_with_trace(self, speech, settings, target, context):
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
