"""WeChat's live IMK client remains usable when AXFocusedUIElement is NoValue."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication

from test_app_gestures import route
from test_ring_gestures import request


@pytest.fixture
def native_route(route, monkeypatch):
    c, service, inline, backend, sent, messages, emit = route
    bundle = "com.tencent.xinWeChat"
    inline._view.update(application=bundle, phase="dictated", raw="测试文字")
    backend.target = replace(backend.target, bundle=bundle, profile="wechat",
                             focus=None, role="", input_context="unknown",
                             diagnostic={"focus_reason": "focus_missing"})
    state = dict(front=(bundle, 42), proof=("e", "c", "u", bundle))
    def focus_state(**_):
        proof = state['proof']
        if proof is None:
            return None
        return dict(zip(('epoch', 'client_id', 'utterance_id', 'application'), proof),
                    ready=inline.ready, selection=[0, 0])
    bridge = SimpleNamespace(epoch='e', connected=True, focus_state=focus_state,
                             gesture_target=lambda: state["proof"], close=lambda: None)
    monkeypatch.setattr(inline, "_bridge", bridge)
    for name in ('capture_start_focus', 'capture_gesture_target', 'same_input_focus', 'same_gesture_target'):
        monkeypatch.setattr(inline, name, getattr(type(inline), name).__get__(inline))
    monkeypatch.setattr(inline, "_foreground_identity", lambda: state["front"])
    return (*route, state)


@pytest.mark.parametrize("gesture,kind", [("swipe-left", "cancel"), ("swipe-down", "polish"), ("swipe-right", None)])
def test_input_actions_use_live_imk_without_ax_capture(native_route, monkeypatch, gesture, kind):
    c, service, inline, backend, sent, messages, _, _ = native_route
    monkeypatch.setattr(backend, "capture", lambda **kw: pytest.fail("IME action must not require AX focus"))
    request(c, gesture)
    assert [m[0] for m in messages] == ([kind] if kind else [])
    assert not sent and not service.notice


@pytest.mark.parametrize("gesture", ["swipe-left", "swipe-down", "swipe-up"])
@pytest.mark.parametrize("change", ["application", "pid", "client", "utterance", "epoch", "not_ready", "disconnect"])
def test_delayed_native_gesture_cannot_follow_focus_or_connection(native_route, gesture, change):
    c, service, inline, backend, sent, messages, _, state = native_route
    request(c, gesture, deliver=False)
    if change == "application": state["front"] = ("other.app", 51)
    if change == "pid": state["front"] = (state["front"][0], 43)
    if change == "client": inline._client_id = "new-field"
    if change == "utterance": inline._utterance_id = "new-sentence"
    if change == "epoch": inline._epoch = "new-connection"
    if change == "not_ready": inline._view["ready"] = False
    if change == "disconnect": c._disconnect_event.set()
    QCoreApplication.processEvents()
    assert not sent and not messages


@pytest.mark.parametrize("proof", [None, ("e", "other", "u", "com.tencent.xinWeChat"),
                                  ("old", "c", "u", "com.tencent.xinWeChat")])
def test_cached_ime_readiness_never_substitutes_for_live_response(native_route, proof):
    c, _, inline, _, sent, messages, _, state = native_route
    state["proof"] = proof
    assert inline.capture_gesture_target() is None
    request(c, "swipe-left")
    assert not messages and not sent


def test_focus_change_while_waiting_for_native_reply_discards_proof(native_route, monkeypatch):
    _, _, inline, _, _, _, _, state = native_route
    def reply():
        state["front"] = ("other.application", 99)
        return state["proof"]
    monkeypatch.setattr(inline._bridge, "gesture_target", reply)
    assert inline.capture_gesture_target() is None


def test_enter_uses_native_window_and_second_live_client_check(native_route, monkeypatch):
    c, service, inline, backend, sent, messages, _, state = native_route
    calls = []
    original = backend.capture
    def capture(**kw):
        calls.append(kw)
        return original(**kw)
    monkeypatch.setattr(backend, "capture", capture)
    request(c, "swipe-up")
    assert calls == [dict(plain_enter=True, focus_sensitive=False)]
    assert sent == [("wechat", "Return")]
    assert messages == [("end_input", {"outcome": "sent"})]


def test_enter_rejects_native_client_changed_before_key_dispatch(native_route):
    c, service, inline, _, sent, messages, _, state = native_route
    request(c, "swipe-up", deliver=False)
    # Simulate a native client transition whose Qt notification is still queued.
    state["proof"] = ("e", "new-field", "u", "com.tencent.xinWeChat")
    QCoreApplication.processEvents()
    assert not sent and not messages
    assert 'input_method_focus_changed' in c._diagnostic_log.path.read_text()


@pytest.mark.parametrize("gesture,expected", [("swipe-down", "polish"), ("swipe-up", "Return")])
def test_native_focus_finish_then_action_keeps_same_sentence(native_route, gesture, expected):
    c, service, inline, _, sent, messages, _, _ = native_route
    inline._view.update(phase="listening", has_composition=True)
    request(c, gesture)
    assert messages == [("finish", {})] and not sent
    inline._view.update(phase="dictated", has_composition=False)
    inline.settled.emit("dictated", "测试文字")
    if expected == "Return": assert sent == [("wechat", "Return")]
    else: assert messages[-1] == ("polish", {"restore_raw_on_undo": False})
    assert service._pending is None
