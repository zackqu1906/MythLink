"""Built-in mappings are ordinary editable records, never a runtime fallback."""
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from proximic_ring.application_defaults import default_mappings, DEFAULTS_VERSION
from proximic_ring.gesture_scenes import PRESENTATION, POWERPOINT
from proximic_ring.ui.application_mapping_controller import ApplicationMappingController, SETTINGS_KEY
from test_app_gestures import route
from test_application_menus import configure, ACTION
from test_gesture_scenes import presentation
from test_ring_gestures import request

WPS = "com.kingsoft.wpsoffice.mac"
CODEX = "com.openai.codex"
WORKBUDDY = "com.tencent.workbuddy.mac"


@pytest.mark.parametrize("bundle,label,count", [
    (WPS, "WPS Office", 4), (WPS + ".global", "WPS Office", 4), (POWERPOINT, "PowerPoint", 4),
    (CODEX, "Codex", 2), (WORKBUDDY, "WorkBuddy", 2),
    ("vendor.WorkBuddy", "WorkBuddy", 2), ("com.example.Editor", "Editor", 0),
    ("com.example.Viewer", "WorkBuddy Helper", 0),
])
def test_defaults_are_installed_only_after_add_and_persist(route, monkeypatch, bundle, label, count):
    c, service, _, _, _, _, _ = route
    catalog = service.catalog
    monkeypatch.setattr(catalog, "_request", lambda *args: None)
    before = c.gestureBindings, c.ringGestures.globalBindings
    assert catalog.apps == [] and not catalog.owns(bundle)
    catalog._candidates = [dict(value=bundle, label=label)]
    catalog.addApplication(bundle)
    assert catalog.bindingCount(bundle) == count
    assert catalog.hasDefaultMappings == bool(count)
    expected = default_mappings(bundle, label)
    assert catalog.regularBindings[bundle] == expected.get("regular", {})
    for scene in expected:
        assert catalog._bindings_for(bundle, scene) == expected[scene]
    restored = ApplicationMappingController(service)
    try:
        assert restored.apps == catalog.apps
        assert restored.regularBindings == catalog.regularBindings
        assert restored._bindings_for(bundle, PRESENTATION) == catalog._bindings_for(bundle, PRESENTATION)
    finally:
        restored.close()
    assert (c.gestureBindings, c.ringGestures.globalBindings) == before


@pytest.mark.parametrize("bundle", [WPS, CODEX, WORKBUDDY])
def test_edited_and_deleted_defaults_stay_user_owned_through_refresh_and_reload(route, monkeypatch, bundle):
    c, service, _, _, _, _, _ = route
    catalog = configure(service, monkeypatch, bundle)
    gesture = "snap" if bundle == WPS else "circle-clockwise"
    assert catalog.setCustomBinding(bundle, gesture, "我的操作", "Cmd+Alt+N")
    if bundle == WPS:
        catalog.selectScene(PRESENTATION)
        gesture = "swipe-left"
    else:
        gesture = "circle-counterclockwise"
    assert catalog.setBinding(bundle, gesture, "")
    expected = json.loads(c._settings.value(SETTINGS_KEY))
    assert catalog.addApplication(bundle)  # Repeated add is only a selection.
    catalog.refreshMenu()
    catalog._apply_result("menu", catalog._generation["menu"], bundle, None, "菜单读取失败")
    catalog._apply_result("apps", catalog._generation["apps"], "", catalog._candidates, "")
    assert json.loads(c._settings.value(SETTINGS_KEY)) == expected
    restored = ApplicationMappingController(service)
    try:
        assert restored._bindings_for(bundle, "regular") == expected[bundle]["bindings"]
        assert restored._bindings_for(bundle, PRESENTATION) == expected[bundle].get("scenes", {}).get(PRESENTATION, {})
    finally:
        restored.close()
    catalog.clearApplicationBindings(bundle)
    catalog.addApplication(bundle)
    assert catalog.bindingCount(bundle) == 0
    restored = ApplicationMappingController(service)
    try:
        assert restored.bindingCount(bundle) == 0
    finally:
        restored.close()


