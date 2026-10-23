"""Mythlink uses the same saved application/global routing as external apps."""
from types import SimpleNamespace
import sys

import pytest

from proximic_ring.desktop.app_shortcuts import LocalMacAppShortcuts
from proximic_ring.desktop.host_application import HOST_BUNDLE
from proximic_ring.desktop.application_menus import application_candidates, read_application_menu
from proximic_ring.ui.desktop.application_mapping_controller import ApplicationMappingController
from test_app_gestures import route
from test_ring_gestures import request


@pytest.fixture
def host_desktop(monkeypatch):
    import proximic_ring.platforms.macos.mac_workspace as workspace
    monkeypatch.setattr(sys, "platform", "darwin")
    state = SimpleNamespace(bundle="", pid=42, foreground=True, window="window")
    values = {("window", "AXRole"): "AXWindow", ("window", "AXSubrole"): "AXSystemDialog",
              ("window", "AXTitle"): "Mythlink Agent · 任务", ("window", "AXModal"): False,
              ("focus", "AXRole"): "AXGroup", ("focus", "AXWindow"): "window",
              ("focus", "AXParent"): "window", ("focus", "AXFocused"): True,
              ("app", "AXMenuBar"): "menu", ("menu", "AXRole"): "AXMenuBar"}
    def read(node, name, _):
        assert name not in {"AXValue", "AXSelectedText"}
        if node == "app" and name == "AXFocusedWindow":
            return 0, state.window
        if node == "app" and name == "AXFocusedUIElement":
            return 0, "focus"
        return 0, values.get((node, name))
    monkeypatch.setitem(sys.modules, "ApplicationServices", SimpleNamespace(
        AXUIElementCreateApplication=lambda pid: "app",
        AXUIElementCopyAttributeValue=read,
        AXUIElementSetMessagingTimeout=lambda *args: None))
    app = SimpleNamespace(bundleIdentifier=lambda: state.bundle, processIdentifier=lambda: state.pid,
                          localizedName=lambda: "Python", activationPolicy=lambda: 1, bundleURL=lambda: None)
    monkeypatch.setattr(workspace, "frontmost_application", lambda: app if state.foreground else None)
    monkeypatch.setattr(workspace, "running_applications", lambda: [app] if state.foreground else [])
    return LocalMacAppShortcuts(host_pid=42), state, values


@pytest.mark.parametrize("bundle", ["", "org.python.python", HOST_BUNDLE])
def test_host_identity_matches_picker_capture_and_menu_in_source_and_packaged_runs(host_desktop, bundle):
    backend, state, _ = host_desktop
    state.bundle = bundle
    candidates = application_candidates(host_pid=42)
    assert [(app["value"], app["label"]) for app in candidates] == [(HOST_BUNDLE, "Mythlink")]
    target = backend.capture(menu_action=True)
    assert target.bundle == HOST_BUNDLE and not target.blocked and target.input_context == "nontext"
    assert backend.same_target(target, require_focus=True)
    assert read_application_menu(HOST_BUNDLE, host_pid=42)["pid"] == 42
    # The IME still receives the native client identity, not a configuration alias.
    assert backend.capture(plain_enter=True).bundle == bundle


@pytest.mark.parametrize("change", ["other_process", "dialog", "permission", "modal", "sheet", "minimized", "menu"])
@pytest.mark.parametrize("focus_sensitive", [False, True])
def test_local_workspace_exception_keeps_real_dialog_and_foreground_guards(host_desktop, change, focus_sensitive):
    backend, state, values = host_desktop
    target = backend.capture(menu_action=True, focus_sensitive=focus_sensitive)
    if change == "other_process": state.pid = 43
    if change == "dialog": values["window", "AXSubrole"] = "AXDialog"
    if change == "permission": values["window", "AXTitle"] = "Mythlink Agent · 请求"
    if change == "modal": values["window", "AXModal"] = True
    if change == "sheet": values["window", "AXSheets"] = ["sheet"]
    if change == "minimized": values["window", "AXMinimized"] = True
    if change == "menu": values["focus", "AXRole"] = "AXMenu"
    assert backend.capture(menu_action=True, focus_sensitive=focus_sensitive).blocked
    assert not backend.same_target(target, require_focus=True)


def test_mythlink_candidate_survives_discovery_failure_without_implicit_bindings(route, monkeypatch):
    _, service, *_ = route
    catalog = service.catalog
    monkeypatch.setattr(catalog, "_request", lambda *args: None)
    catalog._apply_result("apps", 0, "", None, "discovery unavailable")
    assert [app["value"] for app in catalog.candidates] == [HOST_BUNDLE]
    assert not catalog.apps and not catalog.for_target(HOST_BUNDLE)
    assert catalog.addApplication(HOST_BUNDLE)
    assert not catalog.for_target(HOST_BUNDLE)
    assert catalog.setCustomBinding(HOST_BUNDLE, "middle-pinch", "下一个标签", "Ctrl+Tab")
    restored = ApplicationMappingController(service)
    try:
        assert restored.for_target(HOST_BUNDLE)["menu:middle-pinch"].shortcut == "Ctrl+Tab"
    finally:
        restored.close()


@pytest.mark.parametrize("bundle", ["", HOST_BUNDLE])
@pytest.mark.parametrize("title", ["Mythlink", "Mythlink Agent · 任务"])
def test_local_binding_wins_and_absent_cleared_or_removed_binding_uses_global(route, host_desktop, monkeypatch, bundle, title):
    c, service, _, _, _, _, _ = route
    backend, state, values = host_desktop
    state.bundle = bundle
    values["window", "AXTitle"] = title
    service.backend = backend
    # Include the early Agent filter: an unassigned pinch must reach routing.
    c.agent._focused = True
    c.agent.workspace.focus = "task"
    starts, sent = [], []
    monkeypatch.setattr(c.ringGestures._selector, "start", lambda *args: starts.append(True))
    def post(target, shortcut, **options):
        assert backend.same_target(target, **options)
        sent.append(shortcut)
    monkeypatch.setattr(backend, "post", post)
    assert c.ringGestures.setGlobalBinding("window_selector", "middle-pinch")
    request(c, "middle-pinch")
    assert starts == [True] and not sent
    catalog = service.catalog
    monkeypatch.setattr(catalog, "_request", lambda *args: None)
    assert catalog.addApplication(HOST_BUNDLE)
    request(c, "middle-pinch")
    assert len(starts) == 2 and not sent
    assert catalog.setCustomBinding(HOST_BUNDLE, "middle-pinch", "下一个标签", "Ctrl+Tab")
    request(c, "middle-pinch")
    assert sent == ["Ctrl+Tab"] and len(starts) == 2
    assert catalog.setBinding(HOST_BUNDLE, "middle-pinch", "")
    request(c, "middle-pinch")
    assert len(starts) == 3 and len(sent) == 1
    catalog.removeApplication(HOST_BUNDLE)
    request(c, "middle-pinch")
    assert len(starts) == 4 and len(sent) == 1
