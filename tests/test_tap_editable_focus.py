"""Tap borrows a source, then requires fresh IMK proof without an AX editor gate."""
from types import SimpleNamespace

import pytest

from proximic_ring.desktop.app_shortcuts import MacAppShortcuts
from proximic_ring.voice.ime import system_input_source
from test_app_gestures import route
from test_input_source_return import ORIGINAL, VOICE
from test_ring_gestures import request as ring_tap


@pytest.fixture
def tap_probe(route, monkeypatch):
    controller, service, inline, backend, _, _, _ = route
    assert controller.setGestureBinding("confirm", 0, "tap")
    state = dict(front=("com.tencent.xinWeChat", 42), source=ORIGINAL, outcome="ready",
                 activation_attempted=False, already_ready=False, waited=False)
    actions, restores = [], []
    monkeypatch.setattr(service, "foreground_has_binding", lambda _: False)
    monkeypatch.setattr(inline, "_foreground_identity", lambda: state["front"])
    monkeypatch.delattr(inline, "capture_start_focus")
    inline._client_id = inline._utterance_id = ""
    inline._view.update(phase="idle", ready=False)

    def forbidden(*_, **__):
        pytest.fail("Starting dictation must not query AX editor/window structure")
    monkeypatch.setattr(MacAppShortcuts, "capture", forbidden)
    monkeypatch.setattr(backend, "capture", forbidden)

    class Sources:
        def sources(self, **_):
            state["activation_attempted"] = True
            return {ORIGINAL, VOICE}
        def foreground(self): return state["front"]
        def current(self, **_): return state["source"]
        def shortcut(self): return (49, 786432)
        def advance(self, shortcut, origin, *, before_key_down):
            assert origin == state["front"]
            before_key_down()
            actions.append("switch")
            state["source"] = VOICE
            if state["outcome"] == "activation_timeout":
                raise TimeoutError("Source changed, but selection receipt expired")
    monkeypatch.setattr(system_input_source, "SystemInputSources", Sources)

    def focus_state(**_):
        if state["outcome"] == "initial_timeout":
            return None
        if not state["already_ready"] and not state["activation_attempted"]:
            return {"ready": False, "client_id": "", "application": ""}
        if not state["already_ready"] and not state["waited"]:
            state["waited"] = True
            return None
        if state["outcome"] == "ipc_timeout":
            raise TimeoutError("No usable client reply")
        reply = dict(epoch=inline._epoch, application=state["front"][0],
                     client_id="fresh-client", utterance_id="", ready=True, selection=[7, 0])
        if state["outcome"] == "no_selection": reply["selection"] = [-1, -1]
        if state["outcome"] == "malformed_selection": reply["selection"] = [-2, 0]
        if state["outcome"] == "not_ready": reply["ready"] = False
        if state["outcome"] == "missing_client": reply["client_id"] = ""
        if state["outcome"] == "app_changed": state["front"] = ("other.app", 43)
        if state["outcome"] == "pid_changed": state["front"] = (state["front"][0], 43)
        if state["outcome"] == "epoch_changed": inline._bridge.epoch = "new-connection"
        return reply
    monkeypatch.setattr(inline, "_bridge", SimpleNamespace(epoch=inline._epoch, connected=True,
                        focus_state=focus_state, close=lambda: None, send=lambda _: True))
    monkeypatch.setattr(inline._gesture_source_activation, "restore_if_unused",
                        lambda origin, previous, ticket: restores.append((origin, previous, ticket is not None)))
    return controller, inline, state, actions, restores


@pytest.mark.parametrize("bundle", ["com.tencent.xinWeChat", "com.openai.codex", "test.editor"])
@pytest.mark.parametrize("auto_return", [False, True])
def test_tap_uses_live_imk_for_every_app_without_ax(tap_probe, bundle, auto_return):
    controller, inline, state, actions, restores = tap_probe
    state["front"] = (bundle, 42)
    inline.set_auto_return_input_source(auto_return)
    assert ring_tap(controller, "tap", deliver=False)
    target = inline._pending_gesture_focus[0]
    assert target == (inline._epoch, "fresh-client", "", bundle, 42, (7, 0))
    assert actions == ["switch"] and not restores
    inline._source_return.begin(state["front"], target)
    assert inline._source_return.original == (ORIGINAL if auto_return else "")


@pytest.mark.parametrize("auto_return", [False, True])
@pytest.mark.parametrize("outcome", ["no_selection", "malformed_selection", "not_ready", "ipc_timeout",
                                     "activation_timeout", "app_changed", "pid_changed", "epoch_changed",
                                     "missing_client"])
def test_failed_tap_returns_its_borrow_even_when_auto_return_is_off(tap_probe, auto_return, outcome):
    controller, inline, state, actions, restores = tap_probe
    origin = state["front"]
    inline.set_auto_return_input_source(auto_return)
    state["outcome"] = outcome
    assert not ring_tap(controller, "tap", deliver=False)
    assert actions == ["switch"]
    assert restores == [(origin, ORIGINAL, True)]
    assert inline._pending_gesture_focus is None and inline._source_return.probe is None


@pytest.mark.parametrize("already_ready", [False, True])
@pytest.mark.parametrize("outcome", ["ready", "no_selection"])
def test_already_selected_mythlink_does_not_cycle_or_invent_an_original(tap_probe, already_ready, outcome):
    controller, inline, state, actions, restores = tap_probe
    state.update(source=VOICE, already_ready=already_ready, outcome=outcome)
    inline.set_auto_return_input_source(True)
    assert ring_tap(controller, "tap", deliver=False) is (outcome == "ready")
    assert not actions and not restores and inline._source_return.probe is None


def test_no_live_ipc_reply_never_uses_cached_readiness_or_changes_source(tap_probe):
    controller, inline, state, actions, restores = tap_probe
    state["outcome"] = "initial_timeout"
    inline._view.update(ready=True, application=state["front"][0])
    assert not ring_tap(controller, "tap", deliver=False)
    assert not actions and not restores and not state["activation_attempted"]


@pytest.mark.parametrize("source", ["", "unavailable.keyboard"])
def test_unknown_original_source_never_borrows_or_guesses_a_keyboard(tap_probe, source):
    controller, inline, state, actions, restores = tap_probe
    state["source"] = source
    assert not ring_tap(controller, "tap", deliver=False)
    assert not actions and not restores and inline._pending_gesture_focus is None