def test_upgrade_preserves_empty_app_and_custom_bindings_while_filling_missing_slots(route, monkeypatch):
    c, service, _, _, _, _, _ = route
    saved = {WPS: dict(label="WPS", bindings={}), CODEX: dict(label="Codex", bindings={"snap": ACTION})}
    c._settings.setValue(SETTINGS_KEY, json.dumps(saved))
    restored = ApplicationMappingController(service)
    try:
        assert restored.bindingCount(WPS) == 0 and restored.bindingCount(CODEX) == 3
        assert restored._bindings_for(CODEX)["snap"] == ACTION
        assert json.loads(c._settings.value(SETTINGS_KEY)) == saved
    finally:
        restored.close()


def test_restore_and_readd_are_explicit_and_leave_other_apps_untouched(route, monkeypatch):
    c, service, _, _, _, _, _ = route
    catalog = configure(service, monkeypatch, WPS)
    catalog.setCustomBinding(WPS, "snap", "我的播放", "Cmd+P")
    catalog._candidates.append(dict(value=CODEX, label="Codex"))
    catalog.addApplication(CODEX)
    catalog.setCustomBinding(CODEX, "circle-clockwise", "我的切换", "Cmd+Alt+N")
    other = dict(catalog.regularBindings[CODEX])
    cleared = []
    catalog.applicationBindingsCleared.connect(cleared.append)
    assert catalog.restoreDefaultMappings(WPS)
    assert catalog._bindings_for(WPS) == default_mappings(WPS)["regular"]
    assert catalog.regularBindings[CODEX] == other and catalog.selectedApp == CODEX
    assert cleared == [WPS]
    catalog.removeApplication(WPS)
    assert not catalog.restoreDefaultMappings(WPS)
    catalog.addApplication(WPS)
    assert catalog.bindingCount(WPS) == 4
    assert catalog.regularBindings[CODEX] == other


def test_global_occupancy_suspends_defaults_without_changing_global_or_voice_rules(route, monkeypatch):
    c, service, _, _, _, _, _ = route
    assert c.ringGestures.setGlobalBinding("window_selector", "snap")
    before = c.gestureBindings, c.ringGestures.globalBindings
    catalog = configure(service, monkeypatch, WPS)
    assert "snap" in catalog.regularBindings[WPS]
    assert not catalog.for_target(WPS) and catalog.globalConflictNotice
    assert not catalog.voice_overridden(WPS)
    assert (c.gestureBindings, c.ringGestures.globalBindings) == before
    assert c.ringGestures.setGlobalBinding("window_selector", "clench")
    assert catalog.for_target(WPS)["menu:snap"].shortcut == "Cmd+Return"


@pytest.mark.parametrize("bundle,prefix", [(CODEX, "Cmd+Shift"), (WORKBUDDY, "Cmd")])
def test_chat_defaults_use_existing_foreground_route_and_user_changes_win(route, monkeypatch, bundle, prefix):
    _, service, _, backend, sent, _, emit = route
    catalog = configure(service, monkeypatch, bundle)
    backend.target = replace(backend.target, bundle=bundle, profile=bundle)
    emit("circle-clockwise")
    emit("circle-counterclockwise")
    assert sent == [(bundle, prefix + "+]"), (bundle, prefix + "+[")]
    assert catalog.setCustomBinding(bundle, "circle-clockwise", "我的切换", "Ctrl+Tab")
    emit("circle-clockwise")
    assert sent[-1] == (bundle, "Ctrl+Tab")
    catalog.setBinding(bundle, "circle-clockwise", "")
    emit("circle-clockwise")
    assert len(sent) == 3
    backend.target = replace(backend.target, bundle="unconfigured.app", profile="")
    emit("circle-counterclockwise")
    assert len(sent) == 3


