from concurrent.futures import Future

import pytest

from test_inline_ui import inline_ui
from test_shared_voice_capture import raw_capture, finish_asr


@pytest.mark.parametrize("routing", ["manual", "auto"])
@pytest.mark.parametrize("ready_first", [True, False])
def test_native_start_binds_one_shared_capture_in_both_orders(inline_ui, monkeypatch, routing, ready_first):
    controller, bridge, requests, root, event = inline_ui
    future, captures = Future(), []
    monkeypatch.setattr(controller._text_processing_worker, "capture_context", lambda target: captures.append(target) or future)
    monkeypatch.setattr(controller._text_processing_worker, "warmup_router", lambda: None)
    controller.inputRoutingMode = routing
    event(phase="dictated", ready=True, revision=1, raw="", error="")
    event("settled", phase="dictated", revision=1, text="")
    controller._apply_runtime_status("[ASR] START t=2.000s")
    if not ready_first:
        controller._apply_runtime_session_started(42)
        pending = controller._modification_dataset._interaction_data(
            controller._modification_dataset.interaction_id_for_session(42))
        assert pending["input_context"]["status"] == "pending"
        assert "original" not in pending["input_context"]
    event(phase="listening", ready=True, revision=0, automatic_mode=routing == "auto",
          automatic_routing_version=3, original="录音开始前的原文", edit_source_text="原文",
          selection=[4, 0], context_complete=True)
    if ready_first:
        controller._apply_runtime_session_started(42)
    assert len(captures) == 1
    if routing == "auto":
        assert controller._automatic_context is controller._voice_capture["model_context"]
        assert controller._automatic_context is not future
    else:
        assert controller._automatic_context is None and not requests
    finish_asr(controller._modification_dataset, 42)
    # Cancelling classification must not cancel the shared recording future.
    controller._cancel_automatic_voice()
    assert not future.cancelled()
    future.set_result(raw_capture())
    record = controller._modification_dataset._interaction_data(
        controller._modification_dataset.interaction_id_for_session(42))
    assert record["mode"]["routing"] == routing
    assert record["input_context"]["original"] == "录音开始前的原文"
    assert record["screen_capture"]["processing"] == "raw"
    assert len(record["screen_evidence"]["ocr_lines"]) == 4
    assert "ocr_filter_lines" not in record["screen_evidence"]


def test_recording_without_native_begin_still_saves_raw_capture(inline_ui, monkeypatch):
    controller, bridge, requests, root, event = inline_ui
    future = Future()
    monkeypatch.setattr(controller._text_processing_worker, "capture_context", lambda target: future)
    event(phase="dictated", ready=True, revision=1, raw="", error="")
    event("settled", phase="dictated", revision=1, text="")
    controller._apply_runtime_status("[ASR] START t=2.000s")
    controller._apply_runtime_session_started(43)
    future.set_result(raw_capture())
    record = controller._modification_dataset._interaction_data(
        controller._modification_dataset.interaction_id_for_session(43))
    assert record["input_context"]["status"] == "pending"
    assert record["screen_capture"]["status"] == "ok"
    assert len(record["screen_evidence"]["ocr_lines"]) == 4


def test_manual_native_edit_does_not_send_shared_raw_ocr_to_generation(inline_ui, monkeypatch):
    controller, bridge, requests, root, event = inline_ui
    controller._voice_capture = {"future": raw_capture(), "bound": True}
    controller._latest_asr_session_id = 7
    controller._submit_inline_edit((*controller._automatic_key(), 1), "把三点改成四点", "明天三点开会",
                                   controller._inline_input.target_reference())
    assert len(requests) == 1
    assert requests[0].screen_context is None and requests[0].task_type == ""


def test_open_manual_details_refresh_when_ocr_finishes_in_same_millisecond(inline_ui, monkeypatch):
    from PySide6.QtCore import QObject
    from PySide6.QtTest import QTest
    from test_ui_shell import visual_child
    import proximic_ring.voice.modification_dataset as dataset
    monkeypatch.setattr(dataset, "_utc_now", lambda: "2026-11-09T08:00:00.123+00:00")
    controller, bridge, requests, root, event = inline_ui
    future = Future()
    store = controller._modification_dataset
    store.record_screen_capture(7, future, routing="manual")
    finish_asr(store)
    controller._refresh_voice_history_entries()
    root.setProperty("currentPage", 4); root.show(); QTest.qWait(80)
    entry = visual_child(root.findChild(QObject, "voiceHistoryList"), "voiceHistoryEntry")
    entry.setProperty("detailsVisible", True); QTest.qWait(30)
    before = visual_child(entry, "voiceHistoryDetail_screen_capture").property("text")
    assert '"status": "pending"' in before
    future.set_result(raw_capture()); QTest.qWait(100)
    assert entry.property("detailsVisible")
    assert "联系人" in visual_child(entry, "voiceHistoryDetail_raw_ocr").property("text")
    assert visual_child(entry, "voiceHistoryScreenshot").property("paintedWidth") > 0
