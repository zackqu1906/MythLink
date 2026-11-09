from concurrent.futures import Future

import pytest
from test_inline_ui import inline_ui
from test_shared_voice_capture import raw_capture, finish_asr
from proximic_ring.text_processing.context_preparation import PreparedContext


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
