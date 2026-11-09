from concurrent.futures import Future
import json
from types import SimpleNamespace

import numpy as np
import pytest

from proximic_ring.text_processing.screen_context import ScreenContext, filter_context
from proximic_ring.voice.modification_dataset import ModificationDatasetCollector
from test_automatic_history import PNG


def raw_capture():
    return ScreenContext({"source": "screen_ocr", "status": "ok", "processing": "raw",
                          "application": "com.tencent.xinWeChat", "coverage": "visible_only",
                          "capture_id": "b" * 32, "layout": {"sidebar": .3, "history_bottom": .76}},
                         evidence={"screenshot_png": PNG, "ocr_scope": "target_window", "ocr_lines": [
                             {"text": "联系人", "box": [.1, .2, .1, .02], "confidence": .9},
                             {"text": "16:29", "box": [.5, .3, .1, .02], "confidence": .99},
                             {"text": "请联系张伟", "box": [.4, .4, .3, .02], "confidence": .97, "speaker": "other"},
                             {"text": "输入框草稿", "box": [.4, .85, .3, .02], "confidence": .95},
                         ]})


def finish_asr(store, session=7):
    store.record_audio(session, np.zeros(160, dtype=np.float32))
    store.record_asr_update(SimpleNamespace(session_id=session, text="你好", is_final=True,
                                          backend="test", model="test", latency_s=.1, audio_duration_s=.01))


@pytest.mark.parametrize("routing", ["manual", "auto"])
def test_late_capture_is_saved_without_classification_or_generation(tmp_path, routing):
    saved = []
    store = ModificationDatasetCollector(tmp_path, "user", on_saved=saved.append)
    future = Future()
    store.record_screen_capture(7, future, routing=routing, requested_mode="dictation",
                                input_context={"original": "原始输入", "selection": [4, 0]})
    finish_asr(store)
    identifier = store.interaction_id_for_session(7)
    before = store.load_entries()[0]["detailsRevision"]
    assert store._interaction_data(identifier)["screen_capture"]["status"] == "pending"
    # A new sentence begins while OCR for the first sentence is still running.
    store.begin_session(8)
    future.set_result(raw_capture())
    record = store._interaction_data(identifier)
    assert record["mode"]["routing"] == routing
    assert not record["llm"]["requests"] and "router_trace" not in record["mode"]
    assert record["input_context"]["original"] == "原始输入"
    assert len(record["screen_evidence"]["ocr_lines"]) == 4
    assert all("disposition" not in line for line in record["screen_evidence"]["ocr_lines"])
    assert (store._interaction_dir(identifier) / record["screen_evidence"]["screenshot_file"]).read_bytes() == PNG
    assert "screen_capture" not in store._interaction_data(store.interaction_id_for_session(8))
    assert len(saved) == 2 and store.load_entries()[0]["detailsRevision"] != before


def test_filtering_keeps_original_lines_and_never_puts_evidence_in_model_mapping():
    raw = raw_capture()
    snapshot = json.dumps(raw.evidence["ocr_lines"], ensure_ascii=False)
    filtered = filter_context(raw)
    assert filtered["messages"] == [{"speaker": "other", "text": "请联系张伟"}]
    assert [line["disposition"] for line in filtered.evidence["ocr_filter_lines"]] == [
        "sidebar", "timestamp", "passed_filter", "outside_history"]
    assert json.dumps(raw.evidence["ocr_lines"], ensure_ascii=False) == snapshot
    assert "messages" not in raw and raw["processing"] == "raw"
    assert filtered.evidence["ocr_lines"] == raw.evidence["ocr_lines"]
    assert "screenshot_png" not in json.dumps(filtered) and "confidence" not in json.dumps(filtered)
    assert filter_context(filtered) is filtered


def test_failed_layout_keeps_raw_ocr_and_screenshot_for_manual_history():
    raw = raw_capture()
    raw["layout_error"] = "无法定位聊天区域"
    filtered = filter_context(raw)
    assert filtered["status"] == "unavailable" and filtered["messages"] == []
    assert raw["status"] == "ok" and len(raw.evidence["ocr_lines"]) == 4
    assert filtered.evidence["screenshot_png"] == PNG


def test_automatic_worker_filters_the_shared_frame_before_classification():
    import threading
    from proximic_ring.text_processing.model import InputModeRoutingRequest, LLMSettings
    from proximic_ring.text_processing.task_routing import RoutingDecision
    from proximic_ring.text_processing.worker import TextProcessingWorker
    seen, routed = [], []
    done = threading.Event()
    def classify(speech, target, context, **kwargs):
        seen.append(context)
        return RoutingDecision("dictation", None), "{}"
    router = SimpleNamespace(classify=classify, close=lambda: None)
    worker = TextProcessingWorker(intent_router=router, on_result=lambda _: None,
        on_routing_result=lambda result: (routed.append(result), done.set()))
    raw = raw_capture()
    try:
        worker.submit_routing(InputModeRoutingRequest(1, 7, "你好", LLMSettings(), router_backend="clef", screen_context=raw))
        assert done.wait(2) and not routed[0].error
        assert seen[0]["messages"] == [{"speaker": "other", "text": "请联系张伟"}]
        assert len(raw.evidence["ocr_lines"]) == 4 and raw["processing"] == "raw"
    finally:
        worker.close(wait=True)


