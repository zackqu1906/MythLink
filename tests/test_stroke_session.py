"""Gesture/session regression tests with fake Ring, input source and editor."""
from types import SimpleNamespace
import time

import pytest
from PySide6.QtTest import QTest

from test_inline_ui import inline_ui
from test_input_source_activation import FakeSource
from test_stroke_integration import idle
from proximic_ring.stroke_display import normalized_trace, standard_stroke, STANDARD_STROKES


def until(predicate):
    deadline = time.monotonic() + 2
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert predicate()


@pytest.fixture
def stroke(inline_ui, monkeypatch):
    c, bridge, root, tp, source = idle(inline_ui)
    session = tp.strokeSession
    c._inline_input._source_switch_factory = FakeSource
    monkeypatch.setattr(c._inline_input, "_foreground_identity", lambda: ("test.editor", 123))
    QTest.qWait(20)  # Retire pre-test IME lifecycle messages before first gesture.
    tp.start()
    until(lambda: bool(source.starts))
    source.future.set_result(None)
    until(lambda: tp.state == "running")
    bridge.messages.clear()
    yield c, bridge, tp, source, session
    tp.close()
    QTest.qWait(30)


def wake(stroke, context=""):
    c, bridge, tp, source, session = stroke
    tp._run.output.options["on_double_click"]()
    until(lambda: session.phase == "source")
    helper = session._source
    helper.event.emit(dict(event="selected", already_selected=True))
    message = dict(type="state", epoch=c._inline_input._epoch, client_id="stroke-editor", utterance_id="",
                   ready=True, phase="idle", application="test.editor", request_id="stroke:" + session._token)
    c._inline_input._accept(message)
    until(lambda: session.phase == "native")
    request = next(m for m in reversed(bridge.messages) if m["type"] == "stroke_begin")
    c._inline_input._accept(dict(type="stroke_ready", epoch=c._inline_input._epoch,
        client_id=request["client_id"], stroke_session=request["stroke_session"], success=True, context=context))
    until(lambda: session.phase == "running")
    return helper


def test_external_predictions_clear_on_caret_takeover_and_do_not_leak_to_next_session(stroke):
    c, bridge, tp, _, session = stroke
    wake(stroke, context="你好")
    assert tp.predictionMode and tp.strokeCandidates
    tp.setStrokeContext("主界面")  # A hidden trial editor cannot replace external context.
    assert tp._stroke_context == "你好"
    c._inline_input._accept(dict(type="stroke_ink_cleared", epoch=c._inline_input._epoch,
        client_id=c._inline_input.stroke_target[1], stroke_session=session._token))
    until(lambda: not tp.predictionMode and not tp.strokeCandidates)
    assert not tp._stroke_context and session.phase == "running"
    session.cancel()
    until(lambda: tp._run.output_ready)
    wake(stroke)
    assert not tp.predictionMode and not tp.strokeCandidates and not tp._stroke_context


def test_external_unreadable_context_uses_only_acknowledged_characters(stroke):
    c, bridge, tp, _, session = stroke
    wake(stroke)
    tp.addStroke("h")
    char = tp.strokeCandidates[0]
    for success in [False, True]:
        tp.selectCandidate(0)
        until(lambda: bool(tp._pending_stroke_commit))
        request = next(m for m in reversed(bridge.messages) if m["type"] == "stroke_commit")
        c._inline_input._accept(dict(type="stroke_result", epoch=c._inline_input._epoch,
            client_id=request["client_id"], request_id=request["request_id"], success=success))
        assert tp._stroke_context == (char if success else "")
        assert tp.predictionMode == success
    session.cancel()
    assert not tp._stroke_context and not tp.predictionMode


