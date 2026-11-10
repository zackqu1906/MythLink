"""Borrow/return/resume transactions without changing a real input source."""
import threading
import time

import pytest
from PySide6.QtCore import QCoreApplication

from proximic_ring.voice.ime.input_method_install import BUNDLE_ID
from test_inline_state import component, receive
from test_input_source_activation import FakeSource, wait_until


VOICE = BUNDLE_ID + ".dictation"
ORIGINAL = "com.apple.inputmethod.SCIM.ITABC"


class Source(FakeSource):
    def restore(self, origin, previous, expected):
        self.calls.append(("restore", origin, previous, expected))


def start(c, monkeypatch, *, enabled=True, previous=ORIGINAL):
    monkeypatch.setattr(c, "_foreground_identity", lambda: ("test.editor", 123))
    c._source_switch_factory = Source
    c.set_auto_return_input_source(enabled)
    assert c.begin(auto_select=True)
    source = c._source_activation
    source.event.emit(dict(event="selected", already_selected=previous == VOICE, previous_source=previous))
    receive(c, ready=True, phase="idle", utterance_id="", application="test.editor")
    receive(c, ready=True, phase="listening", application="test.editor")
    c.bind_session(7)


def settle(c, *, phase="dictated", revision=1, **fields):
    receive(c, ready=True, phase=phase, revision=revision, raw="今天开会。",
            has_composition=False, awaiting_readback=False, **fields)
    receive(c, "settled", phase=phase, revision=revision, text="今天开会。")
    QCoreApplication.processEvents()


def suspend(c):
    saved = c._source_return.saved
    assert c._source_return.stage == "suspending"
    receive(c, "input_suspended", token=saved["token"], success=True)
    source = c._source_return.helper
    assert source.calls == [("restore", ("test.editor", 123), ORIGINAL, VOICE)]
    receive(c, ready=False, phase="idle", client_id="", utterance_id="")
    source.event.emit(dict(event="restored", restored=True, reason="restored"))
    return saved


def request_resume(c):
    result = []
    worker = threading.Thread(target=lambda: result.append(c.resume_gesture_failure()))
    worker.start()
    wait_until(lambda: c._source_return.stage == "selecting")
    helper = c._source_return.helper
    assert helper.calls[0] == ("start", ("test.editor", 123))
    helper.event.emit(dict(event="selected", previous_source=ORIGINAL,
                           already_selected=False, focus_restored=True))
    token = c._source_return.saved["token"]
    receive(c, client_id="client-new", utterance_id="", ready=True, phase="idle",
            application="test.editor", request_id="resume:" + token)
    assert c._bridge.messages[-1]["type"] == "resume_input"
    return worker, result


def resumed(c, saved, *, success=True, error=""):
    state = {**saved["view"], "epoch": c._epoch, "client_id": "client-new",
             "utterance_id": saved["utterance_id"], "ready": True}
    receive(c, "input_resumed", token=saved["token"], success=success, error=error,
            client_id="client-new", utterance_id=saved["utterance_id"], state=state)


@pytest.mark.parametrize("enabled,previous", [(False, ORIGINAL), (True, VOICE), (True, "")])
def test_disabled_or_no_borrow_never_suspends_or_restores(component, monkeypatch, enabled, previous):
    c, _ = component
    start(c, monkeypatch, enabled=enabled, previous=previous)
    settle(c)
    assert c.ready and c.canUndo
    assert not any(m["type"] == "suspend_input" for m in c._bridge.messages)
    assert c._source_return.helper is None


def test_final_text_waits_for_native_settlement_before_return(component, monkeypatch):
    c, _ = component
    start(c, monkeypatch)
    c.finish()
    assert c._source_return.saved is None
    c.update("今天开会。", True, 7)
    QCoreApplication.processEvents()
    assert c._source_return.saved is None
    settle(c)
    assert c._bridge.messages[-1]["type"] == "suspend_input"
    assert c._source_return.helper is None  # Wait for native snapshot ACK first.
    saved = suspend(c)
    assert not c.ready and not c.active
    assert c._source_return.saved is saved


def test_round_trip_restores_sentence_for_polish_and_two_undos(component, monkeypatch):
    c, _ = component
    start(c, monkeypatch)
    utterance = c._utterance_id
    settle(c)
    for action, next_phase, revision in [("polish", "dictated", 2), ("cancel", "dictated", 3), ("cancel", "undone", 4)]:
        saved = suspend(c)
        worker, result = request_resume(c)
        assert not result
        resumed(c, saved)
        worker.join(1)
        assert result == [None]
        assert c._utterance_id == utterance and c._client_id == "client-new" and c._session_id == 7
        if action == "polish":
            c._view["can_polish"] = True
            assert c.polish()
        else:
            c.cancel()
        assert c._bridge.messages[-1]["type"] == action
        assert c._bridge.messages[-1]["utterance_id"] == utterance
        c.finish_resumed_gesture()
        QCoreApplication.processEvents()
        settle(c, phase=next_phase, revision=revision)
    suspend(c)


