"""Input focus wins shared gestures; independent app/global gestures stay usable."""
from dataclasses import replace
from types import SimpleNamespace
import json

import pytest
from PySide6.QtCore import QCoreApplication

from proximic_ring.gestures.gesture_settings import GlobalGestureBindings, GLOBAL_BINDINGS_KEY, GESTURE_LABELS
from proximic_ring.ui.desktop.application_mapping_controller import ApplicationMappingController, SETTINGS_KEY
from test_app_gestures import route
from test_application_menus import configure, BUNDLE, ACTION
from test_ring_gestures import request, deliver_gestures


def outside(backend, bundle=BUNDLE):
    backend.target = replace(backend.target, bundle=bundle, profile=bundle,
                             focus="page", role="AXGroup", input_context="nontext")


@pytest.mark.parametrize("gesture", list(GESTURE_LABELS))
def test_app_binding_can_share_every_input_or_global_gesture_without_disabling_voice(route, monkeypatch, gesture):
    c, service, inline, backend, sent, messages, _ = route
    catalog = configure(service, monkeypatch)
    before_voice = c.gestureBindings
    assert catalog.setBinding(BUNDLE, gesture, ACTION["id"])
    outside(backend)
    assert not request(c, gesture)
    assert sent == [(BUNDLE, "Ctrl+Tab")]
    assert not messages and c.gestureBindings == before_voice
    assert not catalog.voice_overridden(BUNDLE) and not catalog.voice_disabled_bundles()
    assert not catalog.voiceOverrideNotice
    backend.target = replace(backend.target, focus="composer", role="AXTextArea", input_context="text")
    assert request(c, "double-tap")
    assert len(sent) == 1


@pytest.mark.parametrize("gesture", ["swipe-left", "swipe-right"])
def test_text_focus_does_not_fall_through_even_when_input_action_is_unavailable(route, monkeypatch, gesture):
    c, service, _, backend, sent, _, _ = route
    catalog = configure(service, monkeypatch)
    backend.target = replace(backend.target, bundle=BUNDLE, profile=BUNDLE)
    assert catalog.setBinding(BUNDLE, gesture, ACTION["id"])
    calls = []
    monkeypatch.setattr(c.ringGestures._selector, "start", lambda *a: calls.append(True))
    request(c, gesture)
    assert not sent and not calls


@pytest.mark.parametrize("context", ["text", "nontext", "unknown"])
@pytest.mark.parametrize("scope", ["application", "global"])
@pytest.mark.parametrize("gesture", ["circle-clockwise", "clench", "index-pinch"])
def test_independent_gestures_ignore_input_focus_for_application_and_global_actions(route, monkeypatch, context, scope, gesture):
    c, service, _, backend, sent, messages, _ = route
    catalog = configure(service, monkeypatch)
    backend.target = replace(backend.target, bundle=BUNDLE, profile=BUNDLE, input_context=context)
    if gesture == "index-pinch":
        assert c.ringGestures.setGlobalBinding("show_menu", "")
    assert c.ringGestures.setGlobalBinding("window_selector", gesture)
    if scope == "application":
        assert catalog.setBinding(BUNDLE, gesture, ACTION["id"])
    starts, captures = [], []
    monkeypatch.setattr(c.ringGestures._selector, "start", lambda *args: starts.append(True))
    capture = backend.capture
    def record_capture(**options):
        captures.append(options)
        return capture(**options)
    monkeypatch.setattr(backend, "capture", record_capture)
    request(c, gesture)
    assert bool(sent) == (scope == "application") and bool(starts) == (scope == "global")
    assert captures == [dict(menu_action=True, focus_sensitive=False)] and not messages


@pytest.mark.parametrize("slot", [0, 1])
def test_input_conflicts_follow_primary_and_alternate_bindings(route, monkeypatch, slot):
    c, service, _, backend, sent, _, _ = route
    catalog = configure(service, monkeypatch)
    backend.target = replace(backend.target, bundle=BUNDLE, profile=BUNDLE)
    for gesture in ("swipe-left", "snap"):
        assert catalog.setBinding(BUNDLE, gesture, ACTION["id"])
    assert c.setGestureBinding("undo", slot, "snap")
    assert "snap" in catalog.voiceGestures
    request(c, "snap")  # Current input binding wins even when undo is unavailable.
    assert not sent
    request(c, "swipe-left")
    assert len(sent) == (1 if slot == 0 else 0)
    c.resetGestureBindings()
    service._last_dispatch = None
    assert "snap" not in catalog.voiceGestures
    request(c, "snap")
    assert len(sent) == (2 if slot == 0 else 1)


