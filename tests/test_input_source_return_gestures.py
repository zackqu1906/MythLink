"""Retained voice actions resume before a gesture pins its sentence identity."""
from dataclasses import replace
from types import SimpleNamespace
import time

import pytest

from proximic_ring.gestures.gesture_settings import GestureBindings
from test_app_gestures import route
from test_inline_ui import inline_ui
from test_agent_ui import finish_fixture_voice


@pytest.mark.parametrize("gesture,command", [("swipe-left", "cancel"), ("swipe-down", "polish")])
def test_external_gesture_captures_resumed_sentence(route, monkeypatch, gesture, command):
    controller, service, inline, _, sent, messages, emit = route
    inline._view.update(phase="idle", ready=False)
    finished = []

    def resume():
        inline._client_id, inline._utterance_id = "resumed-client", "resumed-sentence"
        inline._view.update(phase="dictated", ready=True)
        finished.append(time.monotonic())
        return None

    monkeypatch.setattr(inline, "resume_gesture_failure", resume)
    event = emit(gesture)
    assert event.route == "input" and event.sentence == service.sentence()
    assert event.sentence[1:] == ("resumed-client", "resumed-sentence")
    assert event.created >= finished[0]
    assert messages[-1][0] == command and not sent


@pytest.mark.parametrize("gesture", ["swipe-left", "swipe-down"])
def test_failed_resume_blocks_input_and_external_fallback(route, monkeypatch, gesture):
    _, service, inline, backend, sent, messages, emit = route
    inline._view.update(phase="idle", ready=False)
    backend.target = replace(backend.target, input_context="nontext")
    monkeypatch.setattr(inline, "resume_gesture_failure", lambda: "input_method_resume_field_changed")
    monkeypatch.setattr(inline, "capture_start_focus", lambda **_: pytest.fail("failed resume must stop capture"))
    monkeypatch.setattr(backend, "capture", lambda **_: pytest.fail("failed resume must not become an outside action"))
    event = emit(gesture)
    assert event.route == "blocked" and event.capture_reason == "input_method_resume_field_changed"
    assert "已切换输入框" in service.notice
    assert any(e["reason"] == event.capture_reason and e["trace"] == event.trace_id
               and e["stage"] == "outcome" for e in service._diagnostics.recent())
    assert not sent and not messages


@pytest.mark.parametrize("gesture,audio_busy", [("swipe-left", True), ("swipe-down", True), ("swipe-up", False)])
def test_busy_and_unrelated_gestures_do_not_resume(route, monkeypatch, gesture, audio_busy):
    _, service, inline, _, _, _, _ = route
    monkeypatch.setattr(inline, "resume_gesture_failure", lambda: pytest.fail("must not resume this gesture"))
    service.envelope(SimpleNamespace(name=gesture), audio_busy=audio_busy)


@pytest.mark.parametrize("gesture", ["swipe-left", "swipe-down"])
@pytest.mark.parametrize("outcome", ["resumed", "failed", "editor_changed"])
def test_agent_worker_resumes_before_capturing_editor_sentence(inline_ui, monkeypatch, gesture, outcome):
    owner = inline_ui[0]
    agent, inline = owner.agent, owner._inline_input
    finish_fixture_voice(owner)
    agent._focused = True
    agent.editorFocused("task", True)
    captured = []
    agent._inputGestureReceived.connect(lambda *args: captured.append(args))

    def resume():
        inline._client_id, inline._utterance_id = "resumed-client", "resumed-sentence"
        if outcome == "editor_changed":
            agent.workspace.input_key = "another-editor"
        return outcome != "failed"

    monkeypatch.setattr(inline, "resume_for_gesture", resume)
    assert agent.filter_gesture(SimpleNamespace(name=gesture), owner._disconnect_event)
    if outcome == "resumed":
        assert len(captured) == 1
        assert captured[0][4][0] == owner._app_gestures.sentence()
        assert captured[0][4][0][1:] == ("resumed-client", "resumed-sentence")
    else:
        assert not captured


@pytest.mark.parametrize("outcome", ["resumed", "failed", "editor_changed"])
def test_recommendation_worker_preserves_hub_navigation_and_resumes_custom_undo(outcome):
    from proximic_ring.agent_workflow.ui.recommendation_input import RecommendationInput
    captured, resumed = [], []
    sentence = ["epoch", "client", "old-sentence"]
    bindings = replace(GestureBindings(), undo=("snap", ""))
    editor = SimpleNamespace(
        focused=True, _closed=False, key="field:1", interaction=SimpleNamespace(mode="word"),
        runtime=SimpleNamespace(executing=False), owns_input_focus=lambda: True,
        _navigation=SimpleNamespace(emit=lambda *args: captured.append(("navigation", args))),
        _inputGestureReceived=SimpleNamespace(emit=lambda *args: captured.append(("input", args))),
        generation=1,
    )

    def resume():
        resumed.append(True)
        sentence[1:] = ["resumed-client", "resumed-sentence"]
        if outcome == "editor_changed":
            editor.key = "field:2"
        return outcome != "failed"

    editor.owner = SimpleNamespace(
        _gesture_bindings=bindings,
        _touchpad=SimpleNamespace(strokeSession=SimpleNamespace(engaged=False)),
        _ring_gestures=SimpleNamespace(speech_busy=lambda: False),
        _inline_input=SimpleNamespace(resume_for_gesture=resume),
        _app_gestures=SimpleNamespace(sentence=lambda: tuple(sentence)),
    )
    assert RecommendationInput.filter_gesture(editor, SimpleNamespace(name="swipe-left"), object())
    assert captured[0][0] == "navigation" and not resumed
    captured.clear()
    assert RecommendationInput.filter_gesture(editor, SimpleNamespace(name="snap"), object())
    assert resumed == [True]
    if outcome == "resumed":
        assert captured[0][0] == "input" and captured[0][1][4][0] == tuple(sentence)
    else:
        assert not captured
