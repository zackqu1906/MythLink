"""Scene overrides must be opted in and must never escape their presentation."""
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication

from proximic_ring.app_shortcuts import LocalMacAppShortcuts, ShortcutTarget
from proximic_ring.gesture_scenes import POWERPOINT, PRESENTATION, SCENE_ANCHORS
from scene_test_helpers import presentation_context
from proximic_ring.gesture_scenes import presentation_profile, installed_presentation_profile, scene_actions
from proximic_ring.ui.application_mapping_controller import ApplicationMappingController, SETTINGS_KEY
from test_app_gestures import route
from test_application_menus import configure
from test_ring_gestures import request


def window_tree(title="PowerPoint Slide Show - Deck"):
    window = dict(AXRole="AXWindow", AXTitle=title, AXModal=False)
    focus = dict(AXRole="AXImage", AXParent=window)
    return window, focus


def metadata(node, key):
    assert key not in {"AXValue", "AXSelectedText", "AXSelectedTextRange"}
    return node.get(key)


def wps_window_tree():
    # Captured from an actual WPS Mac slide show, not an assumed window title.
    window = dict(AXRole="AXWindow", AXSubrole="AXDialog", AXTitle="", AXModal=False,
                  AXMinimized=False, AXFullScreen=True, AXPosition=(0., 0.), AXSize=(1920., 1080.))
    focus = dict(AXRole="AXGroup", AXFocused=True, AXEnabled=True, AXParent=window,
                 AXChildren=[], AXPosition=(0., 0.), AXSize=(1920., 1080.))
    window["AXChildren"] = [focus, dict(AXRole="AXStaticText")]
    return window, focus


def test_wps_real_untitled_dialog_slide_surface_is_recognized_without_reading_content():
    window, focus = wps_window_tree()
    assert presentation_context("com.kingsoft.wpsoffice.mac", window, focus, metadata) == (PRESENTATION, "nontext")
    window["AXChildren"] = [focus]  # The transient fullscreen title label is optional.
    assert presentation_context("com.kingsoft.wpsoffice.mac.global", window, focus, metadata) == (PRESENTATION, "nontext")


@pytest.mark.parametrize("change", ["foreign", "system_dialog", "modal", "minimized", "sheet",
    "windowed", "unknown_fullscreen", "text", "editable",
    "unknown", "missing", "detached", "not_focused", "disabled", "unknown_children", "nested_control",
    "prompt", "missing_children", "missing_size", "partial_size", "shifted", "empty_size"])
def test_wps_surface_exception_does_not_accept_editor_dialog_or_unknown_metadata(change):
    window, focus = wps_window_tree()
    bundle = "com.kingsoft.wpsoffice.mac"
    if change == "foreign": bundle = "com.example.Other"
    for name, value in {"standard": "AXStandardWindow", "system_dialog": "AXSystemDialog"}.items():
        if change == name: window["AXSubrole"] = value
    if change == "modal": window["AXModal"] = True
    if change == "minimized": window["AXMinimized"] = True
    if change == "sheet": window["AXSheets"] = [dict(AXRole="AXSheet")]
    if change == "windowed": window["AXFullScreen"] = False
    if change == "titled": window["AXTitle"] = "Presentation1"
    if change == "missing_title": window.pop("AXTitle")
    if change == "unknown_fullscreen": window.pop("AXFullScreen")
    if change == "unknown_modal": window.pop("AXModal")
    if change == "text": focus["AXRole"] = "AXTextField"
    if change == "editable": focus["AXEditable"] = True
    if change == "unknown": focus["AXRole"] = "AXUnknown"
    if change == "missing": focus = None
    if change == "detached": focus.pop("AXParent")
    if change == "not_focused": focus["AXFocused"] = False
    if change == "disabled": focus["AXEnabled"] = False
    if change == "unknown_children": focus.pop("AXChildren")
    if change == "nested_control": focus["AXChildren"] = [dict(AXRole="AXTextField")]
    if change == "prompt": window["AXChildren"][1] = dict(AXRole="AXButton")
    if change == "missing_children": window.pop("AXChildren")
    if change == "missing_size": window.pop("AXSize")
    if change == "partial_size": focus["AXSize"] = (600., 400.)
    if change == "shifted": focus["AXPosition"] = (100., 0.)
    if change == "empty_size": window["AXSize"] = focus["AXSize"] = (0., 0.)
    assert presentation_context(bundle, window, focus, metadata) == ("", "unknown")


