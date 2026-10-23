"""Independent task cards share a single floating input workspace."""
import threading
import time
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter
from PySide6.QtTest import QTest

from test_agent_ui import finish_fixture_voice, inline_ui
from test_agent_workspace import request
from test_ui_shell import visual_child
from proximic_ring.ui.agent.agent_overlay import AgentOverlay


def click(window, name):
    QTest.qWait(50)
    item = visual_child(window.rootObject(), name)
    assert item.isVisible() and item.isEnabled()
    point = item.mapToScene(QPointF(item.width()/2, item.height()/2)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
    QTest.qWait(70)


def test_new_task_above_running_card_isolated_input_commands_and_results(inline_ui, monkeypatch, tmp_path):
    owner, _, _, main, _ = inline_ui
    root = owner.agent
    finish_fixture_voice(owner)
    root._visible = root._focused = True
    root._apply_state({'turnId': 'old-turn', 'status': '执行中', 'agent': '正在整理演示文稿…',
                       'codexThreadId': 'old-thread'})
    root._auto_context_mode = 'off'
    old_commands, new_commands = [], []
    monkeypatch.setattr(root, '_send', old_commands.append)
    overlay = AgentOverlay(root, owner)
    overlay.reduce_motion = True
    try:
        overlay.focus('task')
        QTest.qWait(50)
        first = overlay.windows['task']
        visual_child(first.rootObject(), 'agentEditor').setProperty('text', '原任务的修改草稿')
        click(first, 'agentNewTaskButton')
        assert len(root.sessions.members) == 2
        second = root.sessions.active
        assert second is not root and second.workspace is not root.workspace
        assert second._focus_channel is root._focus_channel
        assert second.owner._inline_input is owner._inline_input
        assert not second.workspace.state and not second.draft('task')
        assert second.runtime_directory() != root.runtime_directory()
        assert second.runtime_directory().parent.name == 'sessions'
        key = root.sessions.window_key(second, 'task')
        window = overlay.windows[key]
        assert window.y() + window.height() < first.y()
        assert root.workspaceActive and second.workspaceActive
        assert second.focused and not root.focused
        assert not first.rootObject().property('compact') and not window.rootObject().property('compact')
        assert root.draft('task') == '原任务的修改草稿' and root.busy
        assert not main.isVisible()

        image = QImage(480, first.y()+first.height()-window.y()+24, QImage.Format_ARGB32)
        image.fill(QColor('#171819'))
        painter = QPainter(image)
        painter.drawImage(20, 12, window.grabWindow())
        painter.drawImage(20, first.y()-window.y()+12, first.grabWindow())
        painter.end()
        assert image.save(str(tmp_path/'two-task-cards.png'))

        monkeypatch.setattr(second, '_send', new_commands.append)
        visual_child(window.rootObject(), 'agentEditor').setProperty('text', '另外帮我整理会议纪要')
        click(window, 'agentSendButton')
        assert new_commands == [{'type': 'text', 'text': '另外帮我整理会议纪要', 'targetTurn': ''}]
        assert old_commands == []
        second._apply_state({'turnId': 'new-turn', 'status': '执行中', 'codexThreadId': 'new-thread'})
        click(window, 'agentTaskSwitcher')
        assert root.focused and not second.focused
        click(first, 'agentSendButton')
        assert old_commands == [{'type': 'steer', 'text': '原任务的修改草稿', 'targetTurn': 'old-turn'}]
        click(first, 'agentTaskSwitcher')
        click(window, 'agentSendButton')
        assert new_commands[-1] == {'type': 'cancel'} and len(old_commands) == 1

        second._apply_state({'taskOutcome': 'completed', 'status': '本轮结束', 'agent': '会议纪要已保存。'})
        QTest.qWait(50)
        assert second.taskCompleted and not root.taskCompleted and root.busy
        click(window, 'agentCloseButton')
        assert not window.isVisible() and first.isVisible() and root.busy
        assert root.sessions.active is root and second.feedback == '会议纪要已保存。'
    finally:
        overlay.close()
        root.close()


def test_duplicate_request_ids_and_shared_gestures_route_to_active_conversation(inline_ui, monkeypatch):
    owner = inline_ui[0]; root = owner.agent
    finish_fixture_voice(owner)
    root._visible = root._focused = True
    root._apply_state({'turnId': 'one', 'status': '执行中'})
    second = root.sessions.create(root)
    one, two = [], []
    monkeypatch.setattr(root, '_send', one.append)
    monkeypatch.setattr(second, '_send', two.append)
    overlay = AgentOverlay(root, owner); overlay.reduce_motion = True
    try:
        for session in (root, second):
            session._apply_state({'turnId': 'one' if session is root else 'two',
                                  'requests': [request('same-token')]})
        second.focusCard('same-token'); QTest.qWait(50)
        assert 'same-token' in overlay.windows
        assert root.sessions.window_key(second, 'same-token') in overlay.windows
        click(overlay.windows[root.sessions.window_key(second, 'same-token')], 'agentOption1')
        assert two == [{'type': 'choose', 'token': 'same-token', 'index': 1}] and not one

        second._apply_state({'turnId': 'two'})
        second.focusCard('task'); QTest.qWait(50)
        second.editorFocused('task', True)
        assert root.owns_input_focus()
        owner._disconnect_event = threading.Event()
        seen = []
        monkeypatch.setattr(second, '_submit_from_gesture', lambda key, name: seen.append((key, name)))
        assert root.filter_gesture(SimpleNamespace(name='swipe-up'), owner._disconnect_event)
        QTest.qWait(40)
        assert seen == [('task', 'swipe-up')]
        root.workspaceFocused(False); second.workspaceFocused(False)
        QTest.qWait(40)
        assert all(w.rootObject().property('compact') for k,w in overlay.windows.items() if k not in overlay.retiring)
    finally:
        overlay.close(); root.close()


@pytest.mark.parametrize('status,outcome', [('执行中', ''), ('本轮结束', 'completed'), ('已取消', 'interrupted')])
def test_editor_hint_stays_in_ime_without_replacing_task_state(inline_ui, monkeypatch, status, outcome):
    owner, bridge, *_ = inline_ui
    agent = owner.agent
    finish_fixture_voice(owner)
    owner._disconnect_event = threading.Event()
    state = {'status': status, 'taskOutcome': outcome, 'agent': '原任务输出保持不变。',
             'turnId': 'turn' if status == '执行中' else ''}
    agent._visible = agent._focused = True
    agent._apply_state(state)
    agent.editorFocused('task', True)
    monkeypatch.setattr(owner._inline_input, 'polish', lambda: False)
    before = deepcopy(agent.workspace.state)
    agent._input_gesture('swipe-down', 'task', owner._disconnect_event, time.monotonic(),
                         (owner._app_gestures.sentence(), owner._gesture_bindings))
    assert bridge.messages[-1]['type'] == 'input_notice'
    assert '没有可润色' in bridge.messages[-1]['text']
    assert agent.workspace.state == before and agent.status == status
    assert agent.feedback == state['agent'] and not agent.errorMessage
    assert agent.taskCompleted == (outcome == 'completed')


def test_new_task_waits_for_current_native_sentence(inline_ui):
    owner = inline_ui[0]; agent = owner.agent
    agent._apply_state({'turnId': 'turn'})
    assert agent.sessions.create(agent) is None and len(agent.sessions.members) == 1
    finish_fixture_voice(owner)
    assert agent.canAddTask


def test_closing_unselected_card_does_not_request_input_focus(inline_ui):
    owner = inline_ui[0]; first = owner.agent
    finish_fixture_voice(owner)
    first._visible = first._focused = True
    first._apply_state({'turnId': 'old'})
    second = first.sessions.create(first)
    first._apply_state({'taskOutcome': 'completed'})
    overlay = AgentOverlay(first, owner); overlay.reduce_motion = True
    focused = []
    first.focusRequested.connect(focused.append)
    try:
        second.focusCard('task'); QTest.qWait(60)
        click(overlay.windows['task'], 'agentCloseButton')
        assert not focused and not first.visible
        assert first.sessions.active is second and second.focused and second.visible
    finally:
        overlay.close(); first.close()


def test_main_entry_keeps_tasks_and_invalidates_pending_panel_focus(inline_ui, monkeypatch):
    from proximic_ring.ui.shared.main_window_activation import MainWindowActivation
    owner, _, _, main, _ = inline_ui
    first = owner.agent
    finish_fixture_voice(owner)
    first._visible = first._focused = True
    first._apply_state({'turnId': 'running-task', 'status': '执行中'})
    second = first.sessions.create(first)
    overlay = AgentOverlay(first, owner); overlay.reduce_motion = True
    activation = MainWindowActivation(main, overlay)
    requested, sent = [], []
    try:
        second.focusCard('task'); QTest.qWait(40)
        for session in first.sessions.members:
            session.workspace.drafts['task'] = '保留草稿'
            session.focusRequested.connect(requested.append)
            monkeypatch.setattr(session, '_send', sent.append)
            session._capture_pending = session._focus_after_input = True
        epochs = {session: session._focus_epoch for session in first.sessions.members}
        activation.show(); QTest.qWait(40)
        for session, epoch in epochs.items():
            session._focus_result(epoch, 'capture', {'status': 'remembered'})
            session._finish_focus_input()
        assert not requested and not sent
        assert not first.workspaceActive and main.isVisible()
        assert first.busy and first.visible and second.visible
        assert all(s.draft('task') == '保留草稿' for s in first.sessions.members)
        # The user can still select a card after explicitly opening the main UI.
        second.focusCard('task'); QTest.qWait(40)
        assert second.focused and first.sessions.active is second
    finally:
        activation.close(); overlay.close(); first.close(); main.hide()


def test_click_focus_and_drag_whole_stack_excludes_controls(inline_ui):
    owner = inline_ui[0]; root = owner.agent
    finish_fixture_voice(owner)
    root._visible = root._focused = True
    root._apply_state({'turnId': 'old', 'status': '执行中'})
    second = root.sessions.create(root)
    overlay = AgentOverlay(root, owner); overlay.reduce_motion = True
    try:
        overlay.focus(root.sessions.window_key(second, 'task')); QTest.qWait(100)
        first = overlay.windows['task']
        second_window = overlay.windows[root.sessions.window_key(second, 'task')]
        assert visual_child(first.rootObject(), 'agentRecordsButton') is None
        editor = visual_child(first.rootObject(), 'agentEditor')
        point = editor.mapToScene(QPointF(editor.width()/2, editor.height()/2))
        assert overlay.drag.hit_test(first, point) == 0
        QTest.mouseClick(first, Qt.LeftButton, Qt.NoModifier, point.toPoint())
        QTest.qWait(70)
        assert root.sessions.active is root and root.focused and not second.focused
        for name in ('agentNewTaskButton', 'agentLinkButton', 'agentSendButton'):
            button = visual_child(first.rootObject(), name)
            position = button.mapToScene(QPointF(button.width()/2, button.height()/2))
            assert overlay.drag.hit_test(first, position) == 0, name
        assert overlay.drag.hit_test(first, QPointF(8, 44)) == 1
        before = {key: window.geometry() for key, window in overlay.windows.items()}
        local = QPointF(8, 44)
        start = QPointF(first.mapToGlobal(local.toPoint()))
        def event(kind, position, global_position, button, buttons):
            QCoreApplication.sendEvent(first, QMouseEvent(kind, position, global_position,
                                                         button, buttons, Qt.NoModifier))
        event(QEvent.MouseButtonPress, local, start, Qt.LeftButton, Qt.LeftButton)
        delta = QPointF(-95, -8)
        event(QEvent.MouseMove, local+delta, start+delta, Qt.NoButton, Qt.LeftButton)
        assert overlay.drag.active
        event(QEvent.MouseButtonRelease, local, start+delta, Qt.LeftButton, Qt.NoButton)
        QTest.qWait(70)
        assert not overlay.drag.active
        for key, frame in before.items():
            moved = overlay.windows[key].geometry()
            assert moved.x() == frame.x()-95 and moved.y() == frame.y()-8
        retained = second_window.geometry()
        second._apply_state({'status': '准备就绪'})
        QTest.qWait(60)
        assert second_window.geometry() == retained
        assert not inline_ui[3].isVisible()
    finally:
        overlay.close(); root.close()
