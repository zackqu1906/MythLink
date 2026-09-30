"""Voice-group ownership and user-configured global reservations."""
from dataclasses import replace
from types import SimpleNamespace
import json

import pytest
from PySide6.QtCore import QCoreApplication

from proximic_ring.gesture_settings import GlobalGestureBindings, GLOBAL_BINDINGS_KEY, VOICE_GESTURE_GROUP
from proximic_ring.ui.application_mapping_controller import ApplicationMappingController, SETTINGS_KEY
from test_app_gestures import route
from test_application_menus import configure, BUNDLE, ACTION
from test_ring_gestures import request
from test_gesture_scenes import presentation, POWERPOINT, PRESENTATION


@pytest.mark.parametrize("mode", ["input", "operation"])
@pytest.mark.parametrize("gesture", sorted(VOICE_GESTURE_GROUP))
def test_one_override_claims_whole_group_only_in_that_foreground_app(route, monkeypatch, mode, gesture):
    c, service, inline, backend, sent, messages, _ = route
    catalog = configure(service, monkeypatch)
    backend.target = replace(backend.target, bundle=BUNDLE, profile=BUNDLE)
    before_voice, before_view = c.gestureBindings, dict(inline._view)
    assert catalog.setBinding(BUNDLE, gesture, ACTION["id"])
    c.ringGestures._mode = mode
    for name in sorted(VOICE_GESTURE_GROUP):
        assert not request(c, name)  # Consumed ahead of audio, Enter and field selection.
    assert sent == [(BUNDLE, "Ctrl+Tab")]
    assert not messages and inline._view == before_view and c.gestureBindings == before_voice
    assert not c.ringGestures._fields.picker.active.is_set()
    assert catalog.voice_overridden(BUNDLE) and "无法使用手势语音输入" in catalog.voiceOverrideNotice
    backend.target = replace(backend.target, bundle="test.other.app", profile="other")
    c.ringGestures._mode = "input"
    assert request(c, "tap")
    backend.target = replace(backend.target, bundle=BUNDLE, profile=BUNDLE)
    assert catalog.setBinding(BUNDLE, gesture, "")
    assert request(c, "tap") and not catalog.voiceOverrideNotice


def test_group_is_restored_only_after_last_override_is_removed_and_survives_reload(route, monkeypatch):
    _, service, _, _, _, _, _ = route
    catalog = configure(service, monkeypatch)
    assert catalog.setBinding(BUNDLE, "tap", ACTION["id"])
    assert catalog.setBinding(BUNDLE, "swipe-left", ACTION["id"])
    assert catalog.setBinding(BUNDLE, "tap", "")
    assert catalog.voice_overridden(BUNDLE)
    restored = ApplicationMappingController(service)
    try:
        assert restored.voice_overridden(BUNDLE)
        assert restored.regularBindings == catalog.regularBindings
    finally:
        restored.close()
    assert catalog.removeApplication(BUNDLE)
    assert not catalog.voice_overridden(BUNDLE)


@pytest.mark.parametrize("change", ["app", "pid", "window", "settings", "global", "mode", "disconnect", "busy", "capture_failure"])
def test_claimed_override_never_falls_back_or_posts_to_stale_context(route, monkeypatch, change):
    c, service, inline, backend, sent, messages, _ = route
    catalog = configure(service, monkeypatch)
    backend.target = replace(backend.target, bundle=BUNDLE, profile=BUNDLE)
    assert catalog.setBinding(BUNDLE, "tap", ACTION["id"])
    if change == "capture_failure":
        monkeypatch.setattr(backend, "capture", lambda **kw: None)
    assert not request(c, "tap", deliver=False)
    if change == "app": backend.target = replace(backend.target, bundle="other")
    if change == "pid": backend.target = replace(backend.target, pid=43)
    if change == "window": backend.target = replace(backend.target, window="other")
    if change == "settings": catalog.clearApplicationBindings(BUNDLE)
    if change == "global": assert c.ringGestures.setGlobalBinding("window_selector", "snap")
    if change == "mode": c.ringGestures._generation += 1
    if change == "disconnect": c._disconnect_event.set()
    if change == "busy": inline._view["phase"] = "listening"
    QCoreApplication.processEvents()
    assert sent == [] and messages == []


