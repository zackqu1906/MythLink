"""Agent text fields use the same native startup as any other IME client."""
import os
import threading
import time
from types import MethodType, SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtTest import QTest

from proximic_ring.ui.voice.inline_controller import InlineInputController
from proximic_ring.ui.agent.agent_overlay import AgentOverlay
from proximic_ring.gestures.gesture_settings import GestureBindings
from test_inline_ui import inline_ui
from test_input_source_activation import FakeSource
from test_agent_ui import finish_fixture_voice
from test_agent_workspace import request
from test_stroke_session import stroke, until, wake
from test_ui_shell import visual_child


foreground_identity = InlineInputController._foreground_identity


def unbundled_editor(inline, monkeypatch, *, pid=38368):
    from proximic_ring.platforms.macos import mac_workspace
    front = SimpleNamespace(processIdentifier=lambda: pid, bundleIdentifier=lambda: None)
    monkeypatch.setattr(mac_workspace, 'frontmost_application', lambda: front)
    monkeypatch.setattr(inline, '_foreground_identity', MethodType(foreground_identity, inline))
    inline._source_switch_factory = FakeSource
    return front


@pytest.mark.parametrize('question', [False, True])
@pytest.mark.parametrize('start,end', [('double-tap', 'tap'), ('tap', 'tap'),
                                     ('swipe-left', 'swipe-right'), ('snap', 'double-tap')])
def test_configured_gestures_reach_native_startup_in_source_launched_agent(inline_ui, monkeypatch, question, start, end):
    owner, bridge, _, _, event = inline_ui
    inline, agent = owner._inline_input, owner.agent
    finish_fixture_voice(owner)
    inline._view.update(phase='idle', ready=False, has_composition=False)
    owner._disconnect_event = threading.Event()
    owner._recognition_event.set()
    owner._save_gesture_bindings(GestureBindings(confirm=(start, ''), end=(end, ''), undo=('', ''), switch_mode=('', '')))
    unbundled_editor(inline, monkeypatch)
    # The same gesture may be used by an application outside its text fields.
    monkeypatch.setattr(owner._app_gestures.catalog, 'uses', lambda name: True)
    monkeypatch.setattr(owner._app_gestures.backend, 'capture', lambda **_: pytest.fail('Agent editor must not use AX dialog routing'))
    overlay = AgentOverlay(agent, owner)
    try:
        agent._visible = agent._focused = True
        agent._apply_state({'requests': [request('question', 'question')] if question else []})
        overlay.refresh(); overlay.focus('task'); QTest.qWait(40)
        editor = visual_child(overlay.windows['task'].rootObject(), 'agentEditor')
        assert editor.property('activeFocus')
        key = agent.inputKey
        accepted = []
        # Firmware endpoints pass through the Agent to the existing audio gate.
        assert owner._ring_gestures.filter(SimpleNamespace(name=start), False, owner._disconnect_event)
        owner._prepare_inline_gesture_start(accepted.append, owner._disconnect_event)
        source = inline._source_activation
        assert source.calls == [('start', ('', 38368))]
        assert not accepted  # Audio cannot start until the native client ACKs.
        source.event.emit({'event': 'selected', 'already_selected': True})
        event(phase='idle', ready=True, utterance_id='', application='')
        assert bridge.messages[-1]['type'] == 'begin'
        event(phase='listening', ready=True, application='')
        assert accepted == [True]
        assert agent.inputKey == key and editor.property('activeFocus')
        assert owner._ring_gestures.filter(SimpleNamespace(name=end), True, owner._disconnect_event)
    finally:
        overlay.close()


