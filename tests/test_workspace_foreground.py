"""Headless workers refresh queued workspace activation notifications."""
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def desktop(monkeypatch):
    from proximic_ring import mac_workspace
    monkeypatch.setattr(mac_workspace, "_on_qt_event_thread", lambda: False)
    monkeypatch.setattr(sys, "platform", "darwin")
    def app(pid, bundle):
        return SimpleNamespace(processIdentifier=lambda: pid, bundleIdentifier=lambda: bundle,
                               localizedName=lambda: "Codex", activationPolicy=lambda: 0,
                               isHidden=lambda: False)
    old, new = app(11, "com.apple.finder"), app(22, "com.openai.codex")
    state = SimpleNamespace(actual=new, cached=old, reads=[], sent=[], requested=[])
    ws = SimpleNamespace(frontmostApplication=lambda: state.cached,
                         runningApplications=lambda: [state.cached] if state.cached else [])
    def workspace():
        state.reads.append("workspace")
        return ws
    def drain(interval):
        assert state.reads and state.reads[-1] == "workspace"
        assert 0 < interval <= .005
        state.cached = state.actual
    monkeypatch.setitem(sys.modules, "AppKit", SimpleNamespace(
        NSWorkspace=SimpleNamespace(sharedWorkspace=workspace), NSApplicationActivationPolicyRegular=0))
    monkeypatch.setitem(sys.modules, "Foundation", SimpleNamespace(
        NSRunLoop=SimpleNamespace(currentRunLoop=lambda: SimpleNamespace(runUntilDate_=drain)),
        NSDate=SimpleNamespace(dateWithTimeIntervalSinceNow_=lambda interval: interval)))
    monkeypatch.setitem(sys.modules, "Quartz", SimpleNamespace(
        kCGWindowListOptionOnScreenOnly=1, kCGWindowListExcludeDesktopElements=2,
        kCGNullWindowID=0, kCGWindowNumber="number", kCGWindowOwnerPID="pid",
        kCGWindowLayer="layer", kCGWindowBounds="bounds",
        CGWindowListCopyWindowInfo=lambda *args: [
            {"number": 101, "pid": 11, "layer": 0,
             "bounds": {"X": 0, "Y": 0, "Width": 200, "Height": 100}},
            {"number": 202, "pid": 22, "layer": 0,
             "bounds": {"X": 300, "Y": 0, "Width": 400, "Height": 200}}]))
    return state


@pytest.mark.parametrize("reader", ["input_source", "stamp", "settings", "hud", "inline", "activation", "apps"])
def test_each_consumer_uses_new_foreground_after_switch(desktop, reader):
    if reader == "input_source":
        from proximic_ring.input_source_switch import foreground_pid
        assert foreground_pid() == 22
    elif reader == "stamp":
        from proximic_ring.text_focus import foreground_stamp
        assert foreground_stamp() == {"pid": 22, "window": 202}
    elif reader == "settings":
        from proximic_ring.wechat_setup import KeyboardShortcutsNavigator
        assert KeyboardShortcutsNavigator._front() is desktop.actual
    elif reader == "hud":
        from proximic_ring.ui.gesture_hud import foreground_window_bounds
        assert foreground_window_bounds().x() == 300
    elif reader == "inline":
        from proximic_ring.ui.inline_controller import InlineInputController
        assert InlineInputController._foreground_identity(None) == ("com.openai.codex", 22)
    elif reader == "activation":
        from proximic_ring.ui.input_source_activation import InputSourceActivation
        source = SimpleNamespace(_stage="probing", _process=SimpleNamespace(processId=lambda: 22))
        assert InputSourceActivation.owns_foreground(source)
    else:
        from proximic_ring.window_selector import MacWindowAX
        assert set(MacWindowAX.apps(None)) == {22}


def test_no_foreground_does_not_reuse_previous_app(desktop):
    from proximic_ring.input_source_switch import foreground_pid
    from proximic_ring.text_focus import foreground_stamp
    desktop.actual = None
    assert foreground_stamp() == {"pid": 0, "window": 0}
    with pytest.raises(RuntimeError, match="未找到前台应用"):
        foreground_pid()


def test_sentence_request_pins_current_app_not_cached_app(desktop, monkeypatch):
    from proximic_ring import native_access, wechat_native_keys
    monkeypatch.setattr(native_access, "native_access", lambda: SimpleNamespace(
        call=lambda operation, **kw: desktop.requested.append((operation, kw))))
    wechat_native_keys._send_key("delete", 0, 123, "com.openai.codex")
    assert desktop.requested[0][1]["pid"] == 22
    desktop.actual = None
    with pytest.raises(RuntimeError, match="输入目标已切换"):
        wechat_native_keys._send_key("delete", 0, 123, "com.openai.codex")
    assert len(desktop.requested) == 1


def test_app_switch_during_multi_key_operation_stops_at_balanced_pair(desktop):
    from proximic_ring.wechat_native_keys import _send_key_direct
    quartz = sys.modules["Quartz"]
    quartz.kCGEventFlagMaskCommand, quartz.kCGEventFlagMaskShift = 256, 128
    quartz.kCGEventSourceUserData = 99
    quartz.CGPreflightPostEventAccess = lambda: True
    quartz.CGEventCreateKeyboardEvent = lambda _, code, down: {"code": code, "down": down}
    quartz.CGEventSetFlags = lambda event, flags: None
    quartz.CGEventSetIntegerValueField = lambda *args: None
    def post(pid, event):
        desktop.sent.append((pid, event["down"]))
        if not event["down"]:
            desktop.actual = None  # Notification is queued; cache still says Codex.
    quartz.CGEventPostToPid = post
    with pytest.raises(RuntimeError, match="输入目标已切换"):
        _send_key_direct("select_previous", 3, 123, "com.openai.codex", expected_pid=22)
    assert desktop.sent == [(22, True), (22, False)]
