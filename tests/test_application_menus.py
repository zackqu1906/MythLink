"""Opt-in menu mappings, bounded AX reads, and foreground routing regressions."""
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from proximic_ring.application_menus import menu_shortcut, read_menu_tree
from proximic_ring.app_gestures import AppBinding
from proximic_ring.gesture_settings import BoundGestureEvent, RING_RESERVED_GESTURES
from proximic_ring.ui.application_mapping_controller import SETTINGS_KEY
from test_app_gestures import route
from test_interaction_controls import _controller, _close

BUNDLE = "com.example.Editor"
ACTION = dict(id="next-tab", label="下一个标签页", path="窗口 › 下一个标签页", shortcut="Ctrl+Tab")


def configure(service, monkeypatch, bundle=BUNDLE):
    catalog = service.catalog
    monkeypatch.setattr(catalog, "_request", lambda kind, bundle="": None)
    catalog._candidates = [dict(value=bundle, label="示例编辑器")]
    assert catalog.addApplication(bundle)
    catalog._apply_result("menu", catalog._generation["menu"], bundle, dict(actions=[ACTION]), "")
    return catalog


@pytest.mark.parametrize("char,code,mask,glyph,expected", [
    ("N", 45, 0, 0, "Cmd+N"), ("[", 33, 3, 0, "Cmd+Alt+Shift+["),
    ("", 48, 12, 2, "Ctrl+Tab"), ("", 123, 2, 28, "Cmd+Alt+Left"),
    ("N", 45, 8, 0, ""), ("N", 45, 9, 0, ""), ("", 0, 0, 0, ""),
    ("N", 999, 0, 0, ""), ("N", 45, 32, 0, ""), ("N", None, 0, 0, "Cmd+N"), ("N", 45, None, 0, ""),
    ("[", None, 1, None, "Cmd+Shift+["), ("]", None, 1, None, "Cmd+Shift+]"),
    ("+", None, 0, None, "Cmd+Shift+="), ("{", None, 1, None, "Cmd+Shift+["),
    ("", None, 12, 2, "Ctrl+Tab"), ("", None, 8, 100, "Left"),
    ("", None, 8, 135, "F13"), ("\uf717", None, 8, None, "F20"),
    ("", 123, 0, None, "Cmd+Left"), ("", None, 0, None, ""),
    ("“,", None, 0, None, ""), ("N", None, None, None, ""),
    ("←", None, 2, None, "Cmd+Alt+Left"), ("", None, 0, 4, "Cmd+KeypadEnter"),
    ("F", None, 28, None, "Ctrl+Fn+F"), ("", 123, 28, None, "Ctrl+Fn+Left"),
    ("E", None, 24, None, "Fn+E"), ("", 124, 31, None, "Ctrl+Alt+Shift+Fn+Right"),
])
def test_menu_chords_use_physical_keys_and_apple_modifiers(char, code, mask, glyph, expected):
    assert menu_shortcut(char, code, mask, glyph) == expected


def test_menu_only_traversal_preserves_paths_and_deduplicates():
    item = dict(AXRole="AXMenuItem", AXTitle="新建标签页", AXMenuItemCmdChar="T",
                AXMenuItemCmdVirtualKey=17, AXMenuItemCmdModifiers=0, AXEnabled=False)
    root = dict(AXRole="AXMenuBar", AXChildren=[dict(AXRole="AXMenuBarItem", AXTitle="文件",
        AXChildren=[dict(AXRole="AXMenu", AXTitle="文件", AXChildren=[item, item,
            dict(AXRole="AXTextArea", AXChildren=[item])])])])
    reads = []
    def get(node, name):
        reads.append(name)
        assert name not in {"AXValue", "AXSelectedText", "AXFocusedWindow"}
        return node.get(name)
    result = read_menu_tree(root, get)
    assert len(result["actions"]) == 1 and not result["partial"]
    assert result["actions"][0]["path"] == "文件 › 新建标签页"
    assert result["actions"][0]["shortcut"] == "Cmd+T"
    assert result["actions"][0]["available"] is False
    item.pop("AXEnabled")
    assert read_menu_tree(root, get)["actions"][0]["available"] is None
    assert read_menu_tree(root, get, maximum=1)["partial"]
    assert read_menu_tree(root, get, budget=0)["partial"]