def test_audio_busy_is_consumed_without_posting_override(route, monkeypatch):
    c, service, _, backend, sent, _, _ = route
    catalog = configure(service, monkeypatch)
    backend.target = replace(backend.target, bundle=BUNDLE)
    assert catalog.setBinding(BUNDLE, "tap", ACTION["id"])
    assert not request(c, "tap", busy=True)
    assert not sent


def test_old_field_picker_cannot_start_voice_after_switch_to_overridden_app(route, monkeypatch):
    c, service, _, backend, sent, messages, _ = route
    catalog = configure(service, monkeypatch)
    assert catalog.setBinding(BUNDLE, "swipe-left", ACTION["id"])
    c.ringGestures._fields.picker.active.set()
    backend.target = replace(backend.target, bundle=BUNDLE)
    assert not request(c, "tap")
    assert not sent and not messages


def test_global_reassignment_releases_old_gesture_and_preserves_suspended_binding(route, monkeypatch):
    c, service, _, backend, sent, _, _ = route
    catalog = configure(service, monkeypatch)
    backend.target = replace(backend.target, bundle=BUNDLE, profile=BUNDLE)
    assert catalog.setBinding(BUNDLE, "snap", ACTION["id"])
    stored = c._settings.value(SETTINGS_KEY)
    assert c.ringGestures.setGlobalBinding("window_selector", "snap")
    assert not catalog.canBind("snap") and catalog.canBind("clench")
    assert catalog.for_target(BUNDLE) == {} and "弹指" in catalog.globalConflictNotice
    assert c._settings.value(SETTINGS_KEY) == stored
    starts = []
    monkeypatch.setattr(c.ringGestures._selector, "start", lambda request, conn: starts.append(request.name))
    assert not request(c, "snap") and starts == ["clench"] and not sent
    assert catalog.setBinding(BUNDLE, "clench", ACTION["id"])
    event = c.ringGestures.envelope(SimpleNamespace(name="clench"))
    c._apply_gesture(event, c._disconnect_event)
    assert sent == [(BUNDLE, "Ctrl+Tab")] and starts == ["clench"]
    restored = GlobalGestureBindings.from_json(c._settings.value(GLOBAL_BINDINGS_KEY))
    assert restored.window_selector == "snap"
    assert c.ringGestures.setGlobalBinding("window_selector", "clench")
    assert catalog.canBind("snap") and not catalog.canBind("clench")
    assert "menu:snap" in catalog.for_target(BUNDLE) and "menu:clench" not in catalog.for_target(BUNDLE)
    assert json.loads(c._settings.value(SETTINGS_KEY))[BUNDLE]["bindings"]["clench"]


@pytest.mark.parametrize("action,old,new", [("show_menu", "index-pinch", "snap"), ("switch_mode", "middle-pinch", "circle-clockwise")])
def test_remapped_hint_and_mode_actions_run_on_new_physical_gesture(route, monkeypatch, action, old, new):
    from PySide6.QtTest import QTest
    c, service, _, backend, _, _, _ = route
    catalog = configure(service, monkeypatch)
    monkeypatch.setattr(backend, "capture", lambda **kwargs: backend.target)
    assert c.ringGestures.setGlobalBinding(action, new)
    assert catalog.canBind(old) and not catalog.canBind(new)
    shown = []
    c.ringGestures.showRequested.connect(lambda *args: shown.append(args))
    assert request(c, old)  # Old physical gesture is no longer a global command.
    assert not request(c, new)
    if action == "show_menu":
        # Hints now resolve the current app asynchronously even without scenes.
        for _ in range(40):
            if shown: break
            QTest.qWait(25)
        assert shown == [("input", "")]
    else: assert c.ringGestures.mode == "operation"


def test_global_actions_reject_voice_group_duplicate_and_active_sentence(route):
    c, _, inline, _, _, _, _ = route
    before = c.ringGestures.globalBindings
    for gesture in VOICE_GESTURE_GROUP | {"middle-pinch"}:
        assert not c.ringGestures.setGlobalBinding("show_menu", gesture)
    inline._view["phase"] = "listening"
    assert not c.ringGestures.setGlobalBinding("show_menu", "snap")
    assert c.ringGestures.globalBindings == before
    assert not c._settings.contains(GLOBAL_BINDINGS_KEY)