def test_wps_defaults_route_only_in_their_own_app_and_scene(presentation, monkeypatch):
    c, service, _, backend, _, sent, _ = presentation
    catalog = configure(service, monkeypatch, WPS)
    backend.target = replace(backend.target, bundle=WPS, profile=WPS, scene="", input_context="nontext")
    assert request(c, "snap")  # Normal app shortcuts continue down the existing route.
    event = c.ringGestures.envelope(SimpleNamespace(name="snap"))
    c._apply_gesture(event, c._disconnect_event)
    assert sent == [(WPS, "Cmd+Return")]
    backend.target = replace(backend.target, scene=PRESENTATION)
    for gesture in ("swipe-left", "swipe-right", "snap"):
        assert not request(c, gesture)
    assert sent == [(WPS, "Cmd+Return"), (WPS, "Backspace"), (WPS, "Right"), (WPS, "Escape")]
    assert not catalog.voice_overridden(WPS)  # Scene swipes do not suppress regular voice.
    backend.target = replace(backend.target, bundle="other.app", profile="", scene="")
    request(c, "swipe-left")
    assert len(sent) == 4


def test_installed_scan_adds_only_requested_four_and_never_changes_selection(route, monkeypatch):
    c, service, _, _, _, _, _ = route
    catalog = service.catalog
    requested = []
    monkeypatch.setattr(catalog, "_request", lambda *args: requested.append(args))
    before = c.gestureBindings, c.ringGestures.globalBindings
    catalog.initializeInstalledApplications()
    catalog.initializeInstalledApplications()
    assert requested == [("apps",)]
    candidates = [dict(value=bundle, label=label) for bundle, label in [
        (CODEX, "Codex"), (WORKBUDDY, "WorkBuddy"), (WPS, "WPS Office"), (POWERPOINT, "PowerPoint"),
        ("com.apple.iWork.Keynote", "Keynote"), ("com.example.Other", "Other")]]
    catalog._apply_result("apps", catalog._generation["apps"], "", dict(candidates=candidates), "")
    assert {item["value"] for item in catalog.apps} == {CODEX, WORKBUDDY, WPS, POWERPOINT}
    assert catalog.selectedApp == "" and requested == [("apps",)]
    assert (c.gestureBindings, c.ringGestures.globalBindings) == before
    assert catalog.bindingCount(WPS) == catalog.bindingCount(POWERPOINT) == 4
    assert catalog.bindingCount(CODEX) == catalog.bindingCount(WORKBUDDY) == 2
    saved = c._settings.value(SETTINGS_KEY)
    catalog._apply_result("apps", catalog._generation["apps"], "", dict(candidates=candidates), "")
    assert c._settings.value(SETTINGS_KEY) == saved
    catalog.removeApplication(WPS)
    catalog.clearApplicationBindings(CODEX)
    catalog._apply_result("apps", catalog._generation["apps"], "", dict(candidates=candidates), "")
    assert not catalog._is_added(WPS) and catalog.bindingCount(CODEX) == 0
    restored = ApplicationMappingController(service)
    try:
        restored._apply_result("apps", 0, "", dict(candidates=candidates), "")
        assert not restored._is_added(WPS) and restored.bindingCount(CODEX) == 0
    finally:
        restored.close()


def test_no_installed_targets_creates_no_placeholder_apps_and_later_install_is_discovered(route, monkeypatch):
    _, service, _, _, _, _, _ = route
    catalog = service.catalog
    monkeypatch.setattr(catalog, "_request", lambda *args: None)
    catalog._apply_result("apps", 0, "", dict(candidates=[], partial=True), "")
    assert catalog.apps == []
    catalog._apply_result("apps", 0, "", [dict(value=POWERPOINT, label="PowerPoint")], "")
    assert [item["value"] for item in catalog.apps] == [POWERPOINT]


def test_corrupt_settings_are_not_overwritten_by_automatic_onboarding(route, monkeypatch):
    c, service, _, _, _, _, _ = route
    c._settings.setValue(SETTINGS_KEY, "invalid-json")
    catalog = ApplicationMappingController(service)
    monkeypatch.setattr(catalog, "_request", lambda *args: pytest.fail("No automatic reset of corrupt settings"))
    try:
        catalog.initializeInstalledApplications()
        catalog._apply_result("apps", 0, "", [dict(value=CODEX, label="Codex")], "")
        assert catalog.apps == [] and c._settings.value(SETTINGS_KEY) == "invalid-json"
    finally:
        catalog.close()


