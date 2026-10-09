"""Stroke input on the current UI/voice stack, with no real device or editor writes."""
from types import SimpleNamespace

import pytest
from test_inline_ui import inline_ui
from test_ui_touchpad import Output, prepare


def idle(inline_ui):
    c, bridge, _, root, event = inline_ui
    c._utterance_active = False
    c._pending_inline_audio_start = None
    c._interaction_state = "idle"
    event(phase="interrupted", ready=True, composing=False, writing=False,
          edit_requested=False, awaiting_readback=False)
    tp, source = prepare(c)
    tp.stroke_output_factory = Output
    c._recognition_event.set()
    return c, bridge, root, tp, source


def start_stroke(inline_ui):
    from PySide6.QtTest import QTest
    c, bridge, root, tp, source = idle(inline_ui)
    assert not c._ring_gestures.speech_busy()
    tp.setInputMode("stroke")
    tp.setLocalStrokeInput(True)
    tp.start(); QTest.qWait(20)
    source.future.set_result(None); QTest.qWait(20)
    assert tp.active and tp._run.output.active
    tp.setLocalStrokeInput(True)
    return c, bridge, root, tp, source


def test_stroke_requires_voice_to_finish_without_cancelling_it(inline_ui):
    c, bridge, *_ = inline_ui
    tp, source = prepare(c)
    before = len(bridge.messages)
    tp.setInputMode("stroke")
    assert tp.inputMode == "pointer" and "请先" in tp.message
    assert not source.starts and len(bridge.messages) == before
    assert c._inline_input._view["phase"] == "listening"


def test_switching_outputs_keeps_existing_stream_and_voice_choice(inline_ui):
    from PySide6.QtTest import QTest
    c, _, _, tp, source = start_stroke(inline_ui)
    old = tp._run.output
    assert c.recognitionEnabled and not c._recognition_event.is_set()
    assert not c._inline_input.begin(auto_select=True)
    assert source.starts[0]["duration_s"] is None
    tp.setInputMode("pointer"); QTest.qWait(20)
    assert old.stopped and tp._run.output.active and tp.active
    assert len(source.starts) == 1 and source.stops == 0
    assert c.recognitionEnabled and c._recognition_event.is_set()
    tp.setInputMode("stroke"); QTest.qWait(20)
    old.options["on_stroke"]([(0, 0), (100, 0)])
    old.options["on_end"]("old output", RuntimeError("old error"))
    QTest.qWait(20)
    assert tp._code == "" and tp.active
    tp.stop(); QTest.qWait(20)
    assert c._recognition_event.is_set() and source.stops == 1


def test_default_gestures_are_consumed_before_scene_and_voice(inline_ui, monkeypatch):
    from PySide6.QtTest import QTest
    c, _, _, tp, _ = start_stroke(inline_ui)
    monkeypatch.setattr(c._app_gestures, "scene_envelope", lambda _: pytest.fail("stroke escaped to scenes"))
    tp.addStroke("h")
    candidates = tp.strokeCandidates
    assert len(candidates) == 5
    for name, selected in [("swipe-right", 1), ("swipe-left", 0)]:
        assert not c._ring_gestures.filter(SimpleNamespace(name=name), False, c._disconnect_event)
        QTest.qWait(10)
        assert tp.selectedCandidate == selected
    entered = []
    tp.localCharacterCommitted.connect(entered.append)
    assert not c._ring_gestures.filter(SimpleNamespace(name="tap"), False, c._disconnect_event)
    QTest.qWait(10)
    assert entered == [candidates[0]] and tp._code == ""
    tp.addStroke("h"); tp.addStroke("s")
    for name, code in [("swipe-up", "h"), ("swipe-down", "")]:
        assert not c._ring_gestures.filter(SimpleNamespace(name=name), False, c._disconnect_event)
        QTest.qWait(10)
        assert tp._code == code
    tp.stop(); QTest.qWait(10)
    assert not tp.consume_gesture(SimpleNamespace(name="tap"), c._disconnect_event)