def test_gesture_first_global_editor_swaps_atomically_and_persists(route, monkeypatch):
    c, service, _, _, _, _, _ = route
    catalog = configure(service, monkeypatch)
    before_voice = c.gestureBindings
    snapshots = []
    c.ringGestures.changed.connect(lambda: snapshots.append(c.ringGestures.globalBindings))
    assert c.ringGestures.setGlobalGestureAction("clench", "show_menu")
    expected = dict(show_menu="clench", switch_mode="middle-pinch", window_selector="index-pinch")
    assert snapshots == [expected]
    assert GlobalGestureBindings.from_json(c._settings.value(GLOBAL_BINDINGS_KEY)).as_dict() == expected
    assert catalog.globalOccupancy["clench"] == "手势提示"
    assert catalog.globalOccupancy["index-pinch"] == "窗口选择"
    assert c.ringGestures.setGlobalGestureAction("clench", "show_menu")
    assert len(snapshots) == 1  # Saving the same assignment is a no-op.
    assert c.ringGestures.setGlobalGestureAction("snap", "window_selector")
    assert catalog.canBind("index-pinch") and not catalog.canBind("snap")
    assert c.gestureBindings == before_voice


@pytest.mark.parametrize("busy", ["voice", "fields", "scroll"])
def test_gesture_first_global_editor_rejects_reserved_gestures_and_busy_swap(route, busy):
    c, _, inline, _, _, _, _ = route
    before = c.ringGestures.globalBindings
    for key in VOICE_GESTURE_GROUP | {"unknown"}:
        assert not c.ringGestures.globalActionOptions(key)
        assert not c.ringGestures.setGlobalGestureAction(key, "show_menu")
    assert not c.ringGestures.setGlobalGestureAction("clench", "unknown")
    if busy == "voice": inline._view["phase"] = "listening"
    elif busy == "fields": c.ringGestures._fields.applying.set()
    else: c.ringGestures._scroll.applying.set()
    try:
        assert not c.ringGestures.setGlobalGestureAction("clench", "show_menu")
        assert c.ringGestures.globalBindings == before
        assert not c._settings.contains(GLOBAL_BINDINGS_KEY)
    finally:
        c.ringGestures._fields.applying.clear()
        c.ringGestures._scroll.applying.clear()


def test_regular_and_scene_ownership_are_independent_and_unassigned_scene_gestures_stay_silent(presentation):
    c, service, _, backend, catalog, sent, _ = presentation
    catalog.selectScene("regular")
    assert catalog.setCustomBinding(POWERPOINT, "swipe-left", "常规上一项", "Cmd+Left")
    backend.target = replace(backend.target, scene="", input_context="text")
    assert not request(c, "swipe-left") and sent[-1] == (POWERPOINT, "Cmd+Left")
    assert not request(c, "tap") and len(sent) == 1
    backend.target = replace(backend.target, scene=PRESENTATION, input_context="nontext")
    assert not request(c, "swipe-left") and sent[-1] == (POWERPOINT, "Left")
    catalog.selectScene(PRESENTATION)
    assert catalog.setBinding(POWERPOINT, "swipe-left", "")
    assert not request(c, "swipe-left") and len(sent) == 2  # No regular fallback inside the scene.
    catalog.selectScene("regular")
    assert catalog.setBinding(POWERPOINT, "swipe-left", "")
    assert not request(c, "swipe-left") and not catalog.voice_overridden(POWERPOINT)
    backend.target = replace(backend.target, scene="", input_context="text")
    assert request(c, "tap")


@pytest.mark.parametrize("operation", ["focus_probe", "focus_plan", "focus_apply", "focus_selection"])
def test_native_worker_does_not_focus_fields_in_voice_overridden_app(monkeypatch, operation):
    from proximic_ring import native_access_worker as worker
    from proximic_ring.mac_permissions import PermissionState
    from proximic_ring.app_shortcuts import ShortcutTarget
    monkeypatch.setattr(worker, "read_permission_state", lambda: PermissionState(True, False))
    dispatcher = worker.Dispatcher()
    dispatcher.shortcuts = SimpleNamespace(capture=lambda **kw: ShortcutTarget(BUNDLE, 42, BUNDLE))
    dispatcher.text_focus = SimpleNamespace(plans={"old": object()},
        handle=lambda *a, **kw: pytest.fail("Voice overrides cannot change text focus"))
    result = dispatcher.handle(dict(operation=operation, voice_disabled_apps=[BUNDLE], plan="old"))
    assert result["result"]["status"] == "voice_overridden"
    if operation == "focus_apply": assert dispatcher.text_focus.plans == {}