@pytest.mark.parametrize("title", ["PowerPoint Slide Show - Deck", "幻灯片放映 — 演示", "Slide Show"])
def test_dedicated_show_window_with_known_nontext_focus(title):
    window, focus = window_tree(title)
    assert presentation_context(POWERPOINT, window, focus, metadata) == (PRESENTATION, "nontext")


@pytest.mark.parametrize("change,expected", [
    ("normal", ("", "unknown")), ("named_document", ("", "unknown")),
    ("fullscreen", ("", "unknown")), ("foreign", ("", "unknown")),
    ("modal", ("", "unknown")), ("unknown", (PRESENTATION, "unknown")),
    ("missing", (PRESENTATION, "unknown")), ("detached", (PRESENTATION, "unknown")),
    ("text", (PRESENTATION, "text")), ("editable", (PRESENTATION, "text")),
    ("editable_parent", (PRESENTATION, "text")), ("timeout", ("", "unknown")),
])
def test_scene_evidence_is_not_fullscreen_or_an_unknown_focus(change, expected):
    window, focus = window_tree()
    bundle, budget = POWERPOINT, .12
    if change == "normal": window["AXTitle"] = "Deck.pptx"
    if change == "named_document": window.update(AXTitle="Slide Show - Example.pptx", AXDocument="file:///Slide%20Show%20-%20Example.pptx")
    if change == "fullscreen": window.update(AXTitle="Deck", AXFullScreen=True)
    if change == "foreign": bundle = "com.example.Editor"
    if change == "modal": window["AXModal"] = True
    if change == "unknown": focus["AXRole"] = "AXUnknown"
    if change == "missing": focus = None
    if change == "detached": focus.pop("AXParent")
    if change == "text": focus["AXRole"] = "AXTextArea"
    if change == "editable": focus["AXIsEditable"] = True
    if change == "editable_parent": focus["AXParent"] = dict(AXRole="AXGroup", AXEditable=True, AXParent=window)
    if change == "timeout": budget = 0
    assert presentation_context(bundle, window, focus, metadata, budget=budget) == expected


def test_visible_end_show_control_recognizes_presenter_window_but_not_a_menu_item():
    window, focus = window_tree("Deck")
    button = dict(AXRole="AXButton", AXTitle="结束放映", AXEnabled=True, AXHidden=False)
    window["AXChildren"] = [dict(AXRole="AXToolbar", AXChildren=[button])]
    assert presentation_context(POWERPOINT, window, focus, metadata) == (PRESENTATION, "nontext")
    button["AXHidden"] = True
    assert not presentation_context(POWERPOINT, window, focus, metadata)[0]
    button.update(AXHidden=False, AXRole="AXMenuItem")
    assert not presentation_context(POWERPOINT, window, focus, metadata)[0]
    button["AXRole"] = "AXButton"
    window["AXChildren"][0]["AXHidden"] = True
    assert not presentation_context(POWERPOINT, window, focus, metadata)[0]


def test_minimized_or_sheet_covered_presentation_is_not_eligible():
    window, focus = window_tree()
    window["AXMinimized"] = True
    assert not presentation_context(POWERPOINT, window, focus, metadata)[0]
    window["AXMinimized"] = False
    window["AXSheets"] = [dict(AXRole="AXSheet")]
    assert not presentation_context(POWERPOINT, window, focus, metadata)[0]


