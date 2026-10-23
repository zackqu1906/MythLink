"""Swipe-down uses sentence ownership and an independent model operation."""
from dataclasses import replace
import threading

import pytest

from proximic_ring.text_processing import LLMSettings, OpenAICompatibleTextProcessor, TextProcessingRequest, TextProcessingResult, TextProcessingWorker
from proximic_ring.text_processing.model import TEXT_MODE_POLISH
from proximic_ring.text_processing.prompts import POLISH_PROMPT
from test_app_gestures import route
from test_inline_state import component, receive


def test_recording_down_waits_for_final_once_and_never_sends(route):
    c, service, inline, _, sent, messages, emit = route
    inline._view.update(phase="listening", has_composition=True)
    emit("swipe-down")
    emit("swipe-down")
    emit("swipe-up")  # The first pending intent owns this sentence.
    assert messages == [("finish", {})] and c._finish_utterance_event.is_set()
    inline._view.update(phase="dictated", has_composition=False)
    inline.settled.emit("dictated", "final")
    inline.settled.emit("dictated", "final")
    assert messages == [("finish", {}), ("polish", {"restore_raw_on_undo": False})]
    assert not sent and service._pending is None


def test_down_recognized_before_commit_preserves_recording_undo(route):
    c, service, inline, _, sent, messages, _ = route
    from types import SimpleNamespace
    inline._view.update(phase="listening", has_composition=True)
    event = service.envelope(SimpleNamespace(name="swipe-down"))
    inline._view.update(phase="dictated", has_composition=False)
    c._apply_gesture(event, c._disconnect_event)
    assert messages == [("polish", {"restore_raw_on_undo": False})] and not sent


@pytest.mark.parametrize("change", ["cancel", "convert", "focus", "app", "empty", "error", "disconnect", "settings", "sentence", "timeout"])
def test_pending_polish_cannot_escape_sentence(route, change):
    c, service, inline, backend, sent, messages, emit = route
    inline._view.update(phase="listening", has_composition=True)
    emit("swipe-down")
    if change == "cancel": emit("swipe-left")
    elif change == "convert": emit("swipe-right")
    elif change == "focus": backend.target = replace(backend.target, focus="other")
    elif change == "app": backend.target = None
    elif change == "disconnect": c._disconnect_event.set()
    elif change == "settings": service._generation += 1
    elif change == "sentence": inline._utterance_id = "new"
    elif change == "timeout": service._pending_timer.timeout.emit()
    inline._action_pending = None
    inline._view.update(phase="error" if change == "error" else "dictated", has_composition=False)
    inline.settled.emit(inline._view["phase"], "" if change == "empty" else "sentence")
    assert not sent and not any(kind == "polish" for kind, _ in messages)
    assert service._pending is None


@pytest.mark.parametrize("phase", ["idle", "editing", "edited", "polishing", "undone", "interrupted", "error"])
def test_down_requires_current_dictation(route, phase):
    _, _, inline, _, sent, messages, emit = route
    inline._view.update(phase=phase)
    emit("swipe-down")
    assert not messages and not sent


def test_polish_prompt_is_independent_and_only_gets_this_utterance(monkeypatch):
    processor = OpenAICompatibleTextProcessor()
    calls = []
    monkeypatch.setattr(processor, "_request_chat", lambda settings, **kw: calls.append(kw) or "明天见。")
    assert processor.polish_with_trace("嗯明天见", LLMSettings(enabled=True, model="test")) == ("明天见。", "明天见。")
    assert calls[0]["system_prompt"] == POLISH_PROMPT
    assert calls[0]["user_content"] == "<语音转写>\n嗯明天见\n</语音转写>"
    assert calls[0]["edit_tool"] is None


@pytest.mark.parametrize("response", ["", "  ", {"modified_text": "wrong route"}])
def test_polish_rejects_empty_or_edit_tool_output(monkeypatch, response):
    processor = OpenAICompatibleTextProcessor()
    monkeypatch.setattr(processor, "_request_chat", lambda *a, **kw: response)
    with pytest.raises(RuntimeError, match="已保留听写"):
        processor.polish_with_trace("原听写", LLMSettings(enabled=True, model="test"))


def test_worker_preserves_polish_mode_and_failure_fallback():
    class Processor:
        def polish_with_trace(self, text, settings):
            raise RuntimeError("offline")
        def process_with_collection_trace(self, *a, **kw):
            pytest.fail("polish entered edit/dictation processing")
    results = []
    ready = threading.Event()
    worker = TextProcessingWorker(processor=Processor(), on_result=lambda r: (results.append(r), ready.set()))
    try:
        worker.submit(TextProcessingRequest(1, 2, TEXT_MODE_POLISH, "原听写", LLMSettings(enabled=True, model="test")))
        assert ready.wait(2)
        assert results[0].mode == TEXT_MODE_POLISH
        assert results[0].final_text == "原听写" and results[0].error == "offline"
    finally:
        worker.close(wait=True)


def test_inline_polish_does_not_confirm_edit_intent_and_drops_stale_result(component):
    inline, _ = component
    inline.begin()
    receive(inline, phase="dictated", ready=True, raw="raw", revision=2)
    requested, edits = [], []
    inline.polishRequested.connect(lambda *args: requested.append(args))
    inline.editIntentConfirmed.connect(edits.append)
    assert inline.polish(restore_raw_on_undo=False)
    receive(inline, phase="polishing", ready=True, revision=3)
    receive(inline, "polish_requested", revision=3, text="raw")
    receive(inline, "polish_requested", revision=3, text="raw")
    assert len(requested) == 1 and not edits
    key = requested[0][0]
    inline.apply_polish(key, "polished", "")
    assert inline._bridge.messages[-1]["type"] == "polish_result"
    receive(inline, phase="dictated", ready=True, revision=4)
    before = list(inline._bridge.messages)
    inline.apply_polish(key, "late", "")
    assert inline._bridge.messages == before


def test_app_controller_submits_polish_even_if_automatic_dictation_llm_is_disabled(route, monkeypatch):
    c, _, inline, _, sent, messages, _ = route
    requests = []
    monkeypatch.setattr(c._text_processing_worker, "submit", requests.append)
    c._llm_enabled = False
    inline._view.update(phase="polishing", revision=3)
    key = (*c._app_gestures.sentence(), 3)
    c._submit_inline_polish(key, "本句", inline.target_reference())
    request = requests[0]
    assert request.mode == TEXT_MODE_POLISH and request.settings.enabled
    assert request.raw_text == "本句" and not request.target_text
    c._apply_text_processed(TextProcessingResult(request.request_id, request.session_id, TEXT_MODE_POLISH,
                                                "本句", "润色结果", 0.1, True))
    assert messages[-1] == ("polish_result", {"revision": 3, "text": "润色结果", "error": ""})
    assert not sent


def test_polish_log_keeps_its_own_prompt():
    from proximic_ring.voice.modification_dataset import ModificationDatasetCollector
    payload = ModificationDatasetCollector._llm_input(TEXT_MODE_POLISH, "本句", "不应读取的上下文")
    assert payload["system_prompt"] == POLISH_PROMPT
    assert payload["user_content"] == "<语音转写>\n本句\n</语音转写>"
    assert payload["target_text"] == ""
