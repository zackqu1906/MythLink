from dataclasses import replace
from types import SimpleNamespace
import json

import pytest

from proximic_ring.gestures.app_gestures import (AppBinding, KEY_CODES, action_phase, default_profiles,
    migrate_voice_defaults, normalize_shortcut, profiles_from_json, profiles_to_json,
    validate_profiles, profile_for_application)
from proximic_ring.desktop.app_shortcuts import ShortcutTarget
from proximic_ring.gestures.gesture_settings import BoundGestureEvent, GestureBindings
from test_interaction_controls import _controller, _close


@pytest.mark.parametrize("action", ["send", "previous", "next", "new"])
@pytest.mark.parametrize("phase", ["idle", "dictated", "edited", "undone", "interrupted", "error"])
def test_quiet_stages_allow_app_actions(action, phase):
    assert action_phase(action, phase=phase) == "dispatch"
    assert action_phase(action, phase=phase, writing=True) != "dispatch"
    assert action_phase(action, phase=phase, composing=True) != "dispatch"


@pytest.mark.parametrize("phase", ["starting", "listening", "finishing"])
def test_only_send_can_finish_a_sentence(phase):
    assert action_phase("send", phase=phase) == "finish_then_send"
    for action in ["previous", "next", "new"]:
        assert action_phase(action, phase=phase) not in {"dispatch", "finish_then_send"}
    assert action_phase("send", phase=phase, edit_requested=True) != "finish_then_send"


def test_every_action_is_blocked_during_editing_and_applying():
    for action in ["send", "previous", "next", "new"]:
        assert action_phase(action, phase="editing") != "dispatch"
        assert action_phase(action, phase="applying", writing=True) != "dispatch"


def test_default_profiles_roundtrip_and_no_project_picker():
    p = default_profiles()
    assert profiles_from_json(profiles_to_json(p)) == p
    assert set(p) == {"codex", "workbuddy", "wechat"}
    assert all(set(actions) <= {"send", "previous", "next", "new"} for actions in p.values())
    assert p["codex"]["previous"].shortcut == "Cmd+Shift+["
    assert p["workbuddy"]["next"].shortcut == "Cmd+]"
    assert p["wechat"]["send"].shortcut == "Return"
    assert "new" not in p["wechat"]
    validate_profiles(p, GestureBindings())


def test_down_is_reserved_for_polish(route):
    c, service, inline, _, sent, messages, emit = route
    assert service.setBinding("codex", "next", "swipe-down", "Cmd+Shift+]", True)
    emit("swipe-down")
    assert not messages and not sent
    inline._view.update(phase="dictated")
    emit("swipe-down")
    assert messages == [("polish", {"restore_raw_on_undo": True})]
    assert not sent


def test_conflicts_are_per_app_voice_undo_and_edit_can_be_reused():
    p = default_profiles()
    p["codex"]["next"] = AppBinding("swipe-left", "Cmd+Shift+]")
    p["wechat"]["next"] = AppBinding("swipe-left", "Cmd+Shift+]")
    validate_profiles(p, GestureBindings())
    p["codex"]["previous"] = AppBinding("swipe-left", "Cmd+Shift+[")
    with pytest.raises(ValueError):
        validate_profiles(p, GestureBindings())
    p["codex"]["previous"] = AppBinding("tap", "Cmd+Shift+[")
    validate_profiles(p, GestureBindings())  # Outside input, tap can have an application binding.
    validate_profiles(p, GestureBindings(end=("double-tap", "")))  # Released by remapping END.
    p["codex"]["previous"] = AppBinding("double-tap", "Cmd+Shift+[")
    validate_profiles(p, GestureBindings())


def test_migration_only_retires_old_default_aliases():
    old = GestureBindings(undo=("swipe-left", "swipe-down"), switch_mode=("swipe-right", "swipe-up"))
    assert migrate_voice_defaults(old) == GestureBindings()
    custom = GestureBindings(undo=("swipe-down", "snap"), switch_mode=("swipe-up", "swipe-right"))
    assert migrate_voice_defaults(custom) == custom


@pytest.mark.parametrize("value,expected", [("command+shift+[", "Cmd+Shift+["), ("Enter", "Return"),
    ("Ctrl+Alt+N", "Ctrl+Alt+N"), ("Shift+Cmd+N", "Cmd+Shift+N"), ("Esc", "Escape")])
def test_shortcut_normalization(value, expected):
    assert normalize_shortcut(value) == expected