@pytest.fixture
def presentation(route, monkeypatch):
    c, service, inline, _, sent, messages, _ = route
    catalog = configure(service, monkeypatch, POWERPOINT)
    catalog.clearApplicationBindings(POWERPOINT)  # This fixture tests explicit scene edits independently of defaults.
    catalog.selectScene(PRESENTATION)
    assert catalog.setBinding(POWERPOINT, "swipe-left", "powerpoint:previous")
    assert catalog.setBinding(POWERPOINT, "swipe-right", "powerpoint:next")
    assert catalog.setBinding(POWERPOINT, "tap", "powerpoint:end")
    class Backend(LocalMacAppShortcuts):
        target = ShortcutTarget(POWERPOINT, 42, POWERPOINT, "window", "slide", "AXImage",
                                scene=PRESENTATION, input_context="nontext")
        def capture(self, *, plain_enter=False, menu_action=False, scene=False):
            if self.target is None:
                return None
            return replace(self.target, plain_enter=plain_enter, menu_action=menu_action, scene_checked=scene)
        def post(self, target, shortcut, *, require_focus=False):
            if not self.same_target(target, require_focus=require_focus):
                raise RuntimeError("放映窗口或输入状态已变化")
            sent.append((target.bundle, shortcut))
    backend = service.backend = Backend()
    return c, service, inline, backend, catalog, sent, messages


def test_scene_editor_selection_does_not_change_routing_or_regular_settings(presentation):
    c, s, _, _, catalog, sent, _ = presentation
    before = c.gestureBindings
    generation = s._generation
    assert catalog.regularBindings[POWERPOINT] == {}
    assert not catalog.uses("tap") and catalog.for_target(POWERPOINT) == {}
    catalog.selectScene("regular")
    assert s._generation == generation and catalog.canBind("tap")
    assert not request(c, "swipe-right")
    assert sent == [(POWERPOINT, "Right")] and c.gestureBindings == before
    catalog.selectScene(PRESENTATION)
    assert all(not catalog.canBind(g) for g in SCENE_ANCHORS)
    for gesture in SCENE_ANCHORS:
        assert not catalog.setBinding(POWERPOINT, gesture, "powerpoint:next")
    restored = ApplicationMappingController(s)
    try:
        assert restored.selectedScene == "regular"
        assert restored.for_scene(POWERPOINT, PRESENTATION) == catalog.for_scene(POWERPOINT, PRESENTATION)
    finally:
        restored.close()


@pytest.mark.parametrize("mode", ["input", "operation"])
@pytest.mark.parametrize("gesture,shortcut", [("tap", "Escape"), ("swipe-left", "Left"), ("swipe-right", "Right"),
                                             ("swipe-up", "Right"), ("swipe-down", "Right")])
def test_reserved_overrides_are_consumed_before_audio_focus_and_system_actions(presentation, mode, gesture, shortcut):
    c, s, inline, _, catalog, sent, messages = presentation
    if gesture in {"swipe-up", "swipe-down", "clench"}:
        assert catalog.setBinding(POWERPOINT, gesture, "powerpoint:next")
    c.ringGestures._mode = mode
    sentence, view = s.sentence(), dict(inline._view)
    assert not request(c, gesture)
    assert sent == [(POWERPOINT, shortcut)] and not messages
    assert s.sentence() == sentence and inline._view == view
    assert c.ringGestures.mode == mode and not c.ringGestures._selector.blocked.is_set()


@pytest.mark.parametrize("change", ["app", "pid", "window", "exit", "text", "unknown", "modal", "disconnect", "mode",
                                    "settings", "voice", "composition", "writing", "sentence", "focus_pending", "scroll_pending", "expired"])
