"""Lessons observe ordinary input transactions without owning audio or gestures."""
import os

import pytest
from test_inline_ui import inline_ui


def start_lesson(controller, event):
    event(phase="idle", ready=True, error="", has_composition=False, awaiting_readback=False)
    controller._connected = True
    tutorial = controller.voiceTutorial
    assert tutorial.start()
    tutorial.setEditorFocused(True)
    return tutorial


def begin_sentence(controller, event, session=1):
    assert controller.inlineInput.begin()
    event(phase="listening", ready=True, error="", revision=0,
          polished=False, edit_requested=False, has_composition=True)
    controller._apply_runtime_session_started(session)


def test_three_lessons_follow_native_input_without_practice_capture(inline_ui):
    controller, bridge, _, _, event = inline_ui
    tutorial = start_lesson(controller, event)
    assert not controller.voicePractice.active
    assert tutorial.hint["gestures"] == ["double-tap"]
    begin_sentence(controller, event)
    assert tutorial.hint["id"] == "speak-0" and tutorial.hint["gestures"] == []
    event(ready=True, phase="listening", raw="下午三点")
    assert tutorial.hint["gestures"] == ["tap"]
    assert "未定稿" in tutorial.hint["text"]
    event(ready=True, phase="finishing")
    assert "正在定稿" in tutorial.hint["text"] and tutorial.busy
    event(ready=True, phase="dictated", raw="下午三点开会", has_composition=False)
    assert not tutorial.stepComplete  # State alone is not a write acknowledgment.
    event("settled", phase="dictated", text="下午三点开会")
    assert tutorial.stepComplete
    before = list(bridge.messages)
    tutorial.advance()
    assert bridge.messages == before
    assert tutorial.hint["gestures"] == ["swipe-down"], (tutorial.hint, tutorial._stage, controller.inlineInput._view)
    event(ready=True, phase="polishing", revision=1)
    assert "正在润色" in tutorial.hint["text"]
    # Native settlement can precede its final state update.
    event("settled", phase="dictated", text="下午三点开会。", revision=2)
    assert tutorial.stepComplete and tutorial.busy
    tutorial.advance()
    assert tutorial._stage == 1
    event(ready=True, phase="dictated", polished=True, revision=2)
    # Undoing polish restores a dictated sentence; that is not a passed lesson.
    event(ready=True, phase="polishing", revision=3)
    event("settled", phase="dictated", text="下午三点开会", revision=4)
    event(ready=True, phase="dictated", polished=False, revision=4)
    assert not tutorial.stepComplete and tutorial.hint["gestures"] == ["swipe-down"]
    event(ready=True, phase="polishing", revision=5)
    event("settled", phase="dictated", text="下午三点开会。", revision=6)
    event(ready=True, phase="dictated", polished=True, revision=6)
    tutorial.advance()
    assert tutorial._stage == 2 and tutorial.hint["gestures"] == ["double-tap"]
    begin_sentence(controller, event, session=2)
    assert tutorial.hint["id"] == "speak-2" and tutorial.hint["gestures"] == []
    event(ready=True, phase="listening", raw="把三点改成四点")
    assert tutorial.hint["gestures"] == ["swipe-right"]
    assert "未定稿" in tutorial.hint["text"] or "下划线" in tutorial.hint["text"]
    event(ready=True, phase="finishing", edit_requested=True, revision=1)
    assert "正在应用编辑" in tutorial.hint["text"]
    event(ready=True, phase="editing", revision=2)
    event("settled", phase="edited", text="下午四点开会。", revision=3)
    event(ready=True, phase="edited", revision=3)
    assert tutorial.stepComplete
    assert not controller.voicePractice.active
    before = list(bridge.messages)
    tutorial.stop()
    assert bridge.messages == before  # Closing teaching never cancels normal IME input.