@pytest.mark.parametrize("handoff", ["returned", "returning", "awaiting_suspend_ack", "other_pid", "multi_off", "text_changed"])
def test_new_sentence_inherits_only_verified_native_history(component, monkeypatch, handoff):
    c, _ = component
    start(c, monkeypatch)
    old = c._utterance_id
    settle(c)
    token = c._source_return.saved["token"]
    if handoff == "returning":
        receive(c, "input_suspended", token=token, success=True)
    elif handoff != "awaiting_suspend_ack":
        suspend(c)
    if handoff == "other_pid":
        monkeypatch.setattr(c, "_foreground_identity", lambda: ("test.editor", 124))
    if handoff == "multi_off":
        c.configure(multi_undo_enabled=False)
    assert c.begin(auto_select=True)
    current = c._utterance_id
    assert old != current
    if handoff == "awaiting_suspend_ack":
        receive(c, "input_suspended", token=token, success=True)
        assert c._source_return.helper is None
    c._source_activation.event.emit(dict(event="selected", previous_source=ORIGINAL))
    receive(c, client_id="reactivated", ready=True, phase="idle", utterance_id="", application="test.editor")
    begin = c._bridge.messages[-1]
    assert begin["type"] == "begin" and begin["utterance_id"] == current
    eligible = handoff != "other_pid"
    assert begin.get("undo_history_token") == (token if eligible else None)
    assert any(m["type"] == "discard_suspended_input" and m["token"] == token
               for m in c._bridge.messages) == (not eligible)
    inherited = eligible and handoff != "text_changed"
    c.bind_session(8)  # The audio callback may arrive before the native BEGIN reply.
    receive(c, ready=True, phase="listening", undo_history_depth=int(inherited), application="test.editor")
    assert c._utterance_sessions == ({old: 7, current: 8} if inherited else {current: 8})
    assert c._source_return.begin_history is None


def test_cancelled_new_sentence_retires_its_history_token(component, monkeypatch):
    c, _ = component
    start(c, monkeypatch)
    settle(c)
    saved = suspend(c)
    assert c.begin(auto_select=True)
    c.cancel()
    assert {"type": "discard_suspended_input", "token": saved["token"]} in c._bridge.messages
    assert c._source_return.begin_history is None


def test_pending_polish_runs_before_auto_return(component, monkeypatch):
    c, _ = component
    start(c, monkeypatch)
    c.settled.connect(lambda *_: c.polish())
    settle(c, can_polish=True)
    assert c._bridge.messages[-1]["type"] == "polish"
    assert c._source_return.saved is None


@pytest.mark.parametrize("change", ["composition", "readback", "foreground", "stroke"])
def test_unsettled_or_changed_target_does_not_switch(component, monkeypatch, change):
    c, _ = component
    start(c, monkeypatch)
    receive(c, phase="dictated", ready=True, revision=1, has_composition=change == "composition",
            awaiting_readback=change == "readback")
    if change == "foreground":
        monkeypatch.setattr(c, "_foreground_identity", lambda: ("other.editor", 321))
    if change == "stroke":
        c.stroke_mode = True
    receive(c, "settled", phase="dictated", revision=1, text="今天开会。")
    QCoreApplication.processEvents()
    assert c._source_return.saved is None


def test_native_cannot_verify_snapshot_keeps_original_live_session(component, monkeypatch):
    c, _ = component
    start(c, monkeypatch)
    settle(c)
    token = c._source_return.saved["token"]
    receive(c, "input_suspended", token=token, success=False, reason="unreadable")
    assert c.ready and c.canUndo and c._source_return.helper is None