def test_queued_scene_gesture_is_dropped_without_replay_or_voice_fallback(presentation, change, monkeypatch):
    c, s, inline, backend, catalog, sent, messages = presentation
    assert not request(c, "tap", deliver=False)
    if change == "app": backend.target = replace(backend.target, bundle="com.example.Other")
    if change == "pid": backend.target = replace(backend.target, pid=43)
    if change == "window": backend.target = replace(backend.target, window="other")
    if change == "exit": backend.target = replace(backend.target, scene="")
    if change in {"text", "unknown"}: backend.target = replace(backend.target, input_context=change)
    if change == "modal": backend.target = replace(backend.target, blocked=True)
    if change == "disconnect": c._disconnect_event.set()
    if change == "mode": c.ringGestures._generation += 1
    if change == "settings": catalog.clearApplicationBindings(POWERPOINT)
    if change == "voice": inline._view["phase"] = "listening"
    if change == "composition": inline._view["has_composition"] = True
    if change == "writing": inline._view["awaiting_readback"] = True
    if change == "sentence": inline._utterance_id = "new-sentence"
    if change == "focus_pending": c.ringGestures._fields.pending.set()
    if change == "scroll_pending": c.ringGestures._scroll.pending.set()
    if change == "expired":
        import proximic_ring.ui.app_gesture_controller as module
        now = module.time.monotonic()
        monkeypatch.setattr(module.time, "monotonic", lambda: now + 2)
    QCoreApplication.processEvents()
    assert sent == [] and messages == []


@pytest.mark.parametrize("context", ["exit", "text", "unknown"])
def test_tap_retains_original_voice_endpoint_outside_eligible_scene(presentation, context):
    c, _, inline, backend, _, sent, _ = presentation
    if context == "exit": backend.target = replace(backend.target, scene="")
    if context in {"text", "unknown"}: backend.target = replace(backend.target, input_context=context)
    if context == "voice": inline._view["phase"] = "listening"
    assert request(c, "tap", busy=context == "audio")
    assert sent == []


def test_clear_and_remove_cover_every_scene_and_never_restore_legacy(presentation):
    c, _, _, _, catalog, _, _ = presentation
    assert catalog.bindingCount(POWERPOINT) == 3
    catalog.selectScene("regular")
    assert catalog.clearApplicationBindings(POWERPOINT)
    assert catalog.for_scene(POWERPOINT, PRESENTATION) == {} and catalog.scene_bundles() == [POWERPOINT]
    assert not request(c, "tap")  # Active scenes suppress voice even without assigned actions.
    catalog.selectScene(PRESENTATION)
    assert catalog.setBinding(POWERPOINT, "tap", "powerpoint:end")
    assert catalog.removeApplication(POWERPOINT)
    assert not catalog.uses_scene("tap") and catalog.owns(POWERPOINT)
    saved = json.loads(c._settings.value(SETTINGS_KEY))[POWERPOINT]
    assert saved["removed"] and saved["scenes"] == {} and saved["bindings"] == {}


def test_scene_survives_menu_read_failure_and_can_inherit_without_menu(presentation):
    _, _, _, _, catalog, _, _ = presentation
    catalog._busy = True
    assert catalog.setBinding(POWERPOINT, "swipe-down", "powerpoint:next")
    assert catalog.setBinding(POWERPOINT, "swipe-down", "")
    catalog._apply_result("menu", catalog._generation["menu"], POWERPOINT, None, "读取失败")
    assert catalog.setCustomBinding(POWERPOINT, "swipe-down", "下一页", "Right")


@pytest.mark.parametrize("bundle,profile,start", [
    ("com.kingsoft.wpsoffice.mac", "wps", "Cmd+Return"),
    ("com.kingsoft.wpsoffice.mac.global", "wps", "Cmd+Return"),
    ("com.apple.iWork.Keynote", "keynote", "Cmd+Alt+P"),
    ("org.libreoffice.script", "libreoffice", "F5"),
    ("org.openoffice.script", "openoffice", "F5"),
    ("asc.onlyoffice.ONLYOFFICE", "onlyoffice", "Cmd+Shift+Return"),
])
def test_all_known_presenters_get_scene_and_standard_gesture_defaults(route, monkeypatch, bundle, profile, start):
    c, s, _, _, _, _, _ = route
    catalog = configure(s, monkeypatch, bundle)
    assert catalog.supportsPresentation and catalog.selectedScene == "regular"
    assert catalog.bindingCount(bundle) == 4
    assert catalog.scene_bundles() == [bundle]
    presets = [a for a in catalog.actions if a.get("preset")]
    assert all(a["id"].startswith(profile + ":") for a in presets)
    assert [a["shortcut"] for a in presets][0:1] == ([start] if start else [])
    catalog.selectScene(PRESENTATION)
    assert catalog.canBind("tap") and catalog.setBinding(bundle, "tap", profile + ":next")
    assert catalog.scene_bundles() == [bundle] and catalog.for_scene(bundle, PRESENTATION)["scene:tap"].shortcut == "Right"
    restored = ApplicationMappingController(s)
    try:
        monkeypatch.setattr(restored, "_request", lambda *args: None)
        restored.selectApplication(bundle)
        assert restored.supportsPresentation
        assert restored.for_scene(bundle, PRESENTATION) == catalog.for_scene(bundle, PRESENTATION)
    finally:
        restored.close()