def test_double_click_enters_without_any_voice_commands_and_exits_without_source_restore(stroke):
    c, bridge, tp, source, session = stroke
    helper = wake(stroke)
    assert c.recognitionEnabled and not c._recognition_event.is_set()
    assert tp.active and tp.blocksVoice
    assert not {"begin", "finish", "finish_dictation", "cancel", "update"} & {m["type"] for m in bridge.messages}
    assert helper.calls == [("start", ("test.editor", 123)), ("cancel",)]
    tp.addStroke("h")
    assert tp._code
    tp._run.output.options["on_double_click"]()
    until(lambda: session.phase == "idle" and not tp.busy)
    assert not tp._code and not tp.strokeCandidates
    assert c.recognitionEnabled and c._recognition_event.is_set()
    assert source.stops == 0 and len(source.starts) == 1 and tp.inputMode == "pointer" and tp.active
    assert any(m["type"] == "stroke_end" for m in bridge.messages)
    assert not any(m["type"] == "stroke_commit" for m in bridge.messages)
    assert helper.calls == [("start", ("test.editor", 123)), ("cancel",)]


def test_confirm_keeps_stroke_mode_and_shares_standard_geometry(stroke):
    c, bridge, tp, _, session = stroke
    wake(stroke)
    seen = []
    tp.strokeTraceChanged.connect(lambda p, f: seen.append((p, f)))
    tp.addStroke("z")
    assert seen[-1] == (standard_stroke("z"), True)
    trace = next(m for m in reversed(bridge.messages) if m["type"] == "stroke_trace")
    assert trace["points"] == seen[-1][0] and trace["finished"] is True
    tp.selectCandidate(0)
    until(lambda: bool(tp._pending_stroke_commit))
    request = next(m for m in reversed(bridge.messages) if m["type"] == "stroke_commit")
    c._inline_input._accept(dict(type="stroke_result", epoch=c._inline_input._epoch,
        client_id=request["client_id"], request_id=request["request_id"], success=True))
    assert session.phase == "running" and tp.active and tp._code == "" and tp.blocksVoice


def test_firmware_tap_still_confirms_and_touchpad_single_does_not(stroke):
    c, bridge, tp, _, session = stroke
    wake(stroke)
    tp.addStroke("h")
    tp._run.output.options["on_tap"]()
    QTest.qWait(20)
    assert not tp._pending_stroke_commit
    assert not c._ring_gestures.filter(SimpleNamespace(name="tap"), False, c._disconnect_event)
    until(lambda: bool(tp._pending_stroke_commit))
    assert any(m["type"] == "stroke_commit" for m in bridge.messages)
    assert session.phase == "running"


def test_double_click_owns_queued_transition_before_a_firmware_tap(stroke):
    c, bridge, tp, _, session = stroke
    wake(stroke)
    tp.addStroke("h")
    tp._run.output.options["on_double_click"]()
    assert tp._run.switch_pending
    assert tp.consume_gesture(SimpleNamespace(name="tap"), c._disconnect_event)
    until(lambda: not session.engaged)
    assert not any(m["type"] == "stroke_commit" for m in bridge.messages)
    assert tp.active and tp.inputMode == "pointer"


def test_shared_switch_stops_both_modes_and_next_start_is_mouse(stroke):
    _, _, tp, source, session = stroke
    wake(stroke)
    output = tp._run.output
    tp.toggle()
    until(lambda: not tp.active and not tp.busy)
    assert output.stopped and session.phase == "idle" and source.stops == 1
    assert tp.inputMode == "pointer"
    tp.toggle()
    until(lambda: tp.state == "running")
    assert tp.inputMode == "pointer" and len(source.starts) == 2


def test_live_voice_finishes_before_preparing_the_same_focused_editor(stroke):
    c, bridge, tp, source, session = stroke
    inline = c._inline_input
    inline._view.update(phase="listening", raw="当前听写", has_composition=True)
    inline._utterance_id = "live-voice"
    c._utterance_active = True
    positioned = []
    tp._run.output.focus_stroke_click = lambda: positioned.append(True) or True
    tp._run.output.options["on_double_click"](dict(bundle="test.editor", pid=123, point=[30, 40]))
    until(lambda: session.phase == "voice")
    assert not positioned and any(m["type"] == "finish_dictation" for m in bridge.messages)
    assert session._source is None
    inline._view.update(phase="dictated", has_composition=False, edit_requested=False)
    c._utterance_active = False
    session._poll()
    until(lambda: session.phase == "source")
    assert not positioned
    assert len(source.starts) == 1 and source.stops == 0