def test_unrelated_empty_failed_and_historical_writes_do_not_complete_lesson(inline_ui):
    controller, _, _, _, event = inline_ui
    tutorial = start_lesson(controller, event)
    old_key = tutorial._current_key()
    tutorial.setEditorFocused(False)
    begin_sentence(controller, event)
    event("settled", phase="dictated", text="其他输入框")
    assert not tutorial.stepComplete
    event(ready=True, phase="dictated")
    tutorial.setEditorFocused(True)
    begin_sentence(controller, event, session=2)
    event("settled", phase="dictated", text="", revision=1)
    assert not tutorial.stepComplete
    key = (*tutorial._current_key(), 2)
    tutorial._settled((*old_key, 2), "dictated", "旧结果", "", False)
    tutorial._settled(key, "dictated", "历史撤销结果", "", True)
    tutorial._settled(key, "dictated", "失败结果", "写入失败", False)
    assert not tutorial.stepComplete
    event(ready=True, phase="dictated", error="", revision=2)
    event("settled", phase="dictated", text="成功结果", revision=2)
    assert tutorial.stepComplete
    event(ready=True, phase="undone", revision=3)
    assert not tutorial.stepComplete


def test_tutorial_respects_current_bindings_and_control_mode(inline_ui):
    from proximic_ring.gestures.gesture_settings import GestureBindings
    controller, _, _, _, event = inline_ui
    tutorial = start_lesson(controller, event)
    controller._gesture_bindings = GestureBindings(confirm=("snap", ""), end=("swipe-left", ""), undo=("", ""), switch_mode=("tap", ""))
    controller.gestureSettingsChanged.emit()
    assert tutorial.hint["gestures"] == ["snap"]
    begin_sentence(controller, event)
    event(ready=True, phase="listening", raw="当前识别文字")
    assert tutorial.hint["gestures"] == ["swipe-left"]
    tutorial._stage = 2
    assert tutorial.hint["gestures"] == ["tap"]
    event(ready=True, phase="dictated")
    controller._speech_control_mode = "proximity"
    assert "靠近 Ring" in tutorial.hint["text"]
    assert tutorial.hint["gestures"] == []


@pytest.mark.parametrize("application,origin,expected", [
    ("", ("", os.getpid()), "com.proximic.voice"),
    ("com.proximic.voice", ("com.proximic.voice", os.getpid()), "com.proximic.voice"),
    ("com.other.editor", ("com.other.editor", 12345), "com.other.editor"),
    ("com.old.editor", ("", os.getpid()), ""),
    ("", None, ""),
])
def test_history_owner_uses_begin_identity_without_changing_native_target(inline_ui, application, origin, expected):
    controller, _, _, _, event = inline_ui
    inline = controller.inlineInput
    event(ready=True, application=application)
    inline._sentence_origin = origin
    assert inline.history_application() == expected
    assert inline.target_reference().process_name == application


def test_history_captures_mythlink_before_focus_loss_and_preserves_it_on_undo(inline_ui):
    import json
    controller, _, _, _, event = inline_ui
    event(ready=True, application="")
    controller.inlineInput._sentence_origin = ("", os.getpid())
    controller._apply_runtime_session_started(61)
    dataset = controller._modification_dataset
    record_path = dataset._interaction_path(dataset._session_interactions[61])
    record = json.loads(record_path.read_text())
    assert record["target"]["application"] == "com.proximic.voice"
    target_key = record["target"]["key"]
    event(ready=False, phase="interrupted")
    controller.inlineInput.settled_session_id = 61
    controller._settle_inline_input("interrupted", "")
    # A late historical undo can arrive while another app has focus.
    event(ready=True, application="com.other.editor", phase="dictated")
    controller.inlineInput._sentence_origin = ("com.other.editor", 12345)
    controller.inlineInput.settled_session_id = 61
    controller._settle_inline_input("undone", "")
    record = json.loads(record_path.read_text())
    assert record["target"] == dict(application="com.proximic.voice", key=target_key)
    assert record["outcome"]["status"] == "undone"


def test_delayed_native_ready_records_bound_session_owner(inline_ui):
    import json
    controller, _, _, _, event = inline_ui
    inline = controller.inlineInput
    # START races activation: the cached native client still belongs to another app.
    inline._sentence_origin = ("", os.getpid())
    controller._apply_runtime_session_started(62)
    dataset = controller._modification_dataset
    record_path = dataset._interaction_path(dataset._session_interactions[62])
    assert not json.loads(record_path.read_text())["target"].get("application")
    event(application="", phase="listening", ready=True)
    inline.began.emit()
    assert json.loads(record_path.read_text())["target"]["application"] == "com.proximic.voice"