@pytest.mark.parametrize("bundle,title", [
    ("com.kingsoft.wpsoffice.mac", "WPS 演示 - 幻灯片放映 — 测试"),
    ("com.kingsoft.wpsoffice.mac.global", "WPS Presentation Slide Show - Deck"),
    ("com.apple.iWork.Keynote", "Keynote Slideshow - Deck"),
    ("org.libreoffice.script", "LibreOffice Impress Slide Show - Deck"),
    ("asc.onlyoffice.ONLYOFFICE", "ONLYOFFICE Slide Show - Deck"),
])
def test_presenter_detection_requires_a_show_and_preserves_input_guard(bundle, title):
    window, focus = window_tree(title)
    assert presentation_context(bundle, window, focus, metadata) == (PRESENTATION, "nontext")
    focus["AXRole"] = "AXTextField"
    assert presentation_context(bundle, window, focus, metadata) == (PRESENTATION, "text")
    window.update(AXTitle="Spreadsheet.xlsx", AXFullScreen=True)
    assert presentation_context(bundle, window, focus, metadata) == ("", "unknown")


def test_generic_presentation_editor_persists_scene_and_requires_matching_native_identity(route, monkeypatch, tmp_path):
    from test_application_catalog import application
    c, s, _, _, _, _, _ = route
    bundle = "test.presentation.editor"
    app = application(tmp_path / 'Slides.app', bundle, CFBundleDocumentTypes=[{
        'CFBundleTypeRole': 'Editor', 'CFBundleTypeExtensions': ['odp']}])
    assert installed_presentation_profile(bundle, str(app)) == "generic"
    assert installed_presentation_profile("test.imposter", str(app)) == ""
    monkeypatch.setattr(s.catalog, "_request", lambda *args: None)
    s.catalog._candidates = [dict(value=bundle, label="Slides", path=str(app), presentationProfile="generic")]
    assert s.catalog.addApplication(bundle) and s.catalog.supportsPresentation
    s.catalog.selectScene(PRESENTATION)
    assert not scene_actions(bundle, PRESENTATION, profile="generic")  # No fabricated shortcuts.
    assert s.catalog.setCustomBinding(bundle, "tap", "下一页", "Right")
    restored = ApplicationMappingController(s)
    try:
        monkeypatch.setattr(restored, "_request", lambda *args: None)
        restored.selectApplication(bundle)
        assert restored.supportsPresentation and restored.for_scene(bundle, PRESENTATION)
        window, focus = window_tree("Slide Show - Deck")
        assert presentation_context(bundle, window, focus, metadata, profile="generic") == (PRESENTATION, "nontext")
        assert presentation_context(bundle, window, focus, metadata) == ("", "unknown")
    finally:
        restored.close()


