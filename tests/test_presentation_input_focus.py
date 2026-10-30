"""Confirmed shows tolerate uncertain input focus without cycling input sources."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication

from proximic_ring.voice.ime.system_input_source import InputSourceUnavailable
from test_app_gestures import route
from test_gesture_scenes import POWERPOINT, PRESENTATION, presentation, window_tree
from test_presentation_portability import native_backend
from test_ring_gestures import request


@pytest.fixture
def show_focus(presentation, monkeypatch):
    c, service, inline, backend, catalog, sent, messages = presentation
    inline._utterance_id = ""
    inline._view.update(phase="idle", ready=False, application=POWERPOINT)
    data = dict(state={}, front=(POWERPOINT, 42), source_calls=[])
    bridge = SimpleNamespace(epoch="e", connected=True, close=lambda: None)

    def focus_state(**_):
        if isinstance(data["state"], Exception):
            raise data["state"]
        return data["state"]

    bridge.focus_state = focus_state
    monkeypatch.setattr(inline, "_bridge", bridge)
    monkeypatch.setattr(inline, "_foreground_identity", lambda: data["front"])
    for name in ("capture_start_focus", "same_input_focus", "same_gesture_target"):
        monkeypatch.setattr(inline, name, getattr(type(inline), name).__get__(inline))

    def select(*args, **kwargs):
        data["source_calls"].append((args, kwargs))
        raise InputSourceUnavailable("input_method_source_shortcut_unavailable")

    monkeypatch.setattr("proximic_ring.voice.ime.system_input_source.activate_voice_input_source", select)
    return (*presentation, data)


def text_state(**changes):
    return dict(dict(epoch="e", client_id="editor", utterance_id="", application=POWERPOINT,
                     ready=True, selection=[0, 0]), **changes)


@pytest.mark.parametrize("gesture,shortcut", [("swipe-left", "Left"), ("swipe-right", "Right"), ("tap", "Escape")])
@pytest.mark.parametrize("context", ["nontext", "unknown"])
@pytest.mark.parametrize("focus", ["unbound", "disconnected", "timeout", "exception", "malformed", "nontext"])
def test_show_actions_ignore_uncertain_focus_and_disabled_source_shortcut(show_focus, gesture, shortcut, context, focus):
    c, service, inline, backend, _, sent, messages, data = show_focus
    backend.target = replace(backend.target, input_context=context)
    if focus == "disconnected": inline._bridge.connected = False
    if focus == "timeout": data["state"] = None
    if focus == "exception": data["state"] = TimeoutError()
    if focus == "malformed": data["state"] = text_state(ready=False, selection=None)
    if focus == "nontext": data["state"] = text_state(ready=False, selection=[-1, -1])
    assert not request(c, gesture)
    assert sent == [(POWERPOINT, shortcut)]
    assert not data["source_calls"] and not messages and not service.notice


@pytest.mark.parametrize("ready", [True, False])
def test_positive_imk_text_retains_priority_even_when_ax_claims_show(show_focus, monkeypatch, ready):
    c, _, _, backend, _, sent, _, data = show_focus
    data["state"] = text_state(ready=ready)
    monkeypatch.setattr(backend, "capture", lambda **_: pytest.fail("known IMK text must not query AX"))
    request(c, "swipe-left")
    assert not sent and not data["source_calls"]


@pytest.mark.parametrize("case", ["text", "editor", "blocked", "missing"])
def test_fallback_requires_current_show_and_no_positive_ax_text(show_focus, case):
    c, _, _, backend, _, sent, _, _ = show_focus
    if case == "text": backend.target = replace(backend.target, input_context="text")
    if case == "editor": backend.target = replace(backend.target, scene="")
    if case == "blocked": backend.target = replace(backend.target, blocked=True)
    if case == "missing": backend.target = None
    request(c, "swipe-left")
    assert not sent


@pytest.mark.parametrize("change", ["imk_text", "ax_text", "window", "exit", "foreground", "settings", "disconnect"])
def test_queued_show_action_rechecks_text_window_scene_and_configuration(show_focus, change):
    c, _, _, backend, catalog, sent, _, data = show_focus
    request(c, "swipe-left", deliver=False)
    if change == "imk_text": data["state"] = text_state()
    if change == "ax_text": backend.target = replace(backend.target, input_context="text")
    if change == "window": backend.target = replace(backend.target, window="another-window")
    if change == "exit": backend.target = replace(backend.target, scene="")
    if change == "foreground": data["front"] = ("another.app", 43)
    if change == "settings": catalog.clearApplicationBindings(POWERPOINT)
    if change == "disconnect": c._disconnect_event.set()
    QCoreApplication.processEvents()
    assert not sent and not data["source_calls"]


@pytest.mark.parametrize("state", [None, TimeoutError(), RuntimeError("focus unavailable")])
def test_dispatch_focus_failure_does_not_cancel_confirmed_show(show_focus, state):
    c, _, _, _, _, sent, _, data = show_focus
    request(c, "swipe-left", deliver=False)
    data["state"] = state
    QCoreApplication.processEvents()
    assert sent == [(POWERPOINT, "Left")] and not data["source_calls"]


def test_input_method_reconnection_during_probe_does_not_block_show(show_focus, monkeypatch):
    c, _, inline, _, _, sent, _, data = show_focus

    def reconnect(**_):
        inline._bridge.epoch = "new-connection"
        return None

    monkeypatch.setattr(inline._bridge, "focus_state", reconnect)
    request(c, "swipe-left")
    assert sent == [(POWERPOINT, "Left")] and not data["source_calls"]


@pytest.mark.parametrize("before,after", [("nontext", "unknown"), ("unknown", "nontext")])
def test_native_focus_uncertainty_between_capture_and_dispatch_is_allowed(show_focus, before, after):
    c, _, _, backend, _, sent, _, _ = show_focus
    backend.target = replace(backend.target, input_context=before)
    request(c, "swipe-right", deliver=False)
    backend.target = replace(backend.target, input_context=after, focus=None if after == "unknown" else "slide")
    QCoreApplication.processEvents()
    assert sent == [(POWERPOINT, "Right")]


@pytest.mark.parametrize("unavailable", ["disabled", "installing", "no_bridge"])
def test_unavailable_voice_input_does_not_block_show(show_focus, monkeypatch, unavailable):
    c, _, inline, _, _, sent, _, data = show_focus
    if unavailable == "disabled": inline._enabled = False
    if unavailable == "installing": inline._installing = True
    if unavailable == "no_bridge": monkeypatch.setattr(inline, "_bridge", None)
    request(c, "swipe-right")
    assert sent == [(POWERPOINT, "Right")] and not data["source_calls"]


def test_native_show_window_with_missing_ax_focus_still_dispatches(show_focus, monkeypatch):
    c, service, _, _, _, sent, _, data = show_focus
    window, _ = window_tree()
    backend, _, _ = native_backend(monkeypatch, window, None)
    service.backend = backend

    def post(target, shortcut, **_):
        assert target.scene == PRESENTATION and target.input_context == "unknown"
        assert backend.same_target(target)
        sent.append((target.bundle, shortcut))

    monkeypatch.setattr(backend, "post", post)
    data["state"] = None
    request(c, "swipe-right")
    assert sent == [(POWERPOINT, "Right")] and not data["source_calls"]


@pytest.mark.parametrize("stage", ["recognition", "validation", "both"])
def test_native_focus_timeout_preserves_independently_recognized_show(show_focus, monkeypatch, stage):
    c, service, _, _, _, sent, _, data = show_focus
    window, focus = window_tree()
    backend, _, _ = native_backend(monkeypatch, window, focus)
    service.backend = backend

    def timeout(*_, **__):
        raise TimeoutError("focus inspection timed out")

    if stage in {"recognition", "both"}:
        monkeypatch.setattr("proximic_ring.scenes.recognition.presentation.inspect_focus", timeout)
    if stage in {"validation", "both"}:
        monkeypatch.setattr("proximic_ring.desktop.app_shortcuts.inspect_focus", timeout)

    def post(target, shortcut, **_):
        assert target.scene == PRESENTATION and backend.same_target(target)
        sent.append((target.bundle, shortcut))

    monkeypatch.setattr(backend, "post", post)
    data["state"] = TimeoutError()
    request(c, "swipe-left")
    assert sent == [(POWERPOINT, "Left")] and not data["source_calls"]