def test_codex_character_only_items_are_discovered_in_nested_menus():
    # Captured metadata shape from the installed Codex menu: no virtual keys.
    items = [dict(AXRole="AXMenuItem", AXTitle=label, AXMenuItemCmdChar=char,
                  AXMenuItemCmdModifiers=mask) for label, char, mask in [
                      ("Previous Chat", "[", 1), ("Next Chat", "]", 1),
                      ("Toggle Sidebar", "B", 0), ("Zoom In", "+", 0)]]
    root = dict(AXRole="AXMenuBar", AXChildren=[dict(AXRole="AXMenuBarItem", AXTitle="View",
        AXChildren=[dict(AXRole="AXMenu", AXChildren=items)])])
    result = read_menu_tree(root, lambda n, a: n.get(a))
    assert not result["partial"] and result["unresolved"] == 0
    assert {a["label"]: a["shortcut"] for a in result["actions"]} == {
        "Previous Chat": "Cmd+Shift+[", "Next Chat": "Cmd+Shift+]",
        "Toggle Sidebar": "Cmd+B", "Zoom In": "Cmd+Shift+="}


def test_large_menu_is_not_cut_at_one_thousand_nodes_and_reports_unsupported_keys():
    children = [dict(AXRole="AXMenuItem", AXTitle=f"Action {i}", AXMenuItemCmdChar="N",
                     AXMenuItemCmdModifiers=0) for i in range(1200)]
    children.append(dict(AXRole="AXMenuItem", AXTitle="Unknown modifier", AXMenuItemCmdChar="F",
                         AXMenuItemCmdModifiers=32))
    result = read_menu_tree(dict(AXRole="AXMenuBar", AXChildren=children), lambda n, a: n.get(a))
    assert len(result["actions"]) == 1200 and not result["partial"] and result["unresolved"] == 1


def test_native_menu_element_invalidated_during_read_is_reported_as_partial(monkeypatch):
    import sys
    from proximic_ring.application_menus import read_application_menu
    import proximic_ring.mac_workspace as workspace
    app = SimpleNamespace(bundleIdentifier=lambda: BUNDLE, processIdentifier=lambda: 42)
    monkeypatch.setattr(workspace, "running_applications", lambda: [app])
    def read(node, name, _):
        if (node, name) == ("application", "AXMenuBar"):
            return 0, "root"
        if (node, name) == ("root", "AXRole"):
            return 0, "AXMenuBar"
        if (node, name) == ("root", "AXChildren"):
            return 0, ["invalid"]
        return (-25202, None) if node == "invalid" else (-25212, None)
    monkeypatch.setitem(sys.modules, "ApplicationServices", SimpleNamespace(
        AXUIElementCreateApplication=lambda pid: "application",
        AXUIElementSetMessagingTimeout=lambda *args: None,
        AXUIElementCopyAttributeValue=read))
    assert read_application_menu(BUNDLE)["partial"]