def test_wps_and_powerpoint_bindings_never_cross_app_boundaries(presentation, monkeypatch):
    c, s, _, backend, _, sent, _ = presentation
    wps = "com.kingsoft.wpsoffice.mac"
    catalog = configure(s, monkeypatch, wps)
    catalog.selectScene(PRESENTATION)
    assert catalog.setBinding(wps, "swipe-left", "wps:previous")
    backend.target = replace(backend.target, bundle=wps)
    assert not request(c, "swipe-left") and sent == [(wps, "Backspace")]
    assert not request(c, "tap")  # Unassigned Tap is silent in every active scene.
    assert not request(c, "swipe-left", deliver=False)
    backend.target = replace(backend.target, bundle=POWERPOINT)
    QCoreApplication.processEvents()
    assert sent == [(wps, "Backspace")]
    assert not request(c, "swipe-left") and sent[-1] == (POWERPOINT, "Left")
    assert catalog.removeApplication(wps)
    assert catalog.scene_bundles() == [POWERPOINT]


def test_last_observation_and_hud_use_the_actual_app_not_editor_selection(presentation, monkeypatch):
    import time
    c, s, _, backend, _, _, _ = presentation
    wps = "com.kingsoft.wpsoffice.mac"
    catalog = configure(s, monkeypatch, wps)
    catalog._apps[wps]["label"] = "WPS Office"
    catalog.selectScene(PRESENTATION)
    assert catalog.setBinding(wps, "tap", "wps:next")
    target = replace(backend.target, bundle=wps)
    catalog.selectApplication(POWERPOINT)
    catalog.observe_scene(target)
    QCoreApplication.processEvents()
    assert "上次检测" not in catalog.sceneHint
    catalog.selectApplication(wps)
    assert "已识别到放映" in catalog.sceneHint
    shown = []
    c.ringGestures.sceneHudRequested.connect(lambda mode, items: shown.extend(items))
    c.ringGestures._show_scene_hud(c.ringGestures._generation, time.monotonic(), c._disconnect_event, target)
    assert shown and all(item["application"] == "WPS Office" for item in shown)


def test_existing_wps_record_gains_scene_without_readding_or_changing_bindings(route, monkeypatch):
    c, s, _, _, _, _, _ = route
    bundle = "com.kingsoft.wpsoffice.mac"
    original = {bundle: dict(label="WPS Office", bindings={"snap": dict(
        id="start", label="从头放映", path="放映", shortcut="F5")})}
    raw = json.dumps(original)
    c._settings.setValue(SETTINGS_KEY, raw)
    restored = ApplicationMappingController(s)
    monkeypatch.setattr(restored, "_request", lambda *args: None)
    try:
        restored.selectApplication(bundle)
        assert restored.supportsPresentation and restored.bindingCount(bundle) == 4
        restored.selectScene(PRESENTATION)
        assert restored.canBind("tap") and set(restored.bindings[bundle]) == {"swipe-left", "swipe-right", "snap"}
        assert restored.regularBindings[bundle] == original[bundle]["bindings"]
        assert c._settings.value(SETTINGS_KEY) == raw  # Migration discovery alone never writes settings.
    finally:
        restored.close()


def test_native_capture_verifies_generic_installed_editor_and_fresh_focus(monkeypatch, tmp_path):
    import sys
    import proximic_ring.mac_workspace as workspace
    from test_application_catalog import application
    bundle = "test.custom.slides"
    path = application(tmp_path / 'Slides.app', bundle, CFBundleDocumentTypes=[{
        'CFBundleTypeRole': 'Editor', 'CFBundleTypeExtensions': ['pptx']}])
    front = SimpleNamespace(bundleIdentifier=lambda: bundle, processIdentifier=lambda: 42,
        localizedName=lambda: "Slides", bundleURL=lambda: SimpleNamespace(path=lambda: str(path)))
    monkeypatch.setattr(workspace, "frontmost_application", lambda: front)
    monkeypatch.setattr(sys, "platform", "darwin")
    window, focus = window_tree("Slideshow - Deck")
    root = dict(AXFocusedWindow=window, AXFocusedUIElement=focus)
    monkeypatch.setitem(sys.modules, "ApplicationServices", SimpleNamespace(
        AXUIElementCreateApplication=lambda pid: root, AXUIElementSetMessagingTimeout=lambda *a: None,
        AXUIElementCopyAttributeValue=lambda node, key, _: (0, metadata(node, key))))
    backend = LocalMacAppShortcuts()
    captured = backend.capture(menu_action=True, scene=True)
    assert captured.scene == PRESENTATION and captured.input_context == "nontext"
    assert backend.same_target(captured)
    focus["AXRole"] = "AXTextArea"
    assert not backend.same_target(captured)
    front.bundleIdentifier = lambda: "test.different.app"
    target = backend.capture(menu_action=True, scene=True)
    assert target.bundle == "test.different.app" and not target.scene