@pytest.mark.parametrize("value", ["N", "Shift+N", "Cmd+Cmd+N", "Cmd+N, Cmd+A", "Cmd+", "", "Cmd+Unknown"])
def test_invalid_shortcuts_never_become_typed_text(value):
    with pytest.raises(ValueError):
        normalize_shortcut(value)


def test_actual_key_codes_not_unicode_positions():
    assert {k: KEY_CODES[k] for k in ["N", "[", "]", "Return", "B", "1", "P"]} == {
        "N": 45, "[": 33, "]": 30, "Return": 36, "B": 11, "1": 18, "P": 35}


@pytest.fixture
def route(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    import proximic_ring.ui.controller as module
    monkeypatch.setattr(module, "QSettings", lambda *args: QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))
    # This fixture exercises an explicitly configured legacy installation.
    module.QSettings().setValue("gestures/appProfiles", profiles_to_json(default_profiles()))
    controller = _controller(tmp_path, monkeypatch)
    controller._connected = controller._runtime_active = True
    controller._recognition_enabled = False
    inline = controller._inline_input
    inline._enabled = True
    inline._epoch, inline._client_id, inline._utterance_id = "e", "c", "u"
    inline._view = dict(ready=True, phase="idle", application="com.openai.codex", revision=0,
                        has_composition=False, awaiting_readback=False, raw="hello", edit_requested=False)
    sent = []
    class Backend:
        target = ShortcutTarget("com.openai.codex", 42, "codex", "window", "composer", "AXTextArea", input_context="text")
        def capture(self, *, plain_enter=False, menu_action=False, focus_sensitive=True, scene=False):
            return replace(self.target, plain_enter=plain_enter, menu_action=menu_action,
                           focus_sensitive=focus_sensitive) if self.target else None
        def same_target(self, target, *, require_focus=False):
            if self.target is None:
                return False
            current = replace(self.target, focus_sensitive=target.focus_sensitive)
            if not target.focus_sensitive:
                current = replace(current, focus=target.focus, role=target.role, input_context=target.input_context)
            return replace(target, plain_enter=False, menu_action=False) == current
        def post(self, target, shortcut, *, require_focus=False):
            if not self.same_target(target, require_focus=require_focus):
                raise RuntimeError("目标窗口已变化")
            sent.append((target.profile, shortcut))
    backend = Backend()
    service = controller._app_gestures
    service.backend = backend
    read_foreground = service.foreground_identity
    def foreground_identity():
        if hasattr(service.backend, "target"):
            target = service.backend.target
            return (target.bundle, target.pid) if target is not None else None
        return read_foreground()
    monkeypatch.setattr(service, "foreground_identity", foreground_identity)
    # Routing tests model the same scene as IMK metadata. Dedicated focus tests
    # vary AX and IMK independently and exercise the real probe/revalidation.
    from proximic_ring.voice.ime.focus_probe import InputFocus
    def imk_focus():
        target = getattr(service.backend, "target", None)
        if target is None or target.input_context == "unknown":
            return InputFocus("unavailable", reason="input_method_not_ready")
        selection = (0, 0) if target.input_context == "text" else (-1, -1)
        proof = (inline._epoch, inline._client_id + str(target.focus), inline._utterance_id,
                 target.bundle, target.pid, selection)
        return InputFocus(target.input_context, proof)
    monkeypatch.setattr(inline, "capture_start_focus", imk_focus)
    monkeypatch.setattr(inline, "same_input_focus", lambda proof: imk_focus() == proof)
    monkeypatch.setattr(inline, "capture_gesture_target", lambda: imk_focus().target)
    monkeypatch.setattr(inline, "same_gesture_target", lambda target: imk_focus() == InputFocus('text', target))
    messages = []
    monkeypatch.setattr(inline, "_send", lambda kind, **kw: messages.append((kind, kw)) or True)
    def emit(name):
        event = service.envelope(BoundGestureEvent(SimpleNamespace(name=name), controller._gesture_bindings))
        controller._apply_gesture(event, controller._disconnect_event)
        return event
    yield controller, service, inline, backend, sent, messages, emit
    service.close()
    _close(controller)