def test_double_click_uses_current_focus_without_caret_click_or_ax_capture(stroke):
    _, bridge, tp, _, session = stroke
    positioned = []
    tp._run.output.focus_stroke_click = lambda: positioned.append(True) or True
    tp._run.output.options["on_double_click"](dict(bundle="test.editor", pid=123, point=[30, 40]))
    until(lambda: session.phase == "source")
    assert not positioned
    assert not any(m["type"] in {"begin", "finish", "finish_dictation"} for m in bridge.messages)


def test_stroke_reuses_tap_focus_recovery_without_voice_begin(stroke):
    c, bridge, _, _, session = stroke
    session.request()
    original = session._source
    original.event.emit(dict(event="selected", already_selected=True))
    c._inline_input._accept(dict(type="state", epoch=c._inline_input._epoch,
        client_id="", utterance_id="", ready=False, phase="idle"))
    recovery = session._source
    assert recovery is not original
    assert recovery.calls == [("recover", ("test.editor", 123))]
    recovery.event.emit(dict(event="selected", focus_restored=True))
    c._inline_input._accept(dict(type="state", epoch=c._inline_input._epoch,
        client_id="current-field", utterance_id="", ready=True, phase="idle", application="test.editor"))
    assert session.phase == "native"
    assert sum(m["type"] == "stroke_begin" for m in bridge.messages) == 1
    assert not any(m["type"] in {"begin", "finish", "reset", "update"} for m in bridge.messages)


def test_cold_ime_reconnect_keeps_same_stroke_start_and_ignores_old_epoch(stroke):
    c, bridge, _, _, session = stroke
    inline = c._inline_input
    session.request()
    helper, token, epoch = session._source, session._token, inline._epoch
    inline._accept(dict(type="disconnected", epoch=epoch))
    assert session.phase == "source" and session._token == token
    inline._accept(dict(type="connected", epoch="new-epoch"))
    helper.event.emit(dict(event="selected", focus_restored=True))
    inline._accept(dict(type="state", epoch=epoch, ready=True, client_id="old-field", application="test.editor"))
    assert session.phase == "source"
    inline._accept(dict(type="state", epoch="new-epoch", ready=True, client_id="new-field", application="test.editor"))
    assert session.phase == "native"
    assert inline.stroke_target == ("new-epoch", "new-field", token)
    assert not any(m["type"] in {"begin", "finish", "reset", "update"} for m in bridge.messages)


def test_reset_during_preparation_restores_pointer_without_a_stuck_voice_gate(stroke):
    c, _, tp, _, session = stroke
    session.request()
    assert not c._inline_input.begin(auto_select=True)
    c._inline_input.reset()
    session._poll()
    until(lambda: not session.engaged and tp._run.output.active)
    assert not tp.blocksVoice and c._recognition_event.is_set()


@pytest.mark.parametrize("with_ink", [False, True])
def test_leaving_field_exits_even_with_no_strokes(stroke, with_ink):
    c, bridge, tp, _, session = stroke
    wake(stroke)
    if with_ink: tp.addStroke("h")
    c._inline_input._accept(dict(type="state", epoch=c._inline_input._epoch,
        client_id="another-field", ready=True, phase="idle", application="test.editor"))
    until(lambda: not session.engaged)
    assert not tp._code and not tp.strokeCandidates
    assert not any(m["type"] == "stroke_commit" for m in bridge.messages)


def test_focus_rechecked_before_commit_not_only_on_monitor_tick(stroke):
    c, bridge, tp, _, session = stroke
    wake(stroke)
    tp.addStroke("h")
    c._inline_input._client_id = "another-field"
    tp.selectCandidate(0)
    until(lambda: not session.engaged)
    assert not any(m["type"] == "stroke_commit" for m in bridge.messages)


def test_source_activation_cannot_follow_focus_into_another_app(stroke, monkeypatch):
    c, bridge, _, source, session = stroke
    session.request()
    until(lambda: session.phase == "source")
    monkeypatch.setattr(c._inline_input, "_foreground_identity", lambda: ("other.app", 456))
    session._source.event.emit(dict(event="selected"))
    c._inline_input._accept(dict(type="state", epoch=c._inline_input._epoch, client_id="other",
        utterance_id="", ready=True, phase="idle", application="test.editor", request_id="stroke:" + session._token))
    until(lambda: not session.engaged)
    assert len(source.starts) == 1 and not any(m["type"] == "stroke_begin" for m in bridge.messages)