def test_agent_keeps_editor_during_ime_helper_handoff_only(inline_ui, monkeypatch):
    owner = inline_ui[0]; agent = owner.agent
    overlay = AgentOverlay(agent, owner)
    try:
        agent._visible = agent._focused = True
        overlay.refresh(); overlay.focus('task'); QTest.qWait(40)
        editor = visual_child(overlay.windows['task'].rootObject(), 'agentEditor')
        source = FakeSource(owner._inline_input)
        owner._inline_input._source_activation = source
        helper_focused = [True]
        monkeypatch.setattr(source, 'owns_foreground', lambda: helper_focused[0])
        monkeypatch.setattr('proximic_ring.ui.agent.agent_overlay.QGuiApplication.focusWindow', lambda: None)
        overlay._focus_changed(None); QTest.qWait(70)
        assert agent.focused and editor.isEnabled() and editor.property('activeFocus')
        helper_focused[0] = False
        QTest.qWait(40)
        assert not agent.focused  # An actual app switch still releases focus.
        overlay._focus_changed(overlay.windows['task'])
        assert agent.focused and editor.property('activeFocus')
    finally:
        overlay.close()


def test_unbundled_agent_strokes_use_native_palette(stroke, monkeypatch):
    owner, bridge, tp, _, session = stroke
    inline, agent = owner._inline_input, owner.agent
    unbundled_editor(inline, monkeypatch)
    agent._visible = agent._focused = True
    agent.editorFocused('task', True)
    tp._run.output.options['on_double_click']({'bundle': '', 'pid': 38368})
    until(lambda: session.phase == 'source')
    assert session._source.calls == [('start', ('', 38368))]
    session._source.event.emit({'event': 'selected', 'already_selected': True})
    inline._accept(dict(type='state', epoch=inline._epoch, client_id='agent-editor',
                        utterance_id='', ready=True, phase='idle', application=''))
    until(lambda: session.phase == 'native')
    inline._accept(dict(type='stroke_ready', epoch=inline._epoch, client_id='agent-editor',
                        stroke_session=session._token, success=True, panel_visible=True, context=''))
    until(lambda: session.phase == 'running')
    assert not session.local and any(m['type'] == 'stroke_begin' for m in bridge.messages)
    tp.addStroke('h')
    assert any(m['type'] == 'stroke_update' for m in bridge.messages)


@pytest.mark.parametrize('previous_phase', ['dictated', 'error'])
def test_question_transition_retires_old_sentence_and_restores_answer_focus(inline_ui, monkeypatch, previous_phase):
    owner, bridge, _, _, event = inline_ui
    inline, agent = owner._inline_input, owner.agent
    finish_fixture_voice(owner)
    inline._view.update(phase=previous_phase, application='', has_composition=False, awaiting_readback=False)
    owner._disconnect_event = threading.Event()
    owner._recognition_event.set()
    unbundled_editor(inline, monkeypatch, pid=os.getpid())
    monkeypatch.setattr(agent, '_send', lambda _: None)
    overlay = AgentOverlay(agent, owner)
    try:
        agent._visible = agent._focused = True
        overlay.focus('task'); QTest.qWait(40)
        editor = visual_child(overlay.windows['task'].rootObject(), 'agentEditor')
        old = inline._utterance_id
        assert old
        agent._apply_state({'requests': [request('answer-one', 'question')]})
        QTest.qWait(40)
        assert any(m['type'] == 'end_input' and m['outcome'] == 'replaced'
                   and m['utterance_id'] == old for m in bridge.messages)
        assert inline._utterance_id == '' and not inline.canUndo
        assert editor.property('activeFocus') and agent._editor_key == 'answer-one'

        # A submitted answer temporarily disables the same physical TextArea.
        editor.setProperty('text', '选第一个')
        agent.submit('answer-one'); QTest.qWait(30)
        assert not editor.isEnabled()
        agent._apply_state({'requests': [request('answer-two', 'question')]})
        QTest.qWait(40)
        assert editor.isEnabled() and editor.property('activeFocus')
        assert agent._editor_key == 'answer-two'
        accepted = []
        owner._prepare_inline_gesture_start(accepted.append, owner._disconnect_event)
        current = inline._utterance_id
        assert current and current != old
        inline._source_activation.event.emit({'event': 'selected', 'already_selected': True})
        event(phase='idle', ready=True, utterance_id='', application='')
        event(phase='listening', ready=True, application='')
        assert accepted == [True]
        event(phase='error', ready=True, utterance_id=old, error='旧输入错误', application='')
        assert inline._utterance_id == current and inline._view['phase'] == 'listening'
    finally:
        overlay.close()