@pytest.mark.parametrize("app,bundle,name,shortcut", [
    ("codex", "com.openai.codex", "circle-counterclockwise", "Cmd+Shift+["),
    ("workbuddy", "vendor.WorkBuddy", "circle-clockwise", "Cmd+]"),
    ("wechat", "com.tencent.xinWeChat", "circle-counterclockwise", "Cmd+Shift+["),
])
def test_idle_actions_work_with_recognition_paused_and_other_input_method(route, monkeypatch, app, bundle, name, shortcut):
    from test_application_menus import configure
    c, s, inline, backend, sent, _, emit = route
    assert configure(s, monkeypatch, bundle).setCustomBinding(bundle, name, "切换对话", shortcut)
    inline._view = {"ready": False, "phase": "idle"}  # Pinyin selected; no IME connection needed.
    backend.target = replace(backend.target, bundle=bundle, profile=app, input_context="nontext", focus="page")
    emit(name)
    assert sent == [(app, shortcut)]


def test_continuous_send_finishes_commits_and_sends_exactly_once(route):
    c, s, inline, _, sent, messages, emit = route
    inline._view.update(phase="listening", has_composition=True)
    emit("swipe-up")
    emit("swipe-up")
    assert messages == [("finish", {})]
    assert c._finish_utterance_event.is_set()
    assert not sent
    inline._view.update(phase="dictated", has_composition=False)
    inline.settled.emit("dictated", "完整句子")
    inline.settled.emit("dictated", "完整句子")
    assert sent == [("codex", "Return")]
    assert messages[-1] == ("end_input", {"outcome": "sent"})
    assert s._pending is None and inline._utterance_id == ""


def test_failed_send_keeps_sentence_and_never_shows_sent_feedback(route, monkeypatch):
    _, service, inline, backend, sent, messages, emit = route
    inline._view.update(phase="dictated", can_cancel=True, can_polish=True)
    def fail(*args, **kwargs):
        raise RuntimeError("发送快捷键失败")
    monkeypatch.setattr(backend, "post", fail)
    emit("swipe-up")
    assert not sent and not messages
    assert inline._utterance_id == "u" and inline._view["can_cancel"]
    assert "发送快捷键失败" in service.notice


@pytest.mark.parametrize("change", ["cancel", "convert", "focus", "app", "empty", "error", "disconnect", "settings", "sentence", "timeout"])
def test_pending_send_does_not_escape_its_sentence(route, change):
    c, s, inline, backend, sent, _, emit = route
    inline._view.update(phase="listening", has_composition=True)
    emit("swipe-up")
    if change == "cancel":
        emit("swipe-left")
    elif change == "convert":
        emit("swipe-right")
    elif change == "focus":
        backend.target = replace(backend.target, focus="other-chat")
    elif change == "app":
        backend.target = None
    elif change == "disconnect":
        c._disconnect_event.set()
    elif change == "settings":
        s.setBinding("codex", "next", "circle-clockwise", "Cmd+Alt+]", True)
    elif change == "sentence":
        inline._utterance_id = "new"
    elif change == "timeout":
        s._pending_timer.timeout.emit()
    inline._action_pending = None
    inline._view.update(phase="error" if change == "error" else "dictated", has_composition=False)
    inline.settled.emit(inline._view["phase"], "" if change == "empty" else "句子")
    assert not sent and s._pending is None


@pytest.mark.parametrize("phase", ["listening", "finishing", "editing"])
@pytest.mark.parametrize("name", ["circle-counterclockwise", "circle-clockwise"])
def test_busy_navigation_is_not_replayed_when_work_finishes(route, phase, name):
    _, s, inline, _, sent, _, emit = route
    inline._view.update(phase=phase)
    emit(name)
    assert not sent
    inline._view.update(phase="dictated", has_composition=False)
    inline.settled.emit("dictated", "done")
    assert not sent


def test_recognition_time_stage_cannot_be_reinterpreted_after_queue_delay(route):
    c, s, inline, _, sent, _, _ = route
    inline._view.update(phase="editing")
    event = s.envelope(BoundGestureEvent(SimpleNamespace(name="circle-clockwise"), c._gesture_bindings))
    inline._view.update(phase="edited")
    c._apply_gesture(event, c._disconnect_event)
    assert not sent


def test_shortcut_to_new_chat_is_not_queued_behind_pending_send(route):
    _, s, inline, _, sent, _, emit = route
    assert s.setBinding("codex", "new", "snap", "Cmd+N", True)
    inline._view.update(phase="finishing")
    emit("swipe-up")
    inline._view.update(phase="dictated")
    emit("snap")
    assert not sent and s._pending


def test_undo_request_blocks_navigation_before_native_ack(route):
    _, s, inline, _, sent, messages, emit = route
    inline._view.update(phase="dictated")
    emit("swipe-left")
    assert messages[-1][0] == "cancel" and inline._action_pending
    emit("circle-clockwise")
    assert not sent