@pytest.mark.parametrize("native_error, reason", [
    ("已切换输入框，未恢复语音操作", "input_method_resume_field_changed"),
    ("本句文字或光标已变化，未恢复语音操作", "input_method_resume_text_changed"),
    ("输入恢复请求已过期", "input_method_resume_expired"),
    ("arbitrary editor contents", "input_method_resume_unverified"),
])
def test_rejected_resume_reports_only_this_requests_safe_reason(component, monkeypatch, native_error, reason):
    c, _ = component
    start(c, monkeypatch)
    settle(c)
    saved = suspend(c)
    worker, result = request_resume(c)
    receive(c, "input_resumed", token="other-token", success=False, error="输入恢复请求已过期")
    assert not result and c._source_return.stage == "resuming"
    resumed(c, saved, success=False, error=native_error)
    worker.join(1)
    assert result == [reason]
    assert not any(m["type"] in {"polish", "cancel"} for m in c._bridge.messages)
    assert c._source_return.saved is None
    assert c._source_return.helper.calls[0][0] == "restore"


@pytest.mark.parametrize("change", ["disable", "new_sentence", "disconnect", "timeout"])
def test_cancelled_suspend_cannot_switch_on_a_late_ack(component, monkeypatch, change):
    c, _ = component
    start(c, monkeypatch)
    settle(c)
    token = c._source_return.saved["token"]
    if change == "disable":
        c.set_auto_return_input_source(False)
    elif change == "new_sentence":
        assert c.begin(auto_select=True)
    elif change == "disconnect":
        receive(c, "disconnected")
    else:
        c._source_return._timer.timeout.emit()
    receive(c, "input_suspended", token=token, success=True)
    assert c._source_return.saved is None and c._source_return.helper is None


def test_timeout_and_late_resume_never_restore_old_sentence(component, monkeypatch):
    c, _ = component
    start(c, monkeypatch)
    settle(c)
    saved = suspend(c)
    worker, result = request_resume(c)
    c._source_return._timer.timeout.emit()
    resumed(c, saved)
    worker.join(1)
    assert result == ["input_method_resume_timeout"] and not c._utterance_id
    assert c._source_return.helper.calls[0][0] == "restore"


@pytest.mark.parametrize("dropped", [False, True])
def test_noop_or_dropped_resumed_gesture_returns_again(component, monkeypatch, dropped):
    c, _ = component
    start(c, monkeypatch)
    settle(c, polished=True)
    saved = suspend(c)
    worker, result = request_resume(c)
    resumed(c, saved)
    worker.join(1)
    assert result == [None]
    assert c._source_return.saved is None
    if dropped:
        c._source_return._gesture_timer.timeout.emit()
    else:
        c.finish_resumed_gesture()
    wait_until(lambda: c._source_return.saved is not None)
    suspend(c)


def test_final_undo_can_return_without_a_resumable_empty_sentence(component, monkeypatch):
    c, _ = component
    start(c, monkeypatch)
    settle(c, phase="undone")
    saved = c._source_return.saved
    receive(c, "input_suspended", token=saved["token"], success=True, restorable=False)
    assert c._source_return.saved is None and c._source_return.stage == "returning_final"
    c._source_return.helper.event.emit(dict(event="restored", restored=True))
    assert not c._source_return.original


def test_a_new_input_client_cancels_pending_return(component, monkeypatch):
    c, _ = component
    start(c, monkeypatch)
    settle(c)
    token = c._source_return.saved["token"]
    receive(c, "input_suspended", token=token, success=True)
    helper = c._source_return.helper
    receive(c, ready=True, phase="idle", client_id="different-field", utterance_id="",
            application="test.editor")
    helper.event.emit(dict(event="restored", restored=True))
    assert helper.calls[-1] == ("cancel",)
    assert c._source_return.saved is None and c._client_id == "different-field"


def test_sent_sentence_returns_source_without_retaining_a_sent_message(component, monkeypatch):
    c, _ = component
    start(c, monkeypatch)
    receive(c, phase="dictated", ready=True, has_composition=False, awaiting_readback=False)
    c.release_completed_shortcut(sent=True)
    assert c._source_return.saved is None
    source = c._source_return.helper
    assert source.calls[0] == ("restore", ("test.editor", 123), ORIGINAL, VOICE)
    source.event.emit(dict(event="restored", restored=True))
    assert not c._source_return.original and not c._utterance_id