def test_new_install_has_no_implicit_applications_or_navigation(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    import proximic_ring.ui.controller as module
    monkeypatch.setattr(module, "QSettings", lambda *args: QSettings(str(tmp_path / "fresh.ini"), QSettings.IniFormat))
    c = _controller(tmp_path, monkeypatch)
    try:
        s = c.appGestures
        assert s.catalog.apps == [] and not s._legacy_configured
        monkeypatch.setattr(s.backend, "capture", lambda **kw: pytest.fail("No target reads without a mapping"))
        event = BoundGestureEvent(SimpleNamespace(name="circle-clockwise"), c._gesture_bindings)
        assert s.envelope(event) is event
        assert not c._settings.contains(SETTINGS_KEY)
    finally:
        c.appGestures.close()
        _close(c)


def test_explicit_add_and_binding_validation_preserve_voice_and_persist(route, monkeypatch):
    c, s, _, _, _, _, _ = route
    before = c.gestureBindings
    catalog = configure(s, monkeypatch)
    assert not catalog.addApplication("unlisted.app")
    assert catalog.bindings[BUNDLE] == {}
    for gesture in c._global_gesture_bindings.reserved:
        assert not catalog.setBinding(BUNDLE, gesture, ACTION["id"])
    assert not catalog.setBinding(BUNDLE, "circle-clockwise", "invented")
    assert catalog.setBinding(BUNDLE, "circle-clockwise", ACTION["id"])
    assert catalog.for_target(BUNDLE) == {"menu:circle-clockwise": AppBinding("circle-clockwise", "Ctrl+Tab")}
    saved = json.loads(c._settings.value(SETTINGS_KEY))
    assert saved[BUNDLE]["bindings"]["circle-clockwise"]["path"] == ACTION["path"]
    from proximic_ring.ui.application_mapping_controller import ApplicationMappingController
    restored = ApplicationMappingController(s)
    assert restored.bindings == catalog.bindings
    restored.close()
    assert catalog.setBinding(BUNDLE, "circle-clockwise", "")
    assert catalog.bindings[BUNDLE] == {} and c.gestureBindings == before


def test_custom_shortcut_survives_discovery_failure_restart_and_uses_guarded_dispatch(route, monkeypatch):
    from proximic_ring.ui.application_mapping_controller import ApplicationMappingController
    c, service, inline, backend, sent, _, _ = route
    catalog = configure(service, monkeypatch)
    before = (c.gestureBindings, service.profiles, dict(inline._view))
    action = catalog.customAction("上一个任务", "Cmd+Shift+[")
    assert action["custom"] and catalog.bindings[BUNDLE] == {}  # Draft validation is read-only.
    catalog._apply_result("menu", catalog._generation["menu"], BUNDLE, None, "权限未开启")
    assert catalog.setCustomBinding(BUNDLE, "circle-counterclockwise", "上一个任务", "Cmd+Shift+[")
    assert catalog.actions[0]["id"] == action["id"] and catalog.menuState == "error"
    assert catalog.for_target(BUNDLE) == {"menu:circle-counterclockwise": AppBinding("circle-counterclockwise", "Cmd+Shift+[")}
    restored = ApplicationMappingController(service)
    try:
        assert restored.bindings == catalog.bindings
    finally:
        restored.close()
    backend.target = replace(backend.target, bundle=BUNDLE, profile=BUNDLE)
    event = lambda: service.envelope(BoundGestureEvent(SimpleNamespace(name="circle-counterclockwise"), c._gesture_bindings))
    service.handle(event())
    assert sent == [(BUNDLE, "Cmd+Shift+[")]
    pending = event()
    backend.target = replace(backend.target, bundle="com.example.Other", profile="com.example.Other")
    service.handle(pending)
    assert sent == [(BUNDLE, "Cmd+Shift+[")]
    assert (c.gestureBindings, service.profiles, inline._view) == before
    assert catalog.clearApplicationBindings(BUNDLE) and catalog.actions == []


def test_custom_binding_rejects_locked_gestures_invalid_keys_and_changed_application(route, monkeypatch):
    _, service, *_ = route
    catalog = configure(service, monkeypatch)
    for name, shortcut in [("", "Cmd+N"), ("a" * 121, "Cmd+N"), ("New", "N"), ("New", "Cmd+K, Cmd+N")]:
        assert not catalog.customAction(name, shortcut)
        assert not catalog.setCustomBinding(BUNDLE, "circle-clockwise", name, shortcut)
    for gesture in catalog.globalOccupancy:
        assert not catalog.setCustomBinding(BUNDLE, gesture, "New", "Cmd+N")
    catalog.selectApplication("")
    assert not catalog.setCustomBinding(BUNDLE, "circle-clockwise", "New", "Cmd+N")
    assert catalog.bindings[BUNDLE] == {}


def test_late_menu_reply_does_not_replace_selected_application(route, monkeypatch):
    _, s, *_ = route
    catalog = configure(s, monkeypatch)
    generation = catalog._generation["menu"]
    second = "com.example.Second"
    catalog._candidates.append(dict(value=second, label="Second"))
    assert catalog.addApplication(second)
    catalog._apply_result("menu", generation, BUNDLE, dict(actions=[ACTION]), "")
    assert catalog.selectedApp == second and catalog.actions == [] and catalog.busy
    assert not catalog.setBinding(second, "circle-clockwise", ACTION["id"])
    catalog._apply_result("menu", catalog._generation["menu"], second, None, "辅助功能权限尚未生效")
    assert not catalog.busy and "辅助功能" in catalog.message
    catalog._apply_result("menu", catalog._generation["menu"], second, dict(actions=[ACTION]), "")
    assert catalog.setBinding(second, "circle-clockwise", ACTION["id"])


def test_clear_one_application_preserves_other_bindings_and_live_voice(route, monkeypatch):
    c, service, inline, backend, sent, _, _ = route
    catalog = configure(service, monkeypatch)
    assert catalog.setBinding(BUNDLE, "circle-clockwise", ACTION["id"])
    backend.target = replace(backend.target, bundle=BUNDLE, profile=BUNDLE)
    pending = service.envelope(BoundGestureEvent(SimpleNamespace(name="circle-clockwise"), c._gesture_bindings))
    second = "com.example.Other"
    catalog._candidates.append(dict(value=second, label="Other"))
    catalog.addApplication(second)
    catalog._apply_result("menu", catalog._generation["menu"], second, dict(actions=[ACTION]), "")
    catalog.setBinding(second, "circle-counterclockwise", ACTION["id"])
    other_bindings = catalog.bindings[second]
    before = (c.gestureBindings, service.profiles, dict(inline._view))
    cleared = []
    catalog.applicationBindingsCleared.connect(cleared.append)
    assert catalog.clearApplicationBindings(BUNDLE)
    service.handle(pending)
    assert not sent
    assert catalog.bindings[BUNDLE] == {} and catalog.bindings[second] == other_bindings
    assert catalog.selectedApp == second and catalog.actions == [ACTION]
    assert len(catalog.apps) == 2 and cleared == [BUNDLE]
    assert (c.gestureBindings, service.profiles, inline._view) == before
    assert json.loads(c._settings.value(SETTINGS_KEY))[BUNDLE]["bindings"] == {}
    assert not catalog.clearApplicationBindings("missing")
    menu_generation = catalog._generation["menu"]
    assert catalog.removeApplication(BUNDLE)
    assert catalog.selectedApp == second and catalog.actions == [ACTION]
    assert catalog._generation["menu"] == menu_generation
    assert catalog.bindings[second] == other_bindings and len(catalog.apps) == 1


def test_remove_and_readd_use_fresh_defaults_not_legacy_shortcuts_or_old_menu_reply(route, monkeypatch):
    from proximic_ring.ui.application_mapping_controller import ApplicationMappingController
    c, service, inline, backend, sent, _, emit = route
    bundle = "com.openai.codex"
    catalog = configure(service, monkeypatch, bundle)
    assert catalog.setBinding(bundle, "circle-clockwise", ACTION["id"])
    emit("circle-clockwise")
    assert sent == [("codex", "Ctrl+Tab")]
    sent.clear()
    before = (c.gestureBindings, service.profiles, dict(inline._view))
    generation = catalog._generation["menu"]
    pending = service.envelope(BoundGestureEvent(SimpleNamespace(name="circle-clockwise"), c._gesture_bindings))
    assert catalog.removeApplication(bundle)
    assert catalog.apps == [] and catalog.bindings == {} and not catalog.selectedApp
    assert catalog.menuState == "idle" and not catalog.busy
    catalog._apply_result("menu", generation, bundle, dict(actions=[ACTION]), "")
    assert catalog.actions == [] and catalog.menuState == "idle"
    service.handle(pending)
    emit("circle-clockwise")
    assert not sent  # The old Codex profile must remain suppressed.
    assert not catalog.setBinding(bundle, "circle-clockwise", ACTION["id"])
    assert not catalog.removeApplication(bundle)
    restored = ApplicationMappingController(service)
    try:
        monkeypatch.setattr(service, "_catalog", restored)
        assert restored.apps == [] and restored.bindings == {}
        emit("circle-clockwise")
        assert not sent
    finally:
        monkeypatch.setattr(service, "_catalog", catalog)
        restored.close()
    assert catalog.addApplication(bundle)
    catalog._apply_result("menu", generation, bundle, dict(actions=[ACTION]), "")
    assert catalog.busy and all(item.get("preset") for item in catalog.actions)
    assert catalog.bindings[bundle]["circle-clockwise"]["shortcut"] == "Cmd+Shift+]"
    service._last_dispatch = None  # Simulate a later gesture after remove/re-add.
    emit("circle-clockwise")
    assert sent == [("codex", "Cmd+Shift+]")]
    sent.clear()
    catalog._apply_result("menu", catalog._generation["menu"], bundle, dict(actions=[ACTION]), "")
    assert catalog.setBinding(bundle, "circle-clockwise", ACTION["id"])
    service._last_dispatch = None  # Model a later gesture, outside the existing debounce window.
    emit("circle-clockwise")
    assert sent == [("codex", "Ctrl+Tab")]
    assert (c.gestureBindings, service.profiles, inline._view) == before


def test_menu_read_state_never_erases_saved_bindings(route, monkeypatch):
    _, service, *_ = route
    catalog = configure(service, monkeypatch)
    catalog.setBinding(BUNDLE, "circle-clockwise", ACTION["id"])
    saved = catalog.bindings
    assert catalog.menuState == "ready" and catalog.menuReadAt
    for result, error, state in [(dict(actions=[], partial=True), "", "partial"),
                                  (None, "请先打开这个应用，再刷新快捷键", "error"),
                                  (dict(actions=[]), "", "ready")]:
        catalog.refreshMenu()
        assert catalog.menuState == "loading" and not catalog.menuReadAt
        catalog._apply_result("menu", catalog._generation["menu"], BUNDLE, result, error)
        assert catalog.menuState == state
        assert bool(catalog.menuReadAt) == (state != "error")
        assert catalog.bindings == saved


def test_explicit_app_only_dispatches_its_own_saved_mapping(route, monkeypatch):
    c, s, _, backend, sent, messages, emit = route
    s._legacy_configured = False
    catalog = configure(s, monkeypatch)
    backend.target = replace(backend.target, bundle=BUNDLE, profile=BUNDLE)
    emit("circle-clockwise")
    assert not sent
    assert catalog.setBinding(BUNDLE, "circle-clockwise", ACTION["id"])
    emit("circle-clockwise")
    assert sent == [(BUNDLE, "Ctrl+Tab")] and not messages
    backend.target = replace(backend.target, bundle="com.example.Other", profile="other")
    emit("circle-clockwise")
    assert sent == [(BUNDLE, "Ctrl+Tab")]


@pytest.mark.parametrize("mode", ["input", "operation"])
@pytest.mark.parametrize("gesture", ["circle-clockwise", "circle-counterclockwise", "snap"])
def test_saved_mapping_passes_both_runtime_and_gui_mode_gates(route, monkeypatch, mode, gesture):
    from test_ring_gestures import request
    import proximic_ring.ui.controller as controller_module

    c, service, inline, backend, sent, messages, _ = route
    catalog = configure(service, monkeypatch)
    assert catalog.setBinding(BUNDLE, gesture, ACTION["id"])
    backend.target = replace(backend.target, bundle=BUNDLE, profile=BUNDLE)
    inline._view = {"ready": False, "phase": "idle"}  # No speech session or voice IME required.
    if mode == "operation":
        request(c, "middle-pinch")
        monkeypatch.setattr(controller_module, "voice_action_for_gesture",
                            lambda *a, **kw: pytest.fail("operation-mode shortcuts must not enter voice routing"))
    assert c.ringGestures.mode == mode
    before = c.gestureBindings
    event = SimpleNamespace(name=gesture)
    assert c.ringGestures.filter(event, False, c._disconnect_event)
    queued = c.ringGestures.envelope(BoundGestureEvent(event, c._gesture_bindings))
    c._apply_gesture(queued, c._disconnect_event)
    assert sent == [(BUNDLE, "Ctrl+Tab")]
    assert not messages and c.gestureBindings == before


def test_operation_mode_only_accepts_explicit_mapping_for_captured_app(route, monkeypatch):
    from test_ring_gestures import request
    c, service, _, backend, sent, messages, _ = route
    catalog = configure(service, monkeypatch)
    assert catalog.setBinding(BUNDLE, "circle-clockwise", ACTION["id"])
    request(c, "middle-pinch")
    source = BoundGestureEvent(SimpleNamespace(name="circle-clockwise"), c._gesture_bindings)
    # Codex has a saved legacy shortcut, but no new mapping for this gesture.
    queued = c.ringGestures.envelope(source)
    c._apply_gesture(queued, c._disconnect_event)
    assert not sent
    backend.target = replace(backend.target, bundle=BUNDLE, profile=BUNDLE)
    # Bypassing the mode envelope is not a valid operation-mode dispatch.
    c._apply_gesture(service.envelope(source), c._disconnect_event)
    for name in ("tap", "swipe-left", "swipe-right", "snap", "circle-counterclockwise"):
        assert not request(c, name)
    assert not sent and not messages


@pytest.mark.parametrize("change", [
    "app", "pid", "window", "expired", "mapping", "bindings", "mode", "roundtrip",
    "disconnect", "lock_roundtrip", "overview", "picker", "scroll_applying", "fields_pending",
    "speech", "preparing",
])
def test_operation_mapping_discards_stale_events_without_replay(route, monkeypatch, change):
    from PySide6.QtCore import QCoreApplication
    from test_ring_gestures import request
    from proximic_ring.gesture_settings import GestureBindings
    c, service, inline, backend, sent, _, _ = route
    catalog = configure(service, monkeypatch)
    assert catalog.setBinding(BUNDLE, "circle-clockwise", ACTION["id"])
    backend.target = replace(backend.target, bundle=BUNDLE, profile=BUNDLE)
    request(c, "middle-pinch")
    source = SimpleNamespace(name="circle-clockwise")
    assert c.ringGestures.filter(source, False, c._disconnect_event)
    queued = c.ringGestures.envelope(BoundGestureEvent(source, c._gesture_bindings))
    flag = None
    if change in {"app", "pid", "window"}:
        field, value = {"app": ("bundle", "another"), "pid": ("pid", 99), "window": ("window", "new")}[change]
        backend.target = replace(backend.target, **{field: value})
    elif change == "expired":
        queued = replace(queued, source=replace(queued.source, created=queued.source.created - 2))
    elif change == "mapping":
        catalog.setBinding(BUNDLE, "circle-clockwise", "")
    elif change == "bindings":
        c._gesture_bindings = GestureBindings(undo=("swipe-left", "snap"))
    elif change in {"mode", "roundtrip"}:
        request(c, "middle-pinch")
        if change == "roundtrip":
            request(c, "middle-pinch")
    elif change == "disconnect":
        c._disconnect_event.set()
    elif change == "lock_roundtrip":
        for blocked in (True, False):
            c._proximity._gesture_state = (blocked, "")
            c._proximity.changed.emit()
    elif change in {"overview", "picker", "scroll_applying", "fields_pending"}:
        ring = c.ringGestures
        flag = {"overview": ring._selector.blocked, "picker": ring._fields.picker.active,
                "scroll_applying": ring._scroll.applying, "fields_pending": ring._fields.pending}[change]
        flag.set()
    elif change == "preparing":
        c._pending_inline_audio_start = True
    else:
        inline._view.update(phase="listening", has_composition=True)
    try:
        c._apply_gesture(queued, c._disconnect_event)
        assert not sent
    finally:
        if flag is not None:
            flag.clear()
        c._pending_inline_audio_start = False
        inline._view.update(phase="idle", has_composition=False)
    QCoreApplication.processEvents()
    assert not sent


@pytest.mark.parametrize("busy", ["audio", "speech"])
def test_operation_mapping_busy_at_recognition_is_not_forwarded(route, monkeypatch, busy):
    from test_ring_gestures import request
    c, service, inline, _, sent, _, _ = route
    catalog = configure(service, monkeypatch)
    catalog.setBinding(BUNDLE, "circle-clockwise", ACTION["id"])
    request(c, "middle-pinch")
    if busy == "speech":
        inline._view.update(phase="listening", has_composition=True)
    assert not request(c, "circle-clockwise", busy=busy == "audio", deliver=False)
    inline._view.update(phase="idle", has_composition=False)
    from PySide6.QtCore import QCoreApplication
    QCoreApplication.processEvents()
    assert not sent


def test_operation_mapping_reports_capture_errors_without_posting(route, monkeypatch):
    from test_ring_gestures import request
    c, service, _, backend, sent, _, _ = route
    catalog = configure(service, monkeypatch)
    catalog.setBinding(BUNDLE, "circle-clockwise", ACTION["id"])
    request(c, "middle-pinch")
    def failed_capture(**kwargs):
        raise RuntimeError("测试辅助功能权限错误")
    monkeypatch.setattr(backend, "capture", failed_capture)
    queued = c.ringGestures.envelope(BoundGestureEvent(SimpleNamespace(name="circle-clockwise"), c._gesture_bindings))
    c._apply_gesture(queued, c._disconnect_event)
    assert not sent and "无法读取前台应用" in service.notice


@pytest.mark.parametrize("change", ["app", "pid", "window", "expired", "mapping", "speech"])
def test_queued_menu_mapping_never_uses_a_stale_target_or_state(route, monkeypatch, change):
    c, s, inline, backend, sent, _, _ = route
    catalog = configure(s, monkeypatch)
    assert catalog.setBinding(BUNDLE, "circle-clockwise", ACTION["id"])
    backend.target = replace(backend.target, bundle=BUNDLE, profile=BUNDLE)
    event = s.envelope(BoundGestureEvent(SimpleNamespace(name="circle-clockwise"), c._gesture_bindings))
    if change in {"app", "pid", "window"}:
        field, value = {"app": ("bundle", "another"), "pid": ("pid", 99), "window": ("window", "new")}[change]
        backend.target = replace(backend.target, **{field: value})
    elif change == "expired":
        event = replace(event, created=event.created - 2)
    elif change == "mapping":
        catalog.setBinding(BUNDLE, "circle-clockwise", "")
    else:
        inline._view.update(phase="listening", has_composition=True)
    s.handle(event)
    assert not sent


def test_worker_menu_read_needs_ax_only_and_preserves_capture_mode(monkeypatch):
    from proximic_ring.mac_permissions import PermissionState, MacPermissionError
    from proximic_ring.app_shortcuts import ShortcutTarget
    import proximic_ring.native_access_worker as worker
    import proximic_ring.application_menus as menus
    monkeypatch.setattr(worker, "read_permission_state", lambda: PermissionState(True, False))
    monkeypatch.setattr(menus, "read_application_menu", lambda bundle: dict(bundle=bundle, actions=[ACTION]))
    dispatcher = worker.Dispatcher()
    assert dispatcher.handle(dict(operation="application_menu", bundle=BUNDLE))["result"]["actions"] == [ACTION]
    monkeypatch.setattr(worker, "read_permission_state", lambda: PermissionState(False, False))
    with pytest.raises(MacPermissionError):
        dispatcher.handle(dict(operation="application_menu", bundle=BUNDLE))
    monkeypatch.setattr(worker, "read_permission_state", lambda: PermissionState(True, True))
    calls = []
    dispatcher.shortcuts = SimpleNamespace(capture=lambda **kw: calls.append(kw) or ShortcutTarget(BUNDLE, 42, BUNDLE, menu_action=True))
    result = dispatcher.handle(dict(operation="capture", menu_action=True))["result"]
    assert calls == [dict(plain_enter=False, menu_action=True)] and result["menu_action"]


def test_async_menu_reads_skip_queued_old_requests_and_only_publish_latest(route):
    import threading
    from PySide6.QtTest import QTest
    _, service, *_ = route
    catalog = service.catalog
    started, release = threading.Event(), threading.Event()
    calls = []
    class Channel:
        def call(self, operation, *, bundle):
            calls.append(bundle)
            if len(calls) == 1:
                started.set()
                assert release.wait(2)
            return dict(actions=[dict(ACTION, label=bundle)])
        def close(self):
            pass
    catalog.channel = Channel()
    second = "com.example.Other"
    catalog._candidates = [dict(value=bundle, label=bundle) for bundle in (BUNDLE, second)]
    try:
        catalog.addApplication(BUNDLE)
        assert started.wait(1)
        catalog.addApplication(second)
        catalog.selectApplication(BUNDLE)
        catalog.selectApplication(second)
    finally:
        release.set()
    for _ in range(100):
        QTest.qWait(10)
        if not catalog.busy:
            break
    assert not catalog.busy and catalog.selectedApp == second
    assert catalog.actions[0]["label"] == second
    assert calls == [BUNDLE, second]
