from concurrent.futures import Future
import io
import json

import pytest
from test_inline_ui import inline_ui
from test_shared_voice_capture import raw_capture, finish_asr
from proximic_ring.text_processing.context_preparation import ContextPreparationProvider, PreparedContext
from proximic_ring.text_processing.llm import OpenAICompatibleTextProcessor
from test_context_preparation import SUMMARY_TEXT


def test_voice_screen_ocr_always_uses_ark_flash(inline_ui, monkeypatch):
    controller, *_ = inline_ui
    requests = []
    def respond(request, **kwargs):
        requests.append((request.full_url, request.get_header("Authorization"), json.loads(request.data)))
        result = {"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": SUMMARY_TEXT}]}]}
        return io.BytesIO(json.dumps(result).encode())

    preparer = ContextPreparationProvider(OpenAICompatibleTextProcessor(urlopen=respond))
    worker = controller._text_processing_worker
    monkeypatch.setattr(worker, "capture_context", lambda target: raw_capture())
    monkeypatch.setattr(worker, "prepare_context", preparer.prefetch)
    controller.ocrContextEnabled = True
    controller.qwenThinkingEnabled = True
    controller.llmApiKey = "test-qwen"
    controller.llmProvider = "volcengine"
    controller.llmBaseUrl = "https://ark.example.test/api/v3"
    controller.llmApiKey = "test-ark"
    controller.llmModel = "deepseek-v4-pro-ga-260813"
    try:
        for selected in ("volcengine", "bailian", "local", "openai"):
            controller.llmProvider = selected
            editor = controller._voice_llm_settings()
            controller._prefetch_voice_capture(None, inline=True, verified=True)
            result = controller._voice_capture["model_context"].result(2)
            assert result["status"] == "ok"
            url, auth, body = requests[-1]
            assert url == "https://ark.example.test/api/v3/responses" and auth == "Bearer test-ark"
            assert body["model"] == "deepseek-v4-1-flash-260910" and body["thinking"] == {"type": "disabled"}
            trace = result.evidence["context_preparation"]
            assert (trace["provider"], trace["model"]) == ("volcengine", body["model"])
            assert controller.llmProvider == selected and controller._voice_llm_settings() == editor
        assert len(requests) == 4
    finally:
        preparer.close()


def test_missing_ark_credentials_do_not_use_editor_key(inline_ui, monkeypatch):
    controller, *_ = inline_ui
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    controller.llmProvider = "bailian"
    controller.llmApiKey = "test-qwen"
    controller.ocrContextEnabled = True
    preparer = ContextPreparationProvider(OpenAICompatibleTextProcessor(
        urlopen=lambda *args, **kwargs: pytest.fail("HTTP called without Ark credentials")))
    worker = controller._text_processing_worker
    monkeypatch.setattr(worker, "capture_context", lambda target: raw_capture())
    monkeypatch.setattr(worker, "prepare_context", preparer.prefetch)
    try:
        controller._prefetch_voice_capture(None, inline=True, verified=True)
        result = controller._voice_capture["model_context"].result(2)
        assert result["status"] == "unavailable"
        assert "火山方舟" in result["reason"] and "ARK_API_KEY" in result["reason"]
        assert controller._voice_llm_settings().api_key == "test-qwen"
    finally:
        preparer.close()


@pytest.mark.parametrize("routing", ["auto", "manual_routed_edit"])
@pytest.mark.parametrize("enabled", [False, True])
def test_ocr_switch_is_latched_and_never_controls_raw_history(inline_ui, monkeypatch, routing, enabled):
    from PySide6.QtCore import QObject
    controller, bridge, requests, root, event = inline_ui
    raw, prepared, preparations = Future(), PreparedContext(), []
    monkeypatch.setattr(controller._text_processing_worker, "capture_context", lambda target: raw)
    monkeypatch.setattr(controller._text_processing_worker, "prepare_context",
        lambda capture, settings: preparations.append(capture) or prepared)
    monkeypatch.setattr(controller._text_processing_worker, "warmup_router", lambda: None)
    controller.inputRoutingMode = routing
    controller.ocrContextEnabled = enabled
    switch = root.findChild(QObject, "ocrContextSwitch")
    assert switch is not None and switch.property("checked") == enabled
    assert controller._settings.value("input/ocrContextEnabled", enabled, type=bool) == enabled
    event(phase="dictated", ready=True, revision=1, raw="", error="")
    event("settled", phase="dictated", revision=1, text="")
    controller._apply_runtime_status("[ASR] START t=2.000s")
    event(phase="listening", ready=True, revision=0, automatic_mode=routing == "auto",
          automatic_routing_version=3, routed_edit_mode=routing == "manual_routed_edit", routed_edit_version=1,
          original="原文", edit_source_text="原文", context_complete=True)
    controller._apply_runtime_session_started(42)
    capture = controller._voice_capture
    assert capture["future"] is raw and not raw.done()
    assert capture["context_enabled"] == enabled
    assert preparations == ([raw] if enabled else [])
    context = (controller._automatic_context if routing == "auto" else
        controller._routed_inline_edit_options((*controller._automatic_key(), 1))["screen_context"])
    assert (context is prepared) if enabled else (context == {})
    controller.ocrContextEnabled = not enabled
    # Mid-utterance toggle applies to the next capture, not a pending model request.
    assert capture["context_enabled"] == enabled and capture["model_context"] is context
    finish_asr(controller._modification_dataset, 42)
    raw.set_result(raw_capture())
    store = controller._modification_dataset
    record = store._interaction_data(store.interaction_id_for_session(42))
    assert len(record["screen_evidence"]["ocr_lines"]) == 4
    assert record["screen_evidence"]["screenshot_file"]
    assert record["context_preparation"]["status"] == ("pending" if enabled else "disabled")
    prepared.set_result({"status": "ok", "processing": "llm_compact", "context": "本句上文"})