def test_same_gesture_reuse_keeps_voice_priority_and_never_falls_through(route, monkeypatch):
    from test_application_menus import configure
    _, s, inline, backend, sent, messages, emit = route
    assert configure(s, monkeypatch, backend.target.bundle).setCustomBinding(backend.target.bundle, "swipe-left", "下一个任务", "Cmd+Shift+]")
    inline._view.update(phase="listening", has_composition=True)
    emit("swipe-left")
    assert messages[-1][0] == "cancel" and not sent
    inline._action_pending = None
    inline._view.update(phase="idle", has_composition=False)
    backend.target = replace(backend.target, input_context="nontext", focus="page")
    emit("swipe-left")
    assert sent == [("codex", "Cmd+Shift+]")]


def test_reused_app_gesture_cannot_cancel_a_sentence_started_after_recognition(route):
    c, s, inline, _, sent, messages, _ = route
    s.setBinding("codex", "next", "swipe-left", "Cmd+Shift+]", True)
    event = s.envelope(BoundGestureEvent(SimpleNamespace(name="swipe-left"), c._gesture_bindings))
    inline._utterance_id = "next-sentence"
    inline._view.update(phase="listening", has_composition=True)
    c._apply_gesture(event, c._disconnect_event)
    assert not sent and not messages


def test_pending_send_cleared_by_button_cancel_and_button_convert(route):
    _, s, inline, _, sent, _, emit = route
    inline._view.update(phase="listening", has_composition=True)
    emit("swipe-up")
    inline.convert()
    assert s._pending is None and not sent


def test_pending_send_focus_read_failure_does_not_escape_gui_callback(route, monkeypatch):
    _, s, inline, backend, sent, messages, emit = route
    inline._view.update(phase="listening", has_composition=True)
    def fail(*args, **kwargs):
        raise RuntimeError("application closed")
    monkeypatch.setattr(inline, "same_gesture_target", fail)
    emit("swipe-up")
    assert not sent and not messages and s._pending is None
    assert "输入目标" in s.notice


def test_capture_failure_is_reported_not_mislabeled_as_unmapped(route, monkeypatch):
    c, s, _, backend, sent, _, emit = route
    def fail(**kwargs):
        raise AttributeError("missing native bridge function")
    monkeypatch.setattr(backend, "capture", fail)
    emit("swipe-up")
    assert not sent and "读取前台应用" in s.notice
    log = c._diagnostic_log.path.read_text()
    assert '"reason":"capture_failed"' in log
    assert 'reason="unmapped_gesture"' not in log


@pytest.mark.parametrize("old_confirmation", [False, True])
def test_wechat_navigation_needs_no_test_or_confirmation_and_uses_current_binding(route, monkeypatch, old_confirmation):
    from test_application_menus import configure
    c, s, inline, backend, sent, _, emit = route
    backend.target = replace(backend.target, profile="wechat", bundle="com.tencent.xinWeChat", input_context="nontext", focus="page")
    inline._view.update(application="com.tencent.xinWeChat")
    catalog = configure(s, monkeypatch, backend.target.bundle)
    assert catalog.setCustomBinding(backend.target.bundle, "circle-clockwise", "下一个聊天", "Cmd+Shift+]")
    assert catalog.setCustomBinding(backend.target.bundle, "circle-counterclockwise", "上一个聊天", "Cmd+Shift+[")
    c._settings.setValue("gestures/wechatConfigured", old_confirmation)
    emit("circle-clockwise")
    assert sent == [("wechat", "Cmd+Shift+]")]
    assert catalog.setCustomBinding(backend.target.bundle, "circle-clockwise", "下一个聊天", "Cmd+Alt+]")
    s._last_dispatch = None
    emit("circle-clockwise")
    assert sent[-1] == ("wechat", "Cmd+Alt+]")
    s._last_dispatch = None
    emit("circle-counterclockwise")
    assert sent[-1] == ("wechat", "Cmd+Shift+[")


@pytest.mark.parametrize("phase", ["listening", "finishing", "editing", "applying"])
def test_wechat_navigation_still_waits_for_text_operation_to_finish(route, phase):
    _, _, inline, backend, sent, _, emit = route
    backend.target = replace(backend.target, profile="wechat", bundle="com.tencent.xinWeChat", input_context="nontext", focus="page")
    inline._view.update(application="com.tencent.xinWeChat", phase=phase)
    emit("circle-clockwise")
    assert not sent


