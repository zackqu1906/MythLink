"""A persistent shortcut worker must observe app changes without a GUI loop."""
import sys
from types import SimpleNamespace

import pytest

from proximic_ring.app_shortcuts import LocalMacAppShortcuts
from test_app_shortcuts import desktop


@pytest.fixture
def workspace(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    import proximic_ring.mac_workspace as workspace_module
    # This fixture represents the pipe worker, even if earlier tests created Qt.
    monkeypatch.setattr(workspace_module, "_on_qt_event_thread", lambda: False)
    apps = {
        bundle: SimpleNamespace(bundleIdentifier=lambda b=bundle: b,
                                processIdentifier=lambda p=pid: p,
                                localizedName=lambda n=name: n)
        for bundle, pid, name in [("com.apple.finder", 1, "Finder"),
                                  ("com.tencent.xinWeChat", 2, "WeChat"),
                                  ("com.openai.codex", 3, "Codex")]
    }
    state = SimpleNamespace(cached="com.apple.finder", actual="com.apple.finder")

    def drain(_deadline):
        state.cached = state.actual

    monkeypatch.setitem(sys.modules, "Foundation", SimpleNamespace(
        NSDate=SimpleNamespace(dateWithTimeIntervalSinceNow_=lambda seconds: seconds),
        NSRunLoop=SimpleNamespace(currentRunLoop=lambda: SimpleNamespace(runUntilDate_=drain))))
    monkeypatch.setitem(sys.modules, "AppKit", SimpleNamespace(NSWorkspace=SimpleNamespace(
        sharedWorkspace=lambda: SimpleNamespace(frontmostApplication=lambda: apps[state.cached]))))
    window = {"AXRole": "AXWindow"}
    root = {"AXFocusedWindow": window}
    monkeypatch.setitem(sys.modules, "ApplicationServices", SimpleNamespace(
        AXUIElementCreateApplication=lambda pid: pid,
        AXUIElementSetMessagingTimeout=lambda *args: None,
        AXUIElementCopyAttributeValue=lambda node, key, _: (0, (root if isinstance(node, int) else node).get(key))))
    return state


@pytest.mark.parametrize("bundle,profile", [
    ("com.tencent.xinWeChat", "wechat"), ("com.openai.codex", "codex")])
def test_capture_recovers_after_switching_from_unsupported_app(workspace, bundle, profile):
    backend = LocalMacAppShortcuts()
    assert backend.capture() is None
    workspace.actual = bundle
    target = backend.capture()
    assert target is not None and target.profile == profile


def test_target_revalidation_rejects_app_switch_before_posting(workspace, desktop):
    backend = LocalMacAppShortcuts()
    workspace.actual = workspace.cached = "com.openai.codex"
    target = backend.capture()
    workspace.actual = "com.tencent.xinWeChat"
    # Allocating events is read-only; none may be posted to a stale target.
    with pytest.raises(RuntimeError, match="前台应用已变化") as error:
        backend.post(target, "Cmd+Shift+]")
    assert error.value.reason == "foreground_changed"
    assert not desktop[1].sent
    assert backend.capture().profile == "wechat"


def test_menu_mapping_refreshes_cached_frontmost_record_before_capture_and_post(workspace, desktop):
    backend = LocalMacAppShortcuts()
    workspace.cached, workspace.actual = "com.openai.codex", "com.apple.finder"
    target = backend.capture(menu_action=True)
    assert target.bundle == "com.apple.finder" and target.menu_action
    assert backend.same_target(target)
    workspace.actual = "com.tencent.xinWeChat"
    with pytest.raises(RuntimeError, match="前台应用已变化") as error:
        backend.post(target, "Cmd+N")
    assert error.value.reason == "foreground_changed"
    assert not desktop[1].sent
    fresh = backend.capture(menu_action=True)
    assert fresh.bundle == "com.tencent.xinWeChat" and fresh.pid == 2