def test_vision_runs_full_window_even_when_chat_layout_is_unavailable(monkeypatch):
    import sys
    from proximic_ring.text_processing.screen_context import recognize_image
    quartz = SimpleNamespace(CGImageGetWidth=lambda _: 400, CGImageGetHeight=lambda _: 300,
        CGBitmapContextCreate=lambda *args: object(), CGColorSpaceCreateDeviceRGB=lambda: None,
        kCGImageAlphaPremultipliedLast=1, kCGBitmapByteOrder32Big=2,
        CGContextDrawImage=lambda *args: None, CGRectMake=lambda *args: args)
    class Request:
        def setRecognitionLevel_(self, *_): pass
        def setRecognitionLanguages_(self, *_): pass
        def setUsesLanguageCorrection_(self, *_): pass
        def setRegionOfInterest_(self, *_): pytest.fail("shared OCR must include the full window")
        def results(self):
            return [SimpleNamespace(topCandidates_=lambda _: [SimpleNamespace(string=lambda: "联系人", confidence=lambda: .99)],
                boundingBox=lambda: SimpleNamespace(origin=SimpleNamespace(x=.1, y=.7), size=SimpleNamespace(width=.2, height=.05)))]
    request = Request()
    performed = []
    handler = SimpleNamespace(performRequests_error_=lambda *args: performed.append(args) or (True, None))
    vision = SimpleNamespace(VNRequestTextRecognitionLevelAccurate=1,
        VNRecognizeTextRequest=SimpleNamespace(alloc=lambda: SimpleNamespace(init=lambda: request)),
        VNImageRequestHandler=SimpleNamespace(alloc=lambda: SimpleNamespace(initWithCGImage_options_=lambda *args: handler)))
    monkeypatch.setitem(sys.modules, "Quartz", quartz)
    monkeypatch.setitem(sys.modules, "Vision", vision)
    raw = recognize_image(object(), "com.tencent.xinWeChat")
    assert performed and raw["layout_error"] and raw["status"] == "ok"
    assert raw.evidence["ocr_lines"][0]["text"] == "联系人"
    assert raw.evidence["ocr_lines"][0]["box"] == pytest.approx([.1, .25, .2, .05])


@pytest.mark.parametrize("action", ["delete", "clear", "reset", "close"])
def test_late_capture_cannot_resurrect_or_overwrite_a_reused_session(tmp_path, action):
    store = ModificationDatasetCollector(tmp_path, "user")
    future = Future()
    store.record_screen_capture(7, future, routing="manual")
    identifier = store.interaction_id_for_session(7)
    if action == "delete":
        store.delete_entry(identifier)
    elif action == "clear":
        store.clear()
    elif action == "reset":
        store.reset_runtime()
    else:
        store.close()
    if action in {"clear", "reset"}:
        new_id = store.begin_session(7)
    future.set_result(raw_capture())
    if action in {"delete", "clear"}:
        # clear may reuse a millisecond directory name; never receive the old frame.
        assert not list(store.interactions_root.glob("*/screen-*.png"))
    else:
        assert "screen_evidence" not in store._interaction_data(identifier)
    if action in {"clear", "reset"}:
        assert "screen_evidence" not in store._interaction_data(new_id)


def test_cancelled_record_still_receives_ocr_and_failed_capture_has_a_reason(tmp_path):
    store = ModificationDatasetCollector(tmp_path, "user")
    future = Future()
    store.record_screen_capture(7, future, routing="manual")
    finish_asr(store)
    store.record_application(session_id=7, action="cancelled", mode="dictation", method="input_method")
    future.set_result(raw_capture())
    record = store._interaction_data(store.interaction_id_for_session(7))
    assert record["outcome"]["status"] == "cancelled"
    assert record["screen_evidence"]["screenshot_file"]
    failed = Future()
    store.record_screen_capture(8, failed, routing="manual")
    failed.set_exception(RuntimeError("屏幕录制不可用"))
    assert store._interaction_data(store.interaction_id_for_session(8))["screen_capture"]["reason"] == "屏幕录制不可用"


def test_details_include_all_evidence_and_sidecars_without_expanding_list_rows(tmp_path):
    store = ModificationDatasetCollector(tmp_path, "user")
    store.record_screen_capture(7, raw_capture(), routing="manual", input_context={"original": "完整原文"})
    finish_asr(store)
    store.record_asr_context(7, {"status": "sent", "source": "input_method", "context_data": [],
                                 "reason": "input_method_not_ready"})
    store.record_imu_samples(7, [{"t": 12, "ax": .3}], alignment_method="audio")
    identifier = store.interaction_id_for_session(7)
    record = store._interaction_data(identifier)
    record["future_evidence"] = {"unknown": "仍需显示"}
    store._write_json(store._interaction_path(identifier), record)
    assert "sections" not in store.load_entries()[0]
    details = store.load_details(identifier)
    sections = {section["key"]: section["text"] for section in details["sections"]}
    assert "输入框草稿" in sections["raw_ocr"] and "联系人" in sections["raw_ocr"]
    assert "完整原文" in sections["input_context"] and "仍需显示" in sections["metadata"]
    assert '"ax": 0.3' in sections["imu_samples"]
    assert "screen_captured" in sections["events"] and "你好" in sections["asr_updates"]
    assert "未保存有效上下文文字" in details["asrContextStatus"]
    assert "ocr_filter" not in sections
    assert store.load_details("../escape")["error"]
    outside = tmp_path / "outside.jsonl"
    outside.write_text("do not show")
    (store._interaction_dir(identifier) / "events.jsonl").unlink()
    (store._interaction_dir(identifier) / "events.jsonl").symlink_to(outside)
    assert "do not show" not in json.dumps(store.load_details(identifier))