@pytest.mark.parametrize("question", [False, True])
def test_agent_task_and_answer_capture_mythlink_owner_through_normal_startup(inline_ui, monkeypatch, question):
    import json
    import threading
    from PySide6.QtTest import QTest
    from proximic_ring.ui.agent.agent_overlay import AgentOverlay
    from test_agent_ui import finish_fixture_voice
    from test_input_source_activation import FakeSource
    from test_agent_workspace import request
    from test_ui_shell import visual_child

    owner, bridge, _, _, event = inline_ui
    inline, agent = owner.inlineInput, owner.agent
    finish_fixture_voice(owner)
    inline._view.update(phase="idle", ready=False, has_composition=False)
    owner._disconnect_event = threading.Event()
    owner._recognition_event.set()
    inline._source_switch_factory = FakeSource
    monkeypatch.setattr(inline, "_foreground_identity", lambda: ("", os.getpid()))
    overlay = AgentOverlay(agent, owner)
    try:
        agent._visible = agent._focused = True
        agent._apply_state({"requests": [request("question", "question")] if question else []})
        overlay.refresh(); overlay.focus("task"); QTest.qWait(40)
        assert visual_child(overlay.windows["task"].rootObject(), "agentEditor").property("activeFocus")
        accepted = []
        owner._prepare_inline_gesture_start(accepted.append, owner._disconnect_event)
        inline._source_activation.event.emit({"event": "selected", "already_selected": True})
        event(phase="idle", ready=True, utterance_id="", application="")
        assert bridge.messages[-1]["type"] == "begin"
        event(phase="listening", ready=True, application="")
        assert accepted == [True]
        owner._apply_runtime_status("[ASR] START gesture")
        owner._apply_runtime_session_started(63)
        dataset = owner._modification_dataset
        record = json.loads(dataset._interaction_path(dataset._session_interactions[63]).read_text())
        assert record["target"]["application"] == "com.proximic.voice"
    finally:
        overlay.close()


@pytest.mark.parametrize("size", [(1200, 780), (940, 700)])
def test_tutorial_real_editor_uses_native_events_and_has_no_capture_button(inline_ui, size):
    from PySide6.QtCore import QObject, QMetaObject
    from PySide6.QtTest import QTest
    from test_ui_figma_redesign import click
    controller, bridge, _, root, event = inline_ui
    event(phase="idle", ready=True)
    controller._connected = True
    controller.connectedChanged.emit()
    root.resize(*size); root.show(); root.requestActivate()
    dialog = root.findChild(QObject, "tutorialPreviewDialog")
    QMetaObject.invokeMethod(dialog, "open")
    for _ in range(40):
        if dialog.property("opened"):
            break
        QTest.qWait(30)
    assert dialog.property("opened")
    click(root, "tutorialPrepareButton")
    before = list(bridge.messages)
    click(root, "tutorialBeginButton"); QTest.qWait(100)
    assert dialog.property("step") == 2
    editor = root.findChild(QObject, "tutorialPracticeEditor")
    assert editor.property("activeFocus") and not editor.property("readOnly")
    assert not root.findChild(QObject, "tutorialExerciseButton").property("visible")
    assert not controller.voicePractice.active and bridge.messages == before
    begin_sentence(controller, event)
    assert root.findChild(QObject, "tutorialHintTitle").property("text") == "请开始说话"
    event(ready=True, phase="listening", raw="下午三点开会")
    assert root.findChild(QObject, "tutorialCurrentGesture").property("gestures") == ["tap"]
    event(ready=True, phase="dictated", has_composition=False)
    event("settled", phase="dictated", text="下午三点开会")
    click(root, "tutorialNextButton"); QTest.qWait(40)
    assert dialog.property("step") == 3 and editor.property("activeFocus")
    before = list(bridge.messages)
    QMetaObject.invokeMethod(dialog, "close"); QTest.qWait(100)
    assert not controller.voiceTutorial.active and bridge.messages == before
