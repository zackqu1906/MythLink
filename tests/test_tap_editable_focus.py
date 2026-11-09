"""An idle dictation tap may borrow a keyboard only for a verified editor."""
from types import SimpleNamespace

import pytest

from proximic_ring.desktop.app_shortcuts import MacAppShortcuts, ShortcutTarget
from proximic_ring.voice.ime import system_input_source
from test_app_gestures import route
from test_app_shortcuts import shortcut_capture, wechat_capture
from test_inline_state import component
from test_ring_gestures import request


FRONT = ("test.editor", 42)


@pytest.mark.parametrize("context,expected", [
    ("text", "text"), ("nontext", "outside"), ("unknown", "unavailable"),
    ("window_changed", "unavailable"), ("app_changed", "unavailable"), ("blocked", "unavailable"),
    ("no_target", "unavailable"), ("lost_after_selection", "nontext"), ("already_ready", "text"),
])
def test_idle_tap_checks_before_switch_and_returns_unused_source(component, monkeypatch, context, expected):
    inline, _ = component
    idle = {"ready": False, "client_id": "", "application": ""}
    ready = dict(ready=True, client_id="current", epoch=inline._epoch, utterance_id="",
                 selection=[2, 0], application=FRONT[0])
    state = dict(front=FRONT, reply=ready if context == "already_ready" else idle)
    monkeypatch.setattr(inline, "_foreground_identity", lambda: state["front"])
    inline._bridge = SimpleNamespace(epoch=inline._epoch, connected=True,
                                     focus_state=lambda **_: state["reply"], close=lambda: None,
                                     send=lambda _: True)
    checks, switches, restores = [], [], []
    def passive(_, origin):
        checks.append(origin)
        if context == "app_changed":
            state["front"] = ("other.app", 43)
        if context == "no_target":
            return None
        return ShortcutTarget(FRONT[0], 43 if context == "window_changed" else 42, "",
                              input_context=context if context in {"nontext", "unknown"} else "text",
                              blocked=context == "blocked")
    def select(pid, *, remember_source, **_):
        assert remember_source and pid == 42
        switches.append(pid)
        state["reply"] = ({**ready, "ready": False, "selection": [-1, -1]}
                          if context == "lost_after_selection" else ready)
        return "original.keyboard"
    monkeypatch.setattr(MacAppShortcuts, "passive_focus", passive)
    monkeypatch.setattr(system_input_source, "activate_voice_input_source", select)
    monkeypatch.setattr(inline._gesture_source_activation, "restore_if_unused",
                        lambda front, previous, ticket: restores.append((front, previous)))
    assert inline.capture_start_focus(require_editable=True).status == expected
    assert checks == ([] if context == "already_ready" else [FRONT])
    assert switches == ([42] if context in {"text", "lost_after_selection"} else [])
    assert restores == ([(FRONT, "original.keyboard")] if context == "lost_after_selection" else [])


@pytest.mark.parametrize("focused,live_client", [(True, True), (True, False), (False, False)])
def test_wechat_single_tap_from_an_inactive_input_method(route, wechat_capture, monkeypatch,
                                                          focused, live_client):
    controller, service, inline, _, _, _, _ = route
    local_shortcuts, attributes, _, _ = wechat_capture
    front = ("com.tencent.xinWeChat", 42)
    assert controller.setGestureBinding("confirm", 0, "tap")
    monkeypatch.setattr(service, "foreground_has_binding", lambda _: False)
    monkeypatch.setattr(inline, "_foreground_identity", lambda: front)
    monkeypatch.delattr(inline, "capture_start_focus")  # Use the real method behind route's stub.
    inline._client_id = inline._utterance_id = ""
    inline._view.update(application=front[0], phase="idle", ready=False)
    if focused:
        attributes[("window", "AXFocusedUIElement")] = "editor"
        attributes[("editor", "AXFocused")] = True

    observed = []
    def capture(_, **options):
        # Stand in for the worker pipe. Both native AX classification and passive_focus run.
        target = local_shortcuts.capture(**options)
        observed.append(("ax", target.input_context))
        return target
    monkeypatch.setattr(MacAppShortcuts, "capture", capture)

    selection = (7, 0)
    def focus_state(**_):
        if "switch" not in observed:
            observed.append("inactive")
            return {"ready": False, "client_id": ""}
        if "waiting" not in observed:
            observed.append("waiting")
            return None  # No IMK response yet after selecting the source.
        observed.append("imk")
        return {"epoch": inline._epoch, "application": front[0], "client_id": "wechat-client",
                "utterance_id": "", "selection": selection if live_client else [-1, -1],
                "ready": live_client}
    monkeypatch.setattr(inline, "_bridge", SimpleNamespace(epoch=inline._epoch, connected=True,
                                                          focus_state=focus_state))
    def select(pid, **_):
        assert pid == front[1]
        observed.append("switch")
        return "com.apple.keylayout.ABC"
    monkeypatch.setattr(system_input_source, "activate_voice_input_source", select)
    monkeypatch.setattr(inline._gesture_source_activation, "restore_if_unused",
                        lambda *args: observed.append("restore"))

    assert request(controller, "tap", deliver=False) is live_client
    if not focused:
        assert observed == ["inactive", ("ax", "unknown")]
    else:
        expected = ["inactive", ("ax", "text"), "switch", "waiting", "imk"]
        assert observed == (expected if live_client else [*expected, "restore"])
    if live_client:
        assert inline._pending_gesture_focus[0] == (inline._epoch, "wechat-client", "", *front, selection)
    else:
        assert inline._pending_gesture_focus is None