@pytest.mark.parametrize('change', ['submit', 'question'])
@pytest.mark.parametrize('external', [False, True])
def test_programmatic_editor_change_retires_only_its_own_native_sentence(inline_ui, monkeypatch, change, external):
    owner, bridge, _, _, _ = inline_ui
    inline, agent = owner._inline_input, owner.agent
    finish_fixture_voice(owner)
    inline._view.update(application='', has_composition=False, awaiting_readback=False)
    # Include the short interval before Qt observes loss of foreground focus.
    unbundled_editor(inline, monkeypatch, pid=os.getpid() + (1 if external else 0))
    agent._visible = agent._focused = True
    agent.editorFocused('task', True)
    monkeypatch.setattr(agent, '_send', lambda _: None)
    old = inline._utterance_id
    bridge.messages.clear()
    if change == 'submit':
        agent.edit('task', '新的任务')
        agent.submit('task')
    else:
        agent._apply_state({'requests': [request('new', 'question')]})
    resets = [m for m in bridge.messages if m['type'] == 'end_input']
    assert len(resets) == (0 if external else 1)
    if resets:
        assert resets[0]['outcome'] == ('sent' if change == 'submit' else 'replaced')
    assert inline._utterance_id == (old if external else '')


def test_failed_agent_submission_preserves_sentence_without_sent_feedback(inline_ui, monkeypatch):
    owner, bridge, _, _, _ = inline_ui
    inline, agent = owner._inline_input, owner.agent
    finish_fixture_voice(owner)
    inline._view.update(application='', has_composition=False, awaiting_readback=False)
    unbundled_editor(inline, monkeypatch, pid=os.getpid())
    agent._visible = agent._focused = True
    agent.editorFocused('task', True)
    agent.edit('task', '保留草稿')
    monkeypatch.setattr(agent, '_send', lambda _: False)
    old = inline._utterance_id
    bridge.messages.clear()
    agent.submit('task')
    assert inline._utterance_id == old and agent.draft('task') == '保留草稿'
    assert not any(m['type'] == 'end_input' for m in bridge.messages)