def test_native_wps_dialog_exception_is_scene_only_and_rechecks_focus_and_foreground(monkeypatch):
    import sys
    import proximic_ring.mac_workspace as workspace
    front = SimpleNamespace(bundleIdentifier=lambda: "com.kingsoft.wpsoffice.mac", processIdentifier=lambda: 42,
                            localizedName=lambda: "WPS Office")
    monkeypatch.setattr(workspace, "frontmost_application", lambda: front)
    window, focus = wps_window_tree()
    root = dict(AXFocusedWindow=window, AXFocusedUIElement=focus)
    monkeypatch.setitem(sys.modules, "ApplicationServices", SimpleNamespace(
        AXUIElementCreateApplication=lambda pid: root, AXUIElementSetMessagingTimeout=lambda *a: None,
        AXUIElementCopyAttributeValue=lambda node, key, _: (0, metadata(node, key)),
        AXValueGetValue=lambda value, kind, _: (True, value), kAXValueCGPointType=1, kAXValueCGSizeType=2))
    backend = LocalMacAppShortcuts()
    assert backend.capture(menu_action=True).blocked
    captured = backend.capture(menu_action=True, scene=True)
    assert captured.scene == PRESENTATION and not captured.blocked and backend.same_target(captured)
    focus["AXRole"] = "AXTextArea"
    assert backend.capture(menu_action=True, scene=True).blocked
    assert not backend.same_target(captured)
    focus["AXRole"] = "AXGroup"
    latest = SimpleNamespace(bundleIdentifier=lambda: POWERPOINT, processIdentifier=lambda: 50)
    foreground = iter([front, latest])
    monkeypatch.setattr(workspace, "frontmost_application", lambda: next(foreground))
    assert backend.capture(menu_action=True, scene=True) is None


@pytest.mark.parametrize("identity,old,expected", [
    ("wps:start-first", "F5", "Cmd+Shift+Return"),
    ("wps:start-current", "Shift+F5", "Cmd+Return"),
    ("custom:my-start", "F5", "F5"), ("discovered-menu", "F5", "F5"),
    ("wps:start-first", "Ctrl+F5", "Ctrl+F5"),
])
def test_wps_only_obsolete_builtin_start_presets_are_repaired(route, monkeypatch, identity, old, expected):
    c, s, _, _, _, _, _ = route
    bundle = "com.kingsoft.wpsoffice.mac"
    raw = json.dumps({bundle: dict(label="WPS Office", bindings={"snap": dict(
        id=identity, label="开始放映", path="WPS 演示 快捷键", shortcut=old)})})
    c._settings.setValue(SETTINGS_KEY, raw)
    restored = ApplicationMappingController(s)
    try:
        assert restored.for_target(bundle)["menu:snap"].shortcut == expected
        assert restored.regularBindings[bundle]["snap"]["shortcut"] == expected
        assert c._settings.value(SETTINGS_KEY) == raw
    finally:
        restored.close()


def test_native_worker_rechecks_scene_before_autofocus_and_drops_pinned_plan(monkeypatch):
    from proximic_ring import native_access_worker as worker
    from proximic_ring.mac_permissions import PermissionState
    monkeypatch.setattr(worker, "read_permission_state", lambda: PermissionState(True, False))
    dispatcher = worker.Dispatcher()
    dispatcher.shortcuts = SimpleNamespace(capture=lambda **kw: ShortcutTarget(POWERPOINT, 42, POWERPOINT,
        scene=PRESENTATION, input_context="unknown"))
    dispatcher.text_focus = SimpleNamespace(plans={"old": object()}, handle=lambda *a, **kw: pytest.fail("No focus change in show"))
    result = dispatcher.handle({"operation": "focus_apply", "scene_apps": [POWERPOINT], "plan": "old"})
    assert result["result"]["status"] == "presentation"
    assert dispatcher.text_focus.plans == {}