def test_existing_wps_template_gains_end_show_once_and_custom_keys_survive(route, monkeypatch):
    c, service, _, _, _, _, _ = route
    defaults = default_mappings(WPS)
    defaults[PRESENTATION].pop("snap")  # Previous release's template.
    defaults[PRESENTATION]["swipe-left"] = dict(id="custom:mine", label="上一页", path="自定", shortcut="PageUp")
    c._settings.setValue(SETTINGS_KEY, json.dumps({WPS: dict(label="WPS", bindings=defaults["regular"],
                                                          scenes={PRESENTATION: defaults[PRESENTATION]})}))
    catalog = ApplicationMappingController(service)
    monkeypatch.setattr(catalog, "_request", lambda *args: None)
    try:
        assert catalog._bindings_for(WPS, PRESENTATION)["snap"]["shortcut"] == "Escape"
        assert catalog._bindings_for(WPS, PRESENTATION)["swipe-left"]["shortcut"] == "PageUp"
        catalog.selectApplication(WPS); catalog.selectScene(PRESENTATION)
        assert catalog.setBinding(WPS, "snap", "")
        restored = ApplicationMappingController(service)
        try:
            assert "snap" not in restored._bindings_for(WPS, PRESENTATION)
            assert restored._apps[WPS]["defaultsVersion"] == DEFAULTS_VERSION
        finally:
            restored.close()
    finally:
        catalog.close()


@pytest.mark.parametrize("change", ["none", "custom", "delete", "clear", "remove"])
def test_generic_presenter_resolves_actual_start_menu_without_overwriting_user_action(route, monkeypatch, change):
    c, service, _, _, _, _, _ = route
    catalog = service.catalog
    monkeypatch.setattr(catalog, "_request", lambda *args: None)
    bundle = "test.generic.slides"
    catalog._candidates = [dict(value=bundle, label="Slides", presentationProfile="generic")]
    catalog.addApplication(bundle)
    assert catalog.bindingCount(bundle) == 3 and catalog.defaultMappingNotice
    assert set(catalog.for_scene(bundle, PRESENTATION)) == {"scene:swipe-left", "scene:swipe-right", "scene:snap"}
    catalog.selectScene(PRESENTATION)
    assert {item["shortcut"] for item in catalog.actions if item.get("preset")} >= {"Left", "Right", "Escape"}
    catalog.selectScene("regular")
    if change == "custom": catalog.setCustomBinding(bundle, "snap", "我的放映", "Cmd+Alt+P")
    if change == "delete": catalog.setBinding(bundle, "snap", "")
    if change == "clear": catalog.clearApplicationBindings(bundle)
    if change == "remove": catalog.removeApplication(bundle)
    menu = dict(id="native:start", label="From Current Slide", path="Slide Show › From Current Slide", shortcut="Ctrl+Shift+F5")
    catalog._apply_result("menu", catalog._generation["menu"], bundle, dict(actions=[menu]), "")
    if change == "none":
        assert catalog.for_target(bundle)["menu:snap"].shortcut == "Ctrl+Shift+F5"
        assert not catalog.defaultMappingNotice and catalog.bindingCount(bundle) == 4
    elif change == "custom": assert catalog.for_target(bundle)["menu:snap"].shortcut == "Cmd+Alt+P"
    else: assert not catalog.for_target(bundle)
    restored = ApplicationMappingController(service)
    try:
        assert restored.regularBindings == catalog.regularBindings
    finally:
        restored.close()


def test_installed_scan_survives_unavailable_native_channel(route, monkeypatch):
    import proximic_ring.ui.application_mapping_controller as module
    from PySide6.QtCore import QCoreApplication
    _, service, _, _, _, _, _ = route
    catalog = service.catalog
    def unavailable(*args, **kwargs): raise RuntimeError("permission helper unavailable")
    monkeypatch.setattr(catalog.channel, "call", unavailable)
    monkeypatch.setattr(module, "installed_applications", lambda running: dict(candidates=[dict(value=CODEX, label="Codex")]))
    class ImmediateThread:
        def __init__(self, *, target, **kwargs): self.target = target
        def start(self): self.target()
    monkeypatch.setattr(module.threading, "Thread", ImmediateThread)
    catalog.initializeInstalledApplications()
    QCoreApplication.processEvents()
    assert catalog._is_added(CODEX) and catalog.bindingCount(CODEX) == 2
