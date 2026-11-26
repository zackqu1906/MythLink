"""Exercise Windows input routing without changing the real desktop's keyboard or clipboard."""
import sys
from types import SimpleNamespace

import pytest

from proximic_ring.desktop.windows_applications import candidate
from proximic_ring.desktop.windows_shortcuts import WindowsAppShortcuts
from proximic_ring.scenes.configuration import new_application
from proximic_ring.voice.ime.focus_probe import probe_input_focus


def test_windows_application_defaults_use_stable_executable_identity():
    edge = candidate(r"C:\Program Files\Edge\msedge.exe", "Microsoft Edge")
    assert edge["value"] == candidate(edge["path"].upper())["value"]
    assert edge["value"] != candidate(r"D:\Portable\msedge.exe")["value"]
    assert candidate("shortcut.cmd") is None
    app = new_application(edge)
    assert app["bindings"]["circle-counterclockwise"]["shortcut"] == "Ctrl+Shift+Tab"
    assert app["bindings"]["circle-clockwise"]["shortcut"] == "Ctrl+Tab"
    snip = candidate(r"shell:AppsFolder\Microsoft.ScreenSketch_8wekyb3d8bbwe!App", "截图工具")
    assert new_application(snip)["bindings"]["circle-counterclockwise"]["shortcut"] == "Ctrl+N"


def test_windows_system_snip_shortcut_is_fenced_by_the_original_window(monkeypatch):
    from proximic_ring.desktop import windows_shortcuts as module
    posted = []
    def send(count, events, size):
        posted.extend((events[i].ki.wVk, events[i].ki.dwFlags) for i in range(count))
        return count
    user = SimpleNamespace(GetAsyncKeyState=lambda _: 0, SendInput=send)
    monkeypatch.setattr(module, "api", lambda: (user, None, None))
    backend = WindowsAppShortcuts()
    monkeypatch.setattr(backend, "same_target", lambda *args, **kwargs: False)
    with pytest.raises(RuntimeError, match="窗口"):
        backend.post(object(), "Win+Shift+S")
    assert not posted
    monkeypatch.setattr(backend, "same_target", lambda *args, **kwargs: True)
    backend.post(object(), "Win+Shift+S")
    assert posted == [(91, 1), (16, 0), (83, 0), (83, 2), (16, 2), (91, 3)]


@pytest.mark.parametrize("switch_foreground", [False, True])
def test_windows_input_selection_replaces_stale_pipe_and_rechecks_focus(switch_foreground):
    front = [("win32:34", 34)]
    bridge = SimpleNamespace(connected=True, epoch="previous")
    def focus(**_):
        if bridge.epoch == "current":
            return dict(epoch="current", application="win32:34", client_id="text", ready=True, selection=[1, 0])
    bridge.focus_state = focus
    selected = []
    def select(pid, **_):
        selected.append(pid)
        bridge.epoch = "current"
        if switch_foreground:
            front[0] = ("win32:35", 35)
    result = probe_input_focus(bridge, lambda: front[0], select, reconnect_on_selection=True)
    assert selected == [34]
    if switch_foreground:
        assert result.status == "unavailable" and result.target is None
    else:
        assert result.status == "text" and result.target[:2] == ("current", "text")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows TSF startup")
def test_pinch_waits_for_the_windows_editor_to_grant_text_access():
    origin = ("win32:34", 34)
    bridge = SimpleNamespace(connected=True, epoch="current")
    states = iter([False, True])
    def focus(**_):
        ready = next(states)
        return dict(epoch="current", application=origin[0], client_id="text",
                    ready=ready, selection=[0, 0] if ready else [-1, -1])
    bridge.focus_state = focus
    result = probe_input_focus(bridge, lambda: origin, lambda *_: pytest.fail("already selected"),
                               reconnect_on_selection=True)
    assert result.status == "text" and result.target[1] == "text"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows TSF startup")
def test_external_strokes_wait_for_the_selected_windows_text_client(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from proximic_ring.ui.voice.inline_controller import InlineInputController
    from test_inline_ui import FakeBridge
    from test_input_source_activation import FakeSource
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("MYTHLINK_WINDOWS_TSF", "1")
    controller = InlineInputController(enabled=True, bridge_factory=FakeBridge, source_switch_factory=FakeSource)
    origin = ("win32:34", 34)
    monkeypatch.setattr(controller, "_foreground_identity", lambda: origin)
    ready, failed = [], []
    try:
        controller.prepare_input_method("stroke", origin, ready.append, failed.append)
        source = controller._source_activation
        assert source.calls == [("start", origin)]
        controller._accept({"type": "connected", "epoch": "new"})
        source.event.emit({"event": "selected"})
        app.processEvents()
        assert not ready and any(m["type"] == "ping" for m in controller._bridge.messages)
        state = dict(type="state", epoch="new", client_id="editor", utterance_id="", phase="idle", ready=True)
        controller._accept(dict(state, application="win32:35"))
        assert not ready
        controller._accept(dict(state, application=origin[0]))
        assert ready and not failed
    finally:
        controller.close()
