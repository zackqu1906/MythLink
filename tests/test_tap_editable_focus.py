"""A dictation tap must verify the live editor before admitting microphone input."""
from types import SimpleNamespace

import pytest

from proximic_ring.desktop.app_shortcuts import MacAppShortcuts, ShortcutTarget
from proximic_ring.voice.ime import system_input_source
from test_app_gestures import route
from test_app_shortcuts import shortcut_capture, wechat_capture, wechat_titlebar
from test_inline_state import component
from test_ring_gestures import request as ring_tap


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


@pytest.mark.parametrize("scene,reply", [
    ("focused_editor", "ready"), ("focused_editor", "no_selection"), ("unfocused_editor", "unused"),
    ("titlebar", "ready"), ("titlebar", "no_selection"), ("titlebar", "malformed_selection"),
    ("titlebar", "missing_client"), ("titlebar", "ipc_timeout"), ("titlebar", "activation_timeout"),
    ("titlebar", "window_changed"), ("focused_button", "unused"), ("other_app", "unused"),
])
def test_single_tap_from_an_inactive_input_method(route, request, monkeypatch, scene, reply):
    from proximic_ring.platforms.macos import native_access, native_access_worker
    from proximic_ring.platforms.macos.mac_permissions import PermissionState

    controller, service, inline, _, _, _, _ = route
    editor_fixture = scene in {"focused_editor", "unfocused_editor"}
    backend, attrs, children, _ = request.getfixturevalue("wechat_capture" if editor_fixture else "wechat_titlebar")
    bundle = "test.other" if scene == "other_app" else "com.tencent.xinWeChat"
    front = (bundle, 42)
    if scene == "focused_editor":
        attrs[("window", "AXFocusedUIElement")] = "editor"
        attrs[("editor", "AXFocused")] = True
    elif scene == "focused_button":
        button = children["window"][0]
        attrs[("window", "AXFocusedUIElement")] = button
        attrs[(button, "AXFocused")] = True
    elif scene == "other_app":
        import AppKit
        app = SimpleNamespace(bundleIdentifier=lambda: bundle, processIdentifier=lambda: 42,
                              localizedName=lambda: "Other")
        monkeypatch.setattr(AppKit.NSWorkspace, "sharedWorkspace",
                            lambda: SimpleNamespace(frontmostApplication=lambda: app))

    assert controller.setGestureBinding("confirm", 0, "tap")
    monkeypatch.setattr(service, "foreground_has_binding", lambda _: False)
    monkeypatch.setattr(inline, "_foreground_identity", lambda: front)
    monkeypatch.delattr(inline, "capture_start_focus")  # Use the real method behind route's stub.
    inline._client_id = inline._utterance_id = ""
    inline._view.update(application=bundle, phase="idle", ready=False)
    observed, restored = [], []

    # Only replace the native pipe. Capture, serialization, and pinned-window checks run normally.
    dispatcher = native_access_worker.Dispatcher()
    dispatcher.shortcuts = backend
    monkeypatch.setattr(native_access_worker, "read_permission_state", lambda: PermissionState(True, False))
    def native_call(operation, **params):
        value = dispatcher.handle({"operation": operation, **params})["result"]
        observed.append((operation, value["input_context"] if operation == "capture" else value))
        return value
    monkeypatch.setattr(native_access, "native_access", lambda: SimpleNamespace(call=native_call, last_diagnostic={}))

    def select(pid, *, remember_source, on_borrow=None, **_):
        assert pid == front[1] and remember_source
        if on_borrow:
            on_borrow("com.apple.keylayout.ABC")
        observed.append("switch")
        if reply == "activation_timeout":
            raise TimeoutError  # Source changed before the native confirmation expired.
        return "com.apple.keylayout.ABC"
    monkeypatch.setattr(system_input_source, "activate_voice_input_source", select)

    selection = (7, 0)
    def focus_state(**_):
        if "switch" not in observed:
            observed.append("inactive")
            return {"ready": False, "client_id": ""}
        if "waiting" not in observed:
            observed.append("waiting")
            return None
        if "imk" not in observed:
            observed.append("imk")
        if reply == "ipc_timeout":
            raise TimeoutError
        if reply == "window_changed":
            attrs[("app", "AXFocusedWindow")] = "another-window"
        return {"epoch": inline._epoch, "application": bundle,
                "client_id": "" if reply == "missing_client" else "wechat-client", "utterance_id": "",
                "selection": {"no_selection": [-1, -1], "malformed_selection": [-2, 0]}.get(reply, selection),
                "ready": True}
    monkeypatch.setattr(inline, "_bridge", SimpleNamespace(epoch=inline._epoch, connected=True,
                                                          focus_state=focus_state))
    monkeypatch.setattr(inline._gesture_source_activation, "restore_if_unused",
                        lambda origin, original, ticket: restored.append((origin, original, ticket is not None)))

    accepted = ring_tap(controller, "tap", deliver=False)
    assert accepted is (reply == "ready")
    context = {"focused_editor": "text", "focused_button": "nontext"}.get(scene, "unknown")
    expected = ["inactive", ("capture", context)]
    if reply != "unused":
        expected.append("switch")
        if reply != "activation_timeout":
            expected.extend(["waiting", "imk"])
        if scene == "titlebar" and reply in {"ready", "window_changed"}:
            expected.append(("same_target", reply == "ready"))
    assert observed == expected
    assert restored == ([(front, "com.apple.keylayout.ABC", True)] if not accepted and reply != "unused" else [])
    if accepted:
        assert inline._pending_gesture_focus[0] == (inline._epoch, "wechat-client", "", *front, selection)
    else:
        assert inline._pending_gesture_focus is None