def test_scene_hud_reports_effective_bindings_and_respects_busy_state(presentation):
    import time
    c, _, inline, backend, _, sent, messages = presentation
    scenes, defaults = [], []
    ring = c.ringGestures
    ring.sceneHudRequested.connect(lambda mode, items: scenes.append(items))
    ring.showRequested.connect(lambda mode, text: defaults.append((mode, text)))
    ring._show_scene_hud(ring._generation, time.monotonic(), c._disconnect_event, backend.target)
    assert {item["action"] for item in scenes[0]} == {"上一个动画 / 上一页", "下一个动画 / 下一页", "结束放映",
                                                    "未绑定", "手势提示", "切换交互模式", "窗口选择"}
    assert all(item["voiceDisabled"] for item in scenes[0])
    inline._view["phase"] = "listening"
    ring._show_scene_hud(ring._generation, time.monotonic(), c._disconnect_event, backend.target)
    assert defaults == [("input", "")] and not sent and not messages


def test_real_runtime_presentation_tap_skips_audio_then_restores_voice_after_exit(presentation, monkeypatch):
    import threading
    import numpy as np
    from proximic_ring import app_runtime
    import proximic_ring.firmware_gestures as host
    from proximic_ring.asr.controller import ProximitySessionController
    c, _, _, backend, _, sent, _ = presentation
    state, started, finals, globals_ = {}, [], [], []
    recognition, disconnect = threading.Event(), c._disconnect_event
    recognition.set()
    class Dispatcher:
        error = None
        def __init__(self, *, on_gesture): state["gesture"] = on_gesture
        def submit(self, event): state["gesture"](event)
        def start(self): pass
        def close(self): pass
        def snapshot(self): return {}
    def gesture():
        thread = threading.Thread(target=lambda: state["gesture"](SimpleNamespace(name="tap")))
        thread.start(); thread.join(1)
        assert not thread.is_alive()
    class Sink:
        def start(self, audio): started.append(audio)
        def feed(self, audio): pass
        def end(self, audio): finals.append(audio)
        def abort(self): pass
        def close(self): pass
    class Source:
        error = None
        def __init__(self, **kwargs): self.reads = 0
        def connect(self): pass
        def start_stream(self, **kwargs): pass
        def close(self): pass
        def read(self, frames):
            self.reads += 1
            gate = state["gate"]
            if self.reads == 1:
                gesture()
                QCoreApplication.processEvents()
                assert sent == [(POWERPOINT, "Escape")] and not gate.gesture_busy and not started
                backend.target = replace(backend.target, scene="")
                gesture()
                assert gate.gesture_busy
            elif self.reads == 2:
                assert gate.active and len(started) == 1
                gesture()
            else:
                assert len(finals) == 1
                disconnect.set()
                return None
            return np.ones(frames, dtype=np.float32) * .1
    def build(*args, **kwargs):
        state["gate"] = ProximitySessionController(Sink(), start_on_gesture=True, min_utterance_s=.02)
        return state["gate"]
    monkeypatch.setattr(host, "FirmwareGestureWorker", Dispatcher)
    monkeypatch.setattr(app_runtime, "RingAudioSource", Source)
    monkeypatch.setattr(app_runtime, "_build_session_controller", build)
    app_runtime.RecognitionRuntime(app_runtime.RuntimeSettings(speech_control_mode="gesture")).run(
        disconnect, recognition, on_update=lambda _: None, on_state=lambda _: None,
        on_connected=lambda: None, on_disconnected=lambda: None, on_started=lambda: None,
        on_gesture=globals_.append,
        gesture_filter=lambda event, busy: c.ringGestures.filter(event, busy, disconnect))
    assert sent == [(POWERPOINT, "Escape")] and len(started) == len(finals) == 1
    assert not globals_