def test_native_readiness_is_required_even_if_source_is_selected(stroke):
    c, bridge, tp, source, session = stroke
    session.request()
    session._source.event.emit(dict(event="selected", focus_restored=True))
    c._inline_input._accept(dict(type="state", epoch=c._inline_input._epoch,
        client_id="", ready=False, phase="idle", application="test.editor"))
    assert session.phase == "source" and tp.inputMode == "pointer"
    assert not any(m["type"] in {"stroke_begin", "begin"} for m in bridge.messages)
    c._inline_input._reply_timed_out()
    until(lambda: not session.engaged and tp._run.output.active)
    assert len(source.starts) == 1 and source.stops == 0 and session._source is None


@pytest.mark.parametrize("failure", ["source", "ready_timeout", "native_hidden", "request_exception"])
def test_failed_double_click_handoff_restores_pointer_and_voice(stroke, monkeypatch, failure):
    c, bridge, tp, source, session = stroke
    output = tp._run.output
    output.active = False  # The pointer worker has consumed the double click.
    events = []
    monkeypatch.setattr(c, "_event_log", lambda event, **fields: events.append((event, fields)))
    if failure == "source":
        def broken(*args): raise RuntimeError("source helper unavailable")
        monkeypatch.setattr(c._inline_input, "_source_switch_factory", broken)
    elif failure == "request_exception":
        def broken(*args): raise RuntimeError("handoff unavailable")
        monkeypatch.setattr(session, "request", broken)
    output.options["on_double_click"](dict(bundle="test.editor", pid=123, point=[30, 40]))
    if failure in {"ready_timeout", "native_hidden"}:
        until(lambda: session.phase == "source")
        session._source.event.emit(dict(event="selected"))
        if failure == "ready_timeout":
            c._inline_input._reply_timed_out()
        else:
            c._inline_input._accept(dict(type="state", epoch=c._inline_input._epoch,
                client_id="stroke-editor", ready=True, phase="idle", application="test.editor",
                request_id="stroke:" + session._token))
            until(lambda: session.phase == "native")
            c._inline_input._accept(dict(type="stroke_ready", epoch=c._inline_input._epoch,
                client_id="stroke-editor", stroke_session=session._token, success=True,
                panel_visible=False, error="浮窗无法显示"))
    until(lambda: not session.engaged and tp._run.output is not output and tp._run.output.active)
    assert tp.inputMode == "pointer" and tp.active and not tp.blocksVoice
    assert tp._run.switch_pending is None
    assert c.recognitionEnabled and c._recognition_event.is_set()
    assert len(source.starts) == 1 and source.stops == 0
    assert not any(m["type"] in {"begin", "finish", "finish_dictation", "stroke_commit"} for m in bridge.messages)
    assert any(event == "STROKE_SESSION" and fields.get("reason") for event, fields in events)


def test_late_preparation_reply_cannot_reopen_cancelled_session(stroke):
    _, bridge, _, _, session = stroke
    session.request()
    old_generation = session._generation
    session.cancel()
    session.request()
    session._input_ready(old_generation, dict(epoch="old", client_id="old"))
    assert session.phase == "source"
    assert not any(m["type"] == "stroke_begin" for m in bridge.messages)


def test_only_live_voice_uses_dictation_finish_before_stroke(stroke, monkeypatch):
    c, bridge, _, source, session = stroke
    inline = c._inline_input
    inline._view.update(phase="listening", raw="当前听写", has_composition=True)
    inline._utterance_id = "live-voice"
    c._utterance_active = True
    session.request()
    until(lambda: session.phase == "voice")
    assert any(m["type"] == "finish_dictation" for m in bridge.messages)
    assert len(source.starts) == 1 and session._source is None
    inline._view.update(phase="dictated", has_composition=False, edit_requested=False)
    c._utterance_active = False
    session._poll()
    assert session.phase == "source"