def test_current_shortcut_settings_survive_restart(route, tmp_path, monkeypatch):
    c, s, *_ = route
    assert s.setBinding("workbuddy", "new", "snap", "Cmd+Return", False)
    loaded = profiles_from_json(c._settings.value("gestures/appProfiles"))
    assert loaded["workbuddy"]["new"] == AppBinding("snap", "Cmd+Return", False)


def test_detecting_changed_wechat_language_updates_guidance_without_disabling_gestures(route):
    from proximic_ring.platforms.macos.wechat_setup import MENU_TITLES, menu_details
    c, s, *_ = route
    generation = s._generation
    s._apply_setup_result("menu", s._menu_generation, menu_details(MENU_TITLES["en"], version="4.1.11"))
    s._apply_setup_result("menu", s._menu_generation, menu_details(MENU_TITLES["zh-Hans"], version="4.1.11"))
    assert "同步更新" in s.wechatInfo["message"] and s._generation == generation
    assert s.wechatMenuTitle("previous", "auto") == "显示上一个聊天"
    assert s.wechatMenuTitle("previous", "en") == "Show Previous Chat"
    s._apply_setup_result("menu", s._menu_generation - 1, menu_details(MENU_TITLES["en"]))
    assert s.wechatInfo["language"] == "zh-Hans"


def test_stale_old_connection_settings_and_events_are_inert(route):
    import threading
    c, s, _, _, sent, _, _ = route
    event = s.envelope(BoundGestureEvent(SimpleNamespace(name="swipe-up"), c._gesture_bindings))
    c._apply_gesture(event, threading.Event())
    c._apply_gesture(replace(event, created=0), c._disconnect_event)
    s.setBinding("codex", "next", "circle-clockwise", "Cmd+Alt+]", True)
    c._apply_gesture(event, c._disconnect_event)
    assert not sent


def test_late_native_interruption_after_shortcut_cannot_cancel_next_sentence(route):
    c, _, inline, _, sent, _, emit = route
    inline._view.update(phase="dictated")
    emit("swipe-up")
    assert sent
    inline._utterance_id = "next"
    inline._view.update(phase="listening")
    inline._accept(dict(type="interrupted", epoch="e", client_id="c", utterance_id="u", reason="keyboard"))
    assert inline._view["phase"] == "listening" and not c._cancel_utterance_event.is_set()


def test_unsupported_apps_cannot_use_profiles():
    assert profile_for_application("com.openai.codex") == "codex"
    assert profile_for_application("vendor.WorkBuddy", "WorkBuddy") == "workbuddy"
    assert profile_for_application("com.apple.Terminal", "Terminal") == ""


@pytest.mark.parametrize("bundle,role", [
    ("com.apple.Safari", "AXTextField"),
    ("com.google.Chrome", "AXComboBox"),
    ("org.mozilla.firefox", "AXSearchField"),
    ("other.app", "AXWebArea"),
    ("other.custom.editor", ""),
    ("com.tencent.xinWeChat", "AXTextArea"),
])
def test_input_swipe_up_is_plain_enter_without_app_or_role_whitelist(route, bundle, role):
    _, s, inline, backend, sent, _, emit = route
    inline._view = {"ready": False, "phase": "idle"}
    backend.target = replace(backend.target, bundle=bundle, profile="", role=role)
    # Old disabled/customized profiles must not influence the global action.
    for actions in s._profiles.values():
        actions["send"] = AppBinding("swipe-up", "Cmd+Return", False)
    emit("swipe-up")
    assert sent == [("", "Return")]


def test_swipe_up_settings_migrate_to_fixed_global_enter(route):
    from proximic_ring.ui.gestures.app_gesture_controller import AppGestureController
    from proximic_ring.gestures.gesture_settings import reserve_ring_gestures
    c, s, *_ = route
    profiles = default_profiles()
    profiles["codex"]["send"] = AppBinding("snap", "Cmd+Return", False)
    profiles["workbuddy"]["next"] = AppBinding("swipe-up", "Cmd+]")
    c._settings.setValue("gestures/appProfiles", profiles_to_json(profiles))
    restored = AppGestureController(c)
    try:
        assert all(actions["send"] == AppBinding("swipe-up", "Return")
                   for actions in restored._profiles.values())
        assert restored._profiles["workbuddy"]["next"].enabled
        assert not s.setBinding("codex", "send", "swipe-up", "Cmd+Return", True)
        assert reserve_ring_gestures(GestureBindings(confirm=("swipe-up", ""))) == GestureBindings()
    finally:
        restored.close()