def test_left_swipe_undo_with_focus_and_application_action_after_blur(route, monkeypatch):
    c, service, inline, backend, sent, messages, _ = route
    catalog = configure(service, monkeypatch)
    backend.target = replace(backend.target, bundle=BUNDLE, profile=BUNDLE)
    inline._view.update(application=BUNDLE, phase="dictated")
    assert catalog.setBinding(BUNDLE, "swipe-left", ACTION["id"])
    request(c, "swipe-left")
    assert messages and not sent
    messages.clear()
    inline._action_pending = False
    inline._view.update(phase="dictated", awaiting_readback=False)
    outside(backend)
    request(c, "swipe-left")
    assert sent == [(BUNDLE, "Ctrl+Tab")] and not messages


def test_app_wins_global_then_removal_restores_global_with_no_setting_loss(route, monkeypatch):
    c, service, _, backend, sent, _, _ = route
    catalog = configure(service, monkeypatch)
    outside(backend)
    assert catalog.setBinding(BUNDLE, "swipe-left", ACTION["id"])
    stored = c._settings.value(SETTINGS_KEY)
    assert c.ringGestures.setGlobalBinding("window_selector", "swipe-left")
    assert catalog.canBind("swipe-left") and c._settings.value(SETTINGS_KEY) == stored
    assert "应用绑定优先" in catalog.globalConflictNotice
    starts = []
    monkeypatch.setattr(c.ringGestures._selector, "start", lambda req, conn: starts.append(req.name))
    request(c, "swipe-left")
    assert sent == [(BUNDLE, "Ctrl+Tab")] and starts == []
    assert catalog.setBinding(BUNDLE, "swipe-left", "")
    request(c, "swipe-left")
    assert starts == ["clench"]
    assert GlobalGestureBindings.from_json(c._settings.value(GLOBAL_BINDINGS_KEY)).window_selector == "swipe-left"


@pytest.mark.parametrize("bundle,profile", [("com.openai.codex", "codex"),
    ("com.tencent.xinWeChat", "wechat"), ("vendor.WorkBuddy", "workbuddy"), (BUNDLE, "")])
def test_unconfigured_app_uses_global_even_with_saved_legacy_profiles(route, monkeypatch, bundle, profile):
    c, service, _, backend, sent, _, _ = route
    outside(backend, bundle)
    backend.target = replace(backend.target, profile=profile)
    assert service._legacy_configured and not service.catalog.owns(bundle)
    assert c.ringGestures.setGlobalBinding("window_selector", "circle-clockwise")
    starts = []
    monkeypatch.setattr(c.ringGestures._selector, "start", lambda *args: starts.append(True))
    request(c, "circle-clockwise")
    assert starts == [True] and not sent
    catalog = configure(service, monkeypatch, bundle)
    assert catalog.setBinding(bundle, "circle-clockwise", ACTION["id"])
    request(c, "circle-clockwise")
    assert sent == [(profile, "Ctrl+Tab")] and len(starts) == 1
    catalog.removeApplication(bundle)
    request(c, "circle-clockwise")
    assert len(starts) == 2 and len(sent) == 1


@pytest.mark.parametrize("change", ["app", "pid", "window", "focus", "settings", "global", "generation", "disconnect", "busy", "capture_failure"])
@pytest.mark.parametrize("scope", ["application", "global"])
@pytest.mark.parametrize("gesture", ["clench", "swipe-left"])
def test_queued_actions_recheck_focus_only_for_input_conflicts(route, monkeypatch, scope, change, gesture):
    c, service, inline, backend, sent, messages, _ = route
    catalog = configure(service, monkeypatch)
    outside(backend)
    starts = []
    monkeypatch.setattr(c.ringGestures._selector, "start", lambda *args: starts.append(True))
    assert c.ringGestures.setGlobalBinding("window_selector", gesture)
    if scope == "application": assert catalog.setBinding(BUNDLE, gesture, ACTION["id"])
    if change == "capture_failure": monkeypatch.setattr(backend, "capture", lambda **kw: None)
    assert not request(c, gesture, deliver=False)
    if change == "app": backend.target = replace(backend.target, bundle="other")
    if change == "pid": backend.target = replace(backend.target, pid=43)
    if change == "window": backend.target = replace(backend.target, window="other")
    if change == "focus": backend.target = replace(backend.target, input_context="text", focus="composer")
    if change == "settings": catalog.clearApplicationBindings(BUNDLE)
    if change == "global": assert c.ringGestures.setGlobalBinding("window_selector", "snap")
    if change == "generation": c.ringGestures._generation += 1
    if change == "disconnect": c._disconnect_event.set()
    if change == "busy": inline._view["phase"] = "listening"
    deliver_gestures(c)
    if change == "focus" and gesture == "clench":
        assert bool(sent) == (scope == "application") and bool(starts) == (scope == "global")
    else:
        assert not sent and not starts
    assert not messages