def test_blank_voice_startup_is_cancelled_without_a_voice_finish(stroke):
    c, bridge, _, _, session = stroke
    inline = c._inline_input
    inline._view.update(phase="starting", raw="")
    inline._startup_buffering = True
    inline._begin_origin = ("test.editor", 123)
    c._utterance_active = True
    session.request()
    until(lambda: session.phase in {"voice", "source"})
    session._poll()
    assert session.phase == "source"
    assert not {"finish", "finish_dictation"} & {m["type"] for m in bridge.messages}


def test_cancelled_handoff_does_not_change_next_voice_finish(stroke):
    c, bridge, _, _, _ = stroke
    inline = c._inline_input
    inline._startup_buffering = True
    inline._pending_update = ("已有内容", False, 7, "")
    inline.finish_for_stroke()
    assert inline._finish_as_dictation
    inline.reset()
    inline._view.update(phase="listening", ready=True)
    inline._utterance_id = "next-voice"
    inline.finish()
    assert bridge.messages[-1]["type"] == "finish"


def test_enabling_voice_during_strokes_changes_preference_without_listening(stroke):
    c, _, tp, _, _ = stroke
    wake(stroke)
    c.pauseRecognition()
    c.startRecognition()
    assert c.recognitionEnabled and not c._recognition_event.is_set()
    tp.strokeSession.cancel()
    until(lambda: not tp.busy)
    assert c._recognition_event.is_set()


def test_late_native_ready_cannot_reopen_cancelled_session(stroke):
    c, _, _, source, session = stroke
    session.request()
    until(lambda: session.phase == "source")
    token = session._token
    session.cancel()
    c._inline_input._accept(dict(type="stroke_ready", epoch=c._inline_input._epoch,
        client_id="stroke-editor", stroke_session=token, success=True))
    QTest.qWait(30)
    assert session.phase == "idle" and len(source.starts) == 1


def test_snap_no_longer_wakes_and_stale_double_click_cannot_switch(stroke):
    c, _, tp, source, session = stroke
    assert not session.consume(SimpleNamespace(name="snap"), c._disconnect_event)
    tp._receive(tp._run, "double_click", (tp._run.output, None, time.monotonic() - 2))
    assert session.phase == "idle" and len(source.starts) == 1


def test_local_editor_uses_same_gesture_and_exits_on_local_focus_loss(stroke):
    c, bridge, tp, source, session = stroke
    tp.setLocalStrokeInput(True)
    tp.setLocalStrokeFocus(True)
    tp._run.output.options["on_double_click"]()
    until(lambda: session.phase == "running")
    assert session.local
    assert not any(m["type"] in {"begin", "stroke_begin", "finish_dictation"} for m in bridge.messages)
    tp.addStroke("h")
    tp.setLocalStrokeFocus(False)
    assert not session.engaged and tp._code == ""


def test_current_editor_keyboard_clears_ink_but_not_mode(stroke):
    c, _, tp, _, session = stroke
    wake(stroke)
    tp.addStroke("h")
    epoch, client, token = c._inline_input.stroke_target
    c._inline_input._accept(dict(type="stroke_ink_cleared", epoch=epoch, client_id=client, stroke_session=token))
    until(lambda: not tp._code)
    assert session.phase == "running" and tp.active


def test_previous_session_clear_cannot_close_reopened_strokes_in_same_editor(stroke):
    c, _, tp, _, session = stroke
    wake(stroke)
    old_token = session._token
    session.cancel()
    until(lambda: tp._run.output_ready)
    wake(stroke)
    tp.addStroke("h")
    c._inline_input._accept(dict(type="stroke_cleared", epoch=c._inline_input._epoch,
        client_id=c._inline_input.stroke_target[1], stroke_session=old_token))
    assert session.phase == "running" and tp._code == "h"


