"""Borrow/return/resume transactions without changing a real input source."""
import threading

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
