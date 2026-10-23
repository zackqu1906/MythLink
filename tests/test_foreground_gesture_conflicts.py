"""Other applications cannot make the first voice start depend on AX focus."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from proximic_ring.ui.gestures.app_gesture_controller import InputSourceGestureEvent
from test_app_gestures import route
from test_application_menus import ACTION, configure
from test_gesture_scenes import POWERPOINT, PRESENTATION, presentation
from test_ring_gestures import request
from proximic_ring.voice.ime.focus_probe import InputFocus


def live_text(inline, monkeypatch, target):
    monkeypatch.setattr(inline, 'capture_start_focus', lambda: InputFocus('text',
        ('e', 'client', '', target.bundle, target.pid, (0, 0))))


def first_sentence(controller, inline):
    assert controller.setGestureBinding("confirm", 0, "tap")
    inline._utterance_id = ""
    inline._view.update(phase="idle", ready=True)


def scene_binding(service, monkeypatch, gesture="tap"):
    catalog = configure(service, monkeypatch, POWERPOINT)
    catalog.clearApplicationBindings(POWERPOINT)
    catalog.selectScene(PRESENTATION)
    assert catalog.setBinding(POWERPOINT, gesture, "powerpoint:end")
    return catalog


@pytest.mark.parametrize("bundle", ["com.tencent.xinWeChat", "com.openai.codex"])
@pytest.mark.parametrize("focus_state", ["missing", "dialog", "text"])
@pytest.mark.parametrize("scope", ["application", "scene"])
def test_other_app_binding_never_requires_ax_for_first_start(route, monkeypatch, bundle, focus_state, scope):
    c, service, inline, backend, sent, messages, _ = route
    first_sentence(c, inline)
    backend.target = replace(backend.target, bundle=bundle, blocked=focus_state == "dialog",
                             focus=None if focus_state == "missing" else "composer",
                             input_context="text" if focus_state == "text" else "unknown")
    if scope == "scene":
        catalog = scene_binding(service, monkeypatch)
        assert catalog.uses_scene("tap")
    else:
        catalog = configure(service, monkeypatch, POWERPOINT)
        assert catalog.setCustomBinding(POWERPOINT, "tap", "Window action", "Cmd+Return")
        assert catalog.uses("tap")
    assert not catalog.uses("tap", bundle=bundle) and not catalog.uses_scene("tap", bundle=bundle)
    live_text(inline, monkeypatch, backend.target)
    monkeypatch.setattr(backend, "capture", lambda **kw: pytest.fail("unrelated binding requested AX focus"))
    assert request(c, "tap")
    assert not sent and not messages


@pytest.mark.parametrize("context", ["text", "nontext", "unknown"])
def test_current_application_binding_still_arbitrates_start(route, monkeypatch, context):
    c, service, inline, backend, sent, _, _ = route
    first_sentence(c, inline)
    bundle = backend.target.bundle
    catalog = configure(service, monkeypatch, bundle)
    assert catalog.setBinding(bundle, "tap", ACTION["id"])
    backend.target = replace(backend.target, input_context=context)
    assert request(c, "tap") is (context == "text")
    assert bool(sent) is (context == "nontext")


@pytest.mark.parametrize("context", ["text", "nontext", "unknown"])
def test_current_scene_binding_still_arbitrates_start(presentation, context):
    c, _, inline, backend, _, sent, _ = presentation
    first_sentence(c, inline)
    backend.target = replace(backend.target, input_context=context)
    assert request(c, "tap") is (context == "text")
    assert sent == ([(POWERPOINT, "Escape")] if context == "nontext" else [])


@pytest.mark.parametrize("removed", [False, True])
def test_disabled_or_removed_scene_does_not_require_ax_for_start(presentation, monkeypatch, removed):
    c, _, inline, backend, catalog, sent, messages = presentation
    first_sentence(c, inline)
    if removed:
        assert catalog.removeApplication(POWERPOINT)
    else:
        assert catalog.disableScene(PRESENTATION)
    assert not catalog.uses_scene("tap", bundle=POWERPOINT)
    live_text(inline, monkeypatch, backend.target)
    monkeypatch.setattr(backend, "capture", lambda **kw: pytest.fail("disabled scene requested AX focus"))
    assert request(c, "tap") and not sent and not messages


def test_other_app_scene_does_not_request_scene_capture(route, monkeypatch):
    _, service, _, backend, _, _, _ = route
    scene_binding(service, monkeypatch, "swipe-left")
    backend.target = replace(backend.target, input_context="nontext")
    capture, calls = backend.capture, []
    def record(**options):
        calls.append(options)
        return capture(**options)
    monkeypatch.setattr(backend, "capture", record)
    service.envelope(SimpleNamespace(name="swipe-left"))
    assert calls == [dict(menu_action=True, focus_sensitive=False)]


@pytest.mark.parametrize("change", ["application", "pid", "unavailable"])
def test_scene_scope_cannot_follow_a_changed_or_unknown_foreground(route, monkeypatch, change):
    c, service, inline, backend, sent, messages, _ = route
    first_sentence(c, inline)
    scene_binding(service, monkeypatch)
    original = (backend.target.bundle, backend.target.pid)
    # IMK proves nontext for the original foreground, then native capture sees
    # a changed process/window. No AX text classification is involved.
    monkeypatch.setattr(inline, 'capture_start_focus', lambda: InputFocus('nontext',
        ('e', 'client', '', original[0], original[1], (-1, -1))))
    monkeypatch.setattr(service, "foreground_identity", lambda: None if change == "unavailable" else original)
    if change == "application":
        backend.target = replace(backend.target, bundle=POWERPOINT)
    elif change == "pid":
        backend.target = replace(backend.target, pid=original[1] + 1)
    event = service.envelope(SimpleNamespace(name="tap"))
    assert event.route == "blocked" and event.target is None
    assert event.capture_reason == ("target_unavailable" if change == "unavailable" else "foreground_changed")
    assert not sent and not messages


def test_global_binding_still_requires_focus_arbitration(route):
    c, _, inline, backend, sent, _, _ = route
    first_sentence(c, inline)
    assert c.ringGestures.setGlobalBinding("window_selector", "tap")
    backend.target = replace(backend.target, input_context="unknown")
    assert not request(c, "tap") and not sent


def test_other_app_binding_does_not_block_explicit_input_source(route, monkeypatch):
    _, service, _, backend, _, _, _ = route
    catalog = configure(service, monkeypatch, POWERPOINT)
    assert catalog.setCustomBinding(POWERPOINT, "snap", "Window action", "Cmd+Return")
    assert service.setInputSourceGesture("snap")
    monkeypatch.setattr("proximic_ring.ui.gestures.app_gesture_controller.foreground_pid", lambda: backend.target.pid)
    monkeypatch.setattr(backend, "capture", lambda **kw: pytest.fail("unrelated source binding requested AX focus"))
    event = service.envelope(SimpleNamespace(name="snap"))
    assert isinstance(event, InputSourceGestureEvent) and event.pid == backend.target.pid