def test_updated_ui_previews_canonical_shape(stroke, inline_ui, tmp_path):
    from PySide6.QtCore import QObject
    _, _, tp, _, _ = stroke
    root = inline_ui[3]
    root.resize(940, 700)
    root.setProperty("currentPage", 3)
    root.show(); root.requestActivate()
    root.findChild(QObject, "touchpadPage").setProperty("section", "stroke")
    tp.setInputMode("stroke")
    QTest.qWait(40)
    tp.addStroke("z")
    QTest.qWait(30)
    trail = root.findChild(QObject, "strokeTrailDisplay")
    assert trail.property("standard") is True
    assert root.grabWindow().save(str(tmp_path / "stroke-canonical-preview.png"))
    assert tp.strokeGestureLabel == "Touchpad 双击"
    assert tp.doubleClickIntervalMs == 400
    assert root.findChild(QObject, "strokeWakeEnabled") is None
    assert root.findChild(QObject, "strokeRingToggle") is None


def test_canonical_paths_and_display_normalization_leave_sensor_data_unchanged():
    assert set(STANDARD_STROKES) == set("hspnz")
    points = [[0, 0], [100, -40], [150, 60]]
    before = [list(p) for p in points]
    view = normalized_trace(points)
    assert points == before and all(0 <= x <= 1 for p in view for x in p)
    assert normalized_trace([[0, 0], [float("nan"), 1]]) == []
    copy = standard_stroke("h"); copy[0][0] = -1
    assert standard_stroke("h")[0][0] >= 0


@pytest.mark.parametrize("role,editable,enabled,subrole,expected", [
    ("AXTextArea", True, True, "", True),
    ("AXButton", False, True, "", False),
    ("AXTextField", True, False, "", False),
    ("AXTextField", True, True, "AXSecureTextField", False),
    ("AXGroup", True, True, "", True),
])
def test_wake_eligibility_requires_an_editable_focused_control(role, editable, enabled, subrole, expected):
    from proximic_ring.stroke_focus import editable_target
    class AX:
        def metadata(self, node):
            return dict(AXRole=role, AXIsEditable=editable, AXEnabled=enabled, AXSubrole=subrole)
        def attr(self, node, attribute): return True
        def settable(self, node, attribute): return editable
    target = SimpleNamespace(focus=object(), window=object(), blocked=False)
    assert editable_target(target, AX()) is expected


def test_stroke_focus_probe_requires_no_event_posting_permission(monkeypatch):
    from proximic_ring.mac_permissions import PermissionState
    from proximic_ring.app_shortcuts import ShortcutTarget
    from proximic_ring.native_access_worker import Dispatcher
    import proximic_ring.native_access_worker as worker
    import proximic_ring.stroke_focus as focus
    monkeypatch.setattr(worker, "read_permission_state", lambda: PermissionState(True, False))
    monkeypatch.setattr(focus, "editable_target", lambda target: True)
    target = ShortcutTarget("test.editor", 123, "", focus=object(), window=object(), plain_enter=True)
    dispatcher = Dispatcher()
    dispatcher.shortcuts = SimpleNamespace(capture=lambda **kwargs: target)
    result = dispatcher.handle({"operation": "stroke_capture"})["result"]
    assert result["eligible"] is True and result["remote_id"] in dispatcher.targets
    # Ordinary shortcut captures keep their original public shape.
    result = dispatcher.handle({"operation": "capture", "plain_enter": True})["result"]
    assert "eligible" not in result and ShortcutTarget(**result).bundle == "test.editor"


def test_stroke_trace_queue_coalesces_only_live_points_in_the_same_session():
    from proximic_ring.ime_bridge import IMEBridge
    bridge = IMEBridge(lambda _: None)
    bridge._connection = object()
    bridge._epoch = "e"
    def send(kind="stroke_trace", token="a", finished=False, points=None):
        bridge.send(dict(type=kind, client_id="c", stroke_session=token, finished=finished, points=points or []))
    send(points=[[.5, .5]])
    send(points=[[.6, .5]])
    assert len(bridge._outgoing) == 1 and bridge._outgoing[-1]["points"] == [[.6, .5]]
    send(finished=True)
    send(points=[[.5, .5]])
    send(kind="stroke_end")
    send(token="b")
    assert len(bridge._outgoing) == 5
    assert [m["type"] for m in bridge._outgoing] == ["stroke_trace"] * 3 + ["stroke_end", "stroke_trace"]
    bridge._connection = None