def test_external_commit_waits_for_matching_input_method_ack(inline_ui):
    c, bridge, _, tp, _ = idle(inline_ui)
    tp.setInputMode("stroke")
    tp.addStroke("h")
    assert bridge.messages[-1]["type"] == "stroke_update"
    char = tp.strokeCandidates[0]
    tp.selectCandidate(0)
    request = bridge.messages[-1]
    assert request["type"] == "stroke_commit" and tp._code == "h"
    c._inline_input._accept(dict(type="stroke_result", epoch=c._inline_input._epoch,
        client_id=request["client_id"], request_id="stale", success=True))
    assert tp._code == "h"
    c._inline_input._accept(dict(type="stroke_result", epoch=c._inline_input._epoch,
        client_id=request["client_id"], request_id=request["request_id"], success=True))
    assert tp._code == "" and char in tp.commitMessage and not tp.blocksVoice


def test_already_queued_voice_start_cannot_write_during_strokes(inline_ui):
    from PySide6.QtTest import QTest
    c, bridge, _, tp, _ = start_stroke(inline_ui)
    before = len(bridge.messages)
    c._apply_runtime_status("[ASR] START t=2.000s")
    assert not c._utterance_active and c._cancel_utterance_event.is_set()
    assert c._ignore_asr_updates_until_next_start and len(bridge.messages) == before
    tp.stop(); QTest.qWait(20)


def test_external_target_switch_clears_old_candidates(inline_ui):
    c, _, _, tp, _ = idle(inline_ui)
    tp.setInputMode("stroke"); tp.addStroke("h")
    c._inline_input._accept(dict(type="state", epoch=c._inline_input._epoch,
        client_id="new-editor", utterance_id="", phase="idle", ready=True))
    assert tp._code == "" and tp.strokeCandidates == [] and not tp.blocksVoice


def test_native_keyboard_takeover_invalidates_only_current_composition(inline_ui):
    c, _, _, tp, _ = idle(inline_ui)
    tp.setInputMode("stroke"); tp.addStroke("h")
    c._inline_input._accept(dict(type="stroke_cleared", epoch="old", client_id=c._inline_input._client_id))
    assert tp._code == "h"
    c._inline_input._accept(dict(type="stroke_cleared", epoch=c._inline_input._epoch,
                                 client_id=c._inline_input._client_id))
    assert tp._code == "" and c._recognition_event.is_set()


def test_pending_external_commit_cannot_block_next_local_input(inline_ui):
    _, _, _, tp, _ = idle(inline_ui)
    tp.setInputMode("stroke"); tp.addStroke("h"); tp.selectCandidate(0)
    assert tp._pending_stroke_commit
    tp.setLocalStrokeInput(True)
    assert not tp._pending_stroke_commit and not tp._code
    tp.addStroke("s")
    assert tp._code == "s"
    tp.clearStrokes()


def test_stopping_strokes_does_not_reenable_user_paused_recognition(inline_ui):
    from PySide6.QtTest import QTest
    c, _, _, tp, _ = start_stroke(inline_ui)
    c.pauseRecognition()
    tp.stop(); QTest.qWait(20)
    assert not c.recognitionEnabled and not c._recognition_event.is_set()


@pytest.mark.parametrize("size", [(1440, 940), (940, 700)])
def test_local_editor_candidates_and_layout(inline_ui, size, tmp_path):
    from PySide6.QtCore import QObject, QPointF
    from PySide6.QtTest import QTest
    c, _, root, tp, _ = idle(inline_ui)
    root.resize(*size); root.setProperty("currentPage", 3); root.show(); root.requestActivate()
    root.findChild(QObject, "touchpadPage").setProperty("section", "stroke")
    tp.setInputMode("stroke"); QTest.qWait(60)
    box = root.findChild(QObject, "strokeCompositionPanel")
    assert box.property("visible") and box.property("inputVisible")
    tp.addStroke("h"); QTest.qWait(20)
    assert tp._code and tp._local_stroke_input, (tp._code, tp._local_stroke_input, root.isActive(), c._inline_input._view)
    tp.selectCandidate(0); QTest.qWait(20)
    editor = root.findChild(QObject, "strokeTestEditor")
    assert editor.property("text")
    for name in ["strokeSearchShell", "strokeCandidateRow", "strokeTools"]:
        item = root.findChild(QObject, name)
        point = item.mapToScene(QPointF())
        assert point.x() >= root.property("sidebarWidth")
        assert point.x() + item.width() <= root.width()
    assert root.grabWindow().save(str(tmp_path / f"stroke-{size[0]}.png"))
    root.setProperty("currentPage", 0); QTest.qWait(20)
    assert not tp._local_stroke_input