@pytest.mark.parametrize("phase", ["listening", "finishing", "editing", "polishing"])
def test_busy_sentence_owns_left_swipe_after_focus_loss(route, monkeypatch, phase):
    c, service, inline, backend, sent, _, _ = route
    catalog = configure(service, monkeypatch)
    outside(backend)
    assert catalog.setBinding(BUNDLE, "swipe-left", ACTION["id"])
    inline._view.update(phase=phase)
    cancels = []
    monkeypatch.setattr(inline, "cancel", lambda: cancels.append(True))
    request(c, "swipe-left")
    assert cancels == [True] and not sent
    inline._view.update(phase="dictated")
    request(c, "swipe-left")
    assert sent == [(BUNDLE, "Ctrl+Tab")]


def test_input_event_cannot_turn_into_application_action_when_focus_is_lost_in_queue(route, monkeypatch):
    c, service, inline, backend, sent, messages, _ = route
    catalog = configure(service, monkeypatch)
    assert catalog.setBinding(BUNDLE, "swipe-left", ACTION["id"])
    backend.target = replace(backend.target, bundle=BUNDLE)
    inline._view.update(phase="dictated")
    request(c, "swipe-left", deliver=False)
    outside(backend)
    QCoreApplication.processEvents()
    assert not sent and not messages


def test_audio_busy_snapshot_never_replays_as_app_action_after_finalization(route, monkeypatch):
    c, service, _, backend, sent, _, _ = route
    configure(service, monkeypatch).setBinding(BUNDLE, "clench", ACTION["id"])
    outside(backend)
    request(c, "clench", busy=True, deliver=False)
    QCoreApplication.processEvents()
    assert not sent


@pytest.mark.parametrize("context", ["unknown", "blocked", "missing"])
def test_unavailable_imk_focus_blocks_shared_start_without_dispatching_shortcuts(route, context):
    c, _, _, backend, sent, _, _ = route
    backend.target = (None if context == "missing" else replace(backend.target,
                      input_context="unknown", blocked=context == "blocked"))
    assert not request(c, "circle-clockwise")
    assert not sent
    assert not request(c, "double-tap")


def test_global_editor_allows_shared_gestures_swaps_and_reloads_without_mode_toggle(route):
    c, _, _, _, _, _, _ = route
    ring = c.ringGestures
    assert "switch_mode" not in ring.globalBindings
    for gesture in GESTURE_LABELS:
        assert ring.globalActionOptions(gesture)
    assert ring.setGlobalGestureAction("clench", "show_menu")
    assert ring.globalBindings["show_menu"] == "clench"
    assert ring.globalBindings["window_selector"] == "index-pinch"
    assert ring.setGlobalGestureAction("swipe-left", "window_selector")
    assert ring.setGlobalGestureAction("swipe-up", "scroll_up")
    assert GlobalGestureBindings.from_json(c._settings.value(GLOBAL_BINDINGS_KEY)) == c._global_gesture_bindings
    assert not ring.setGlobalBinding("switch_mode", "middle-pinch")
    assert not ring.setGlobalGestureAction("unknown", "show_menu")
    migrated = GlobalGestureBindings.from_json(json.dumps(dict(show_menu="snap", switch_mode="middle-pinch", window_selector="clench")))
    assert migrated.show_menu == "snap" and not migrated.action_for("middle-pinch")


def test_app_configuration_roundtrip_keeps_shared_global_and_voice_bindings(route, monkeypatch):
    _, service, _, _, _, _, _ = route
    catalog = configure(service, monkeypatch)
    assert catalog.setBinding(BUNDLE, "clench", ACTION["id"])
    assert catalog.setBinding(BUNDLE, "double-tap", ACTION["id"])
    restored = ApplicationMappingController(service)
    try:
        assert restored.regularBindings == catalog.regularBindings
        assert not restored.voice_disabled_bundles()
    finally:
        restored.close()