def test_parked_steer_and_answers_have_separate_native_sessions(inline_ui, monkeypatch):
    from PySide6.QtGui import QInputMethodEvent
    owner, bridge, _, _, event = inline_ui
    inline, agent = owner._inline_input, owner.agent
    finish_fixture_voice(owner)
    inline._view.update(application='')
    owner._disconnect_event = threading.Event()
    owner._recognition_event.set()
    unbundled_editor(inline, monkeypatch, pid=os.getpid())
    sent = []
    monkeypatch.setattr(agent, '_send', sent.append)
    overlay = AgentOverlay(agent, owner)
    try:
        agent._visible = agent._focused = True
        agent._apply_state({'turnId':'original'})
        overlay.focus('task'); QTest.qWait(40)
        editor = visual_child(overlay.windows['task'].rootObject(), 'agentEditor')
        draft = '  改为20项\n并保留备注🙂  '
        editor.setProperty('text', draft)
        old = inline._utterance_id
        old_context = (owner._app_gestures.sentence(), owner._gesture_bindings)
        questions = [request('q1','question'), request('q2','question')]
        agent._apply_state({'turnId':'original','requests':questions}); QTest.qWait(30)
        assert agent.inputKey=='q1' and editor.property('text')==''
        assert agent.draft('task')==draft and not inline._utterance_id
        assert any(m['type']=='end_input' and m['utterance_id']==old
                   and m['outcome']=='replaced' for m in bridge.messages)
        agent.submit('task')
        agent.submit('q1')  # No answer yet: parked text is not a default answer.
        agent._input_gesture('swipe-up','task',owner._disconnect_event,time.monotonic(),old_context)
        assert sent==[]

        def begin_voice():
            accepted = []
            owner._prepare_inline_gesture_start(accepted.append, owner._disconnect_event)
            sentence = inline._utterance_id
            inline._source_activation.event.emit({'event':'selected','already_selected':True})
            event(phase='idle',ready=True,utterance_id='',application='')
            event(phase='listening',ready=True,application='')
            assert accepted==[True]
            return sentence

        sentences = [old]
        for token, text in [('q1','2'),('q2','直接回答：使用英文')]:
            current = begin_voice()
            assert current not in sentences
            sentences.append(current)
            event(phase='error',ready=True,utterance_id=old,error='迟到的旧错误',application='')
            assert inline._view['phase']=='listening'
            commit = QInputMethodEvent(); commit.setCommitString(text)
            QCoreApplication.sendEvent(editor, commit)
            finish_fixture_voice(owner)
            event(phase='dictated',ready=True,application='',has_composition=False)
            QTest.qWait(20)
            assert agent.draft(token)==text and agent.draft('task')==draft
            agent.submit(token); QTest.qWait(20)
            assert any(m['type']=='end_input' and m['utterance_id']==current
                       and m['outcome']=='sent' for m in bridge.messages)
            assert not editor.isEnabled()  # Wait for server to resolve this question.
            questions = [q for q in questions if q['token'] != token]
            agent._apply_state({'turnId':'original','requests':questions}); QTest.qWait(30)

        assert sent==[{'type':'choose','token':'q1','index':1},
                      {'type':'answer','questionToken':'q2','text':'直接回答：使用英文'}]
        assert agent.inputKey=='task' and editor.property('text')==draft
        assert editor.property('activeFocus') and editor.property('cursorPosition')==editor.property('length')
        assert agent.workspace.draft_turns['task']=='original'
        assert begin_voice() not in sentences
        finish_fixture_voice(owner)
        agent.submit('task')
        assert sent[-1]=={'type':'steer','targetTurn':'original','text':draft.strip()}
    finally:
        overlay.close()


def test_question_waits_for_stroke_commit_then_retires_its_session(stroke, monkeypatch):
    owner, bridge, tp, _, session = stroke
    inline, agent = owner._inline_input, owner.agent
    monkeypatch.setattr(inline, '_foreground_identity', lambda: ('test.editor',os.getpid()))
    monkeypatch.setattr(agent, '_send', lambda _: None)
    agent._visible = agent._focused = True
    agent.editorFocused('task',True)
    agent._apply_state({'turnId':'turn'})
    wake(stroke)
    token = session._token
    tp.strokeTraceChanged.emit([[0.1,0.1],[0.3,0.1]],False)
    agent._apply_state({'turnId':'turn','requests':[request('q','question')]})
    assert agent.inputKey=='task' and session.engaged
    tp.addStroke('h')
    char = tp.strokeCandidates[0]
    tp.selectCandidate(0)
    until(lambda: bool(tp._pending_stroke_commit))
    native = next(m for m in reversed(bridge.messages) if m['type']=='stroke_commit')
    QTest.qWait(20)
    assert agent.inputKey=='task'
    agent.edit('task',char)  # Native client has written before its readback ACK.
    inline._accept(dict(type='stroke_result',epoch=inline._epoch,
        client_id=native['client_id'],request_id=native['request_id'],success=True))
    until(lambda: agent.inputKey=='q')
    assert agent.draft('task')==char and not agent.draft('q')
    assert not session.engaged
    assert any(m['type']=='stroke_end' and m['stroke_session']==token for m in bridge.messages)
    agent.edit('q','回答');agent.submit('q')
    agent._apply_state({'turnId':'turn','requests':[]})
    assert agent.inputKey=='task' and agent.draft('task')==char