def test_preselected_gesture_remembers_source_before_begin_and_auto_start_reselects(component, monkeypatch):
    c, _ = component
    monkeypatch.setattr(c, "_foreground_identity", lambda: ("test.editor", 123))
    c._source_switch_factory = Source
    c.set_auto_return_input_source(True)
    target = (c._epoch, c._client_id, "", "test.editor", 123, (22, 0))
    import time
    c._source_return.remember_probe(target, ORIGINAL)
    c.pin_start_focus(target, time.monotonic())
    assert c.begin(auto_select=True)
    assert c._source_return.original == ORIGINAL and c._source_activation is None
    c.reset()
    assert c.begin(auto_select=False)
    assert c._source_activation.calls == [("start", ("test.editor", 123))]


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("original", [ORIGINAL, VOICE])
@pytest.mark.parametrize("phase", ["dictated", "edited"])
def test_imk_probe_to_begin_and_final_return_preserves_source_across_sentences(
        component, monkeypatch, enabled, original, phase):
    from proximic_ring.desktop.app_shortcuts import MacAppShortcuts
    from proximic_ring.voice.ime import system_input_source

    c, _ = component
    origin = ("test.editor", 123)
    state = dict(source=original)
    switches = []
    monkeypatch.setattr(c, "_foreground_identity", lambda: origin)
    monkeypatch.setattr(MacAppShortcuts, "capture", lambda *_, **__: pytest.fail("AX precheck"))
    c._source_switch_factory = Source
    c.set_auto_return_input_source(enabled)
    bridge = c._bridge
    bridge.connected, bridge.epoch = True, c._epoch
    bridge.focus_state = lambda **_: (
        dict(epoch=c._epoch, application=origin[0], client_id="probe-client", utterance_id="",
             ready=True, selection=[22, 0]) if state["source"] == VOICE else dict(ready=False, client_id=""))

    class Sources:
        def sources(self, **_): return {ORIGINAL, VOICE}
        def current(self, **_): return state["source"]
        def foreground(self): return origin
        def shortcut(self): return (49, 786432)
        def advance(self, _, __, *, before_key_down):
            before_key_down()
            switches.append(state["source"])
            state["source"] = VOICE
    monkeypatch.setattr(system_input_source, "SystemInputSources", Sources)

    returned = enabled and original == ORIGINAL
    history_token = None
    for sentence in range(2 if returned else 1):
        proof = c.capture_start_focus(require_editable=True)
        assert proof.status == "text"
        c.pin_start_focus(proof.target, time.monotonic())
        assert c.begin(auto_select=True)
        assert c._source_activation is None  # The BEGIN path must not reselect the borrowed source.
        assert c._source_return.original == (ORIGINAL if returned else "")
        receive(c, client_id="probe-client", ready=True, phase="idle", utterance_id="",
                application=origin[0], selection=[22, 0], request_id=c._startup_focus_request)
        begin = bridge.messages[-1]
        assert begin["type"] == "begin" and begin.get("undo_history_token") == history_token
        receive(c, ready=True, phase="listening", application=origin[0], selection=[22, 0],
                undo_history_depth=sentence)
        c.bind_session(sentence + 1)
        settle(c, phase=phase, undo_history_depth=sentence + 1)
        if returned:
            history_token = c._source_return.saved["token"]
            suspend(c)
            state["source"] = ORIGINAL
            assert c._source_return.stage == "suspended"
        else:
            assert c._source_return.saved is None and c._source_return.helper is None
            assert state["source"] == VOICE
    assert switches == ([ORIGINAL] * (2 if returned else 1) if original == ORIGINAL else [])


@pytest.mark.parametrize("failure", ["cancel", "timeout"])
def test_cancelled_start_returns_a_source_already_borrowed(component, monkeypatch, failure):
    c, _ = component
    monkeypatch.setattr(c, "_foreground_identity", lambda: ("test.editor", 123))
    c._source_switch_factory = Source
    c.set_auto_return_input_source(True)
    assert c.begin(auto_select=True)
    c._source_activation.event.emit(dict(event="selected", previous_source=ORIGINAL,
                                         already_selected=False, focus_restored=True))
    if failure == "cancel":
        c.cancel()
    else:
        c._reply_timed_out()
    assert c._source_return.helper.calls[0] == ("restore", ("test.editor", 123), ORIGINAL, VOICE)
    assert not any(m["type"] == "update" for m in c._bridge.messages)


def test_retry_snapshot_survives_source_return_and_mode_switch(component, monkeypatch):
    c, _ = component
    start(c, monkeypatch)
    source = c._utterance_id
    request = object()
    c._edit_replays[source] = request
    settle(c, edit_retry_version=1, can_retry_edit=True, retry_source_utterance_id=source)
    saved = suspend(c)
    assert not c.empty_redo_after_return()
    c.configure(history_action_mode="undo")
    assert c.empty_redo_after_return()
    c.configure(history_action_mode="retry")
    assert not c.empty_redo_after_return()
    worker, result = request_resume(c)
    resumed(c, saved)
    worker.join(1)
    assert result == [None] and c.canRetryEdit and c._edit_replays[source] is request
    c.retry_edit()
    assert c._bridge.messages[-1]["type"] == "retry_edit"
