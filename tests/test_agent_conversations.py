"""New/hide/link actions preserve per-conversation drafts and native ownership."""
from copy import deepcopy
import pytest
from PySide6.QtCore import QObject, QPointF, QTimer, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from test_agent_ui import finish_fixture_voice
from test_inline_ui import inline_ui
from test_agent_sessions import click
from proximic_ring.agent_workflow.ui.agent_overlay import AgentOverlay

TID = '00000000-0000-4000-8000-000000000001'


def test_recent_catalog_limits_and_sorts_by_last_activity_not_creation(tmp_path):
    from proximic_ring.agent_workflow.agent.recent_threads import list_recent_threads
    rows = [{'id': f'00000000-0000-4000-8000-{i:012d}', 'name': f'对话 {i}',
             'updatedAt': i, 'createdAt': 20-i, 'ephemeral': False} for i in range(1, 9)]
    rows += [dict(rows[-1], id='not-a-thread'),
             dict(rows[-1], ephemeral=True), dict(rows[-1], archived=True)]
    calls, closed = [], []
    class Catalog:
        def __init__(self, **kwargs): pass
        def initialize(self): pass
        def call(self, method, params, **kwargs):
            calls.append((method, params))
            return {'data': rows}
        def close(self): closed.append(True)
    recent = list_recent_threads(tmp_path, '/test/codex', rpc_factory=Catalog)
    assert [row['title'] for row in recent] == ['对话 8', '对话 7', '对话 6', '对话 5', '对话 4']
    assert calls[0][0] == 'thread/list' and calls[0][1]['sortKey'] == 'updated_at'
    assert calls[0][1]['sortDirection'] == 'desc' and closed == [True]


def prepare(inline_ui):
    owner = inline_ui[0]
    finish_fixture_voice(owner)
    agent = owner.agent
    agent._visible = agent._focused = True
    agent._memory_timer.stop()
    return owner, agent


def test_new_and_hide_are_available_after_completion_and_while_running(inline_ui, monkeypatch):
    owner, agent = prepare(inline_ui)
    sent = []
    monkeypatch.setattr(agent, '_send', sent.append)
    agent._apply_state({'taskOutcome': 'completed', 'agent': '已完成'})
    overlay = AgentOverlay(agent, owner)
    overlay.reduce_motion = True
    try:
        overlay.focus('task'); QTest.qWait(80)
        window = overlay.windows['task']
        assert window.rootObject().findChild(QObject, 'agentLinkButton').isVisible()
        click(window, 'agentNewTaskButton')
        target = agent.sessions.active
        assert target is not agent and not target.workspace.state
        agent._apply_state({'turnId': 'running', 'codexThreadId': TID, 'agent': '正在运行'})
        agent.edit('task', '保留草稿')
        click(window, 'agentCloseButton')
        assert not agent.visible and agent.busy and agent.draft('task') == '保留草稿'
        assert sent == []
    finally:
        overlay.close(); agent.close()


def test_link_commits_only_after_ready_and_reuses_existing_session(inline_ui, monkeypatch):
    _, source = prepare(inline_ui)
    source._auto_context_mode = 'off'  # Isolate conversation routing from asynchronous capture.
    from proximic_ring.agent_workflow.ui.agent_controller import AgentController
    sent = []
    monkeypatch.setattr(AgentController, '_send', lambda self, cmd: sent.append((self, cmd)))
    source._apply_state({'turnId': 'original-turn', 'codexThreadId': 'original-thread', 'agent': '原来的任务'})
    source.edit('task', '原对话草稿')
    before = deepcopy(source.workspace.state)
    picker = source.conversations
    picker._opened = True
    picker._rows = [{'id': TID, 'title': '最近对话', 'cwd': '/tmp'}]
    picker.select(TID)
    candidate = source.sessions.members[-1]
    assert source.visible and not candidate.visible and picker.connecting
    assert sent == [(candidate, {'type': 'attach_thread', 'threadId': TID})]
    assert not source.canAddTask
    candidate._apply_state({'threadAttachState': 'ready', 'codexThreadId': TID,
        'codexThreadTitle': '最近对话', 'connectionState': 'ready', 'codexSyncState': 'ready',
        'turnId': 'selected-turn', 'agent': '目标对话的回复'})
    assert not source.visible and candidate.visible and source.sessions.active is candidate
    assert source.workspace.state == before and source.draft('task') == '原对话草稿'
    assert candidate.draft('task') == ''
    candidate.edit('task', '继续这个任务'); candidate.submit('task')
    assert sent[-1] == (candidate, {'type': 'steer', 'text': '继续这个任务', 'targetTurn': 'selected-turn'})
    candidate.cancel()
    assert sent[-1] == (candidate, {'type': 'cancel'})
    source._visible = True
    picker._opened = True
    picker.select(TID)
    assert len(source.sessions.members) == 2 and source.sessions.active is candidate
    source.close()


def test_link_failure_keeps_current_conversation_and_draft(inline_ui, monkeypatch):
    _, source = prepare(inline_ui)
    from proximic_ring.agent_workflow.ui.agent_controller import AgentController
    monkeypatch.setattr(AgentController, '_send', lambda *args: None)
    source.edit('task', '不要丢失')
    picker = source.conversations
    picker._opened = True
    picker._rows = [{'id': TID, 'title': '旧对话', 'cwd': '/tmp'}]
    picker.select('not-in-catalog')
    assert len(source.sessions.members) == 1
    picker.select(TID)
    candidate = source.sessions.members[-1]
    candidate._apply_state({'threadAttachState': 'error', 'threadAttachError': '目标不存在'})
    assert source.visible and source.sessions.active is source and not candidate.visible
    assert source.draft('task') == '不要丢失' and picker.error == '目标不存在'
    assert not picker.connecting and picker.opened
    source.close()


def test_disconnected_current_thread_can_be_reconnected(inline_ui, monkeypatch):
    _, source = prepare(inline_ui)
    from proximic_ring.agent_workflow.ui.agent_controller import AgentController
    sent = []
    monkeypatch.setattr(AgentController, '_send', lambda self, command: sent.append(command))
    source._apply_state({'codexThreadId': TID, 'connectionState': 'disconnected'})
    source.conversations._rows = [{'id': TID, 'title': '原对话', 'cwd': '/tmp'}]
    source.conversations.select(TID)
    assert source.conversations.connecting
    assert sent == [{'type': 'attach_thread', 'threadId': TID}]
    source.close()


def test_recent_menu_preserves_card_and_clicks_link_without_sending_old_draft(inline_ui, monkeypatch, tmp_path):
    owner, source = prepare(inline_ui)
    from proximic_ring.agent_workflow.ui.agent_controller import AgentController
    sent = []
    monkeypatch.setattr(AgentController, '_send', lambda self, command: sent.append((self, command)))
    def refresh():
        source.conversations._accept(0, [
            {'id': TID, 'title': '整理更新消息', 'cwd': '/tmp/notes'},
            *({'id': f'00000000-0000-4000-8000-{i:012d}', 'title': title, 'cwd': '/tmp/project'}
              for i, title in enumerate(['浮窗样式调整', '手势快捷键排序', '浏览器扩展连接', 'Computer History 推荐'], 2)),
        ], '')
    monkeypatch.setattr(source.conversations, 'refresh', refresh)
    source._apply_state({'taskOutcome': 'completed', 'codexThreadTitle': '当前对话',
        'agent': '消息已经整理完成，可继续补充。\n已按时间汇总更新，并保留了来源。\n你可以在当前对话继续修改。'})
    source.edit('task', '只属于原对话的草稿')
    overlay = AgentOverlay(source, owner)
    overlay.reduce_motion = True
    try:
        overlay.focus('task'); QTest.qWait(300)
        window = overlay.windows['task']
        root = window.rootObject()
        before = window.geometry()
        assert window.grabWindow().save(str(tmp_path/'before-menu.png'))
        click(window, 'agentLinkButton')
        listing = root.findChild(QObject, 'agentConversationList')
        menu = root.findChild(QObject, 'agentConversationPicker')
        assert window.grabWindow().save(str(tmp_path/'menu-open.png'))
        assert listing.property('count') == 5
        assert root.findChild(QObject, 'agentConversationSearch') is None
        assert root.findChild(QObject, 'agentCancelButton') is None
        assert window.geometry() == before
        assert root.findChild(QObject, 'agentMessageViewport').isVisible()
        assert root.findChild(QObject, 'agentEditor').isVisible()
        assert menu.property('width') < window.width()
        assert not menu.property('dim')
        # Escape and an outside click close only the menu, preserving the draft.
        assert not source._editor_key
        QTest.keyClick(window, Qt.Key_Escape); QTest.qWait(80)
        assert not source.conversations.opened and not menu.property('visible')
        assert source.draft('task') == '只属于原对话的草稿'
        click(window, 'agentLinkButton')
        button = root.findChild(QObject, 'agentNewTaskButton')
        outside = button.mapToScene(QPointF(button.width()/2, button.height()/2))
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, outside.toPoint())
        QTest.qWait(80)
        assert not source.conversations.opened and sent == []
        assert len(source.sessions.members) == 1
        assert source.draft('task') == '只属于原对话的草稿'
        click(window, 'agentLinkButton')
        assert menu.property('visible') and not source._editor_key
        QTest.mouseMove(window, QPointF(window.width()-8, window.height()-8).toPoint())
        QTest.qWait(100)
        assert window.grabWindow().save(str(tmp_path/'recent-picker.png'))
        point = listing.mapToScene(QPointF(listing.width()/2, 16))
        assert overlay.drag.hit_test(window, point) == 0
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point.toPoint())
        QTest.qWait(40)
        assert sent[-1][1] == {'type': 'attach_thread', 'threadId': TID}
        assert source.draft('task') == '只属于原对话的草稿' and source.visible
        target = sent[-1][0]
        releases = []
        release = overlay.focus_session.release
        def observe_release(*args, **kwargs):
            releases.append((args, kwargs))
            return release(*args, **kwargs)
        monkeypatch.setattr(overlay.focus_session, 'release', observe_release)
        target._apply_state({'threadAttachState': 'ready', 'connectionState': 'ready',
            'codexThreadId': TID, 'codexThreadTitle': '整理更新消息', 'codexSyncState': 'ready',
            'taskOutcome': 'completed', 'agent': '已连接原来的对话。'})
        QTest.qWait(850)  # Let the old 750 ms native focus-return tail expire.
        assert target.visible and not source.visible and target.draft('task') == ''
        key = source.sessions.window_key(target, 'task')
        assert releases == []  # Replacing a card must not return focus to another app.
        assert target.focused and QGuiApplication.focusWindow() is overlay.windows[key]
        assert overlay.windows[key].rootObject().findChild(QObject, 'agentEditor').property('activeFocus')
        QTest.keyClick(overlay.windows[key], Qt.Key_A); QTest.qWait(30)
        assert target.draft('task') == 'a' and source.draft('task') == '只属于原对话的草稿'
        assert overlay.windows[key].grabWindow().save(str(tmp_path/'linked-conversation.png'))
        overlay._release_focus(); QTest.qWait(300)
        compact = overlay.windows[key]
        assert compact.rootObject().findChild(QObject, 'agentCompactNewTaskButton').isVisible()
        assert compact.rootObject().findChild(QObject, 'agentCompactLinkButton').isVisible()
        compact_controls = [compact.rootObject().findChild(QObject, name) for name in (
            'agentCompactCloseButton', 'agentCompactStatus', 'agentCompactTaskSwitcher',
            'agentCompactNewTaskButton', 'agentCompactLinkButton')]
        compact_controls = [item for item in compact_controls if item.isVisible()]
        assert all(item.width() == item.height() == 24 for item in compact_controls), [
            (item.objectName(), item.width(), item.height()) for item in compact_controls]
        assert len({item.mapToScene(QPointF(0, item.height()/2)).y() for item in compact_controls}) == 1
        assert compact.grabWindow().save(str(tmp_path/'compact-conversation.png'))
        # Reopening from a compact/short card still fits, with asynchronous data
        # and keyboard navigation; choosing the already connected ID only closes.
        def refresh_target():
            target.conversations._loading = True
            target.conversations.changed.emit()
            QTimer.singleShot(40, lambda: target.conversations._accept(0, source.conversations.rows, ''))
        monkeypatch.setattr(target.conversations, 'refresh', refresh_target)
        click(compact, 'agentCompactLinkButton')
        QTest.qWait(80)
        target_root = compact.rootObject()
        target_list = target_root.findChild(QObject, 'agentConversationList')
        assert target.conversations.opened and not target_root.property('compact')
        bottom = target_list.mapToScene(QPointF(0, target_list.height())).y()
        assert bottom <= compact.height() - 8
        assert compact.grabWindow().save(str(tmp_path/'short-card-menu.png'))
        QTest.keyClick(compact, Qt.Key_Down)
        assert target_list.property('currentIndex') == 1
        QTest.keyClick(compact, Qt.Key_Up)
        assert target_list.property('currentIndex') == 0
        QTest.keyClick(compact, Qt.Key_Return); QTest.qWait(50)
        assert not target.conversations.opened and len(sent) == 1
    finally:
        overlay.close(); source.close()


@pytest.mark.parametrize('focus_elsewhere', ['outside', 'another_card'])
def test_link_ready_preserves_focus_changed_while_connecting(inline_ui, monkeypatch, focus_elsewhere):
    _, source = prepare(inline_ui)
    from proximic_ring.agent_workflow.ui.agent_controller import AgentController
    monkeypatch.setattr(AgentController, '_send', lambda *args: None)
    other = source.sessions.create(source, activate=False)
    picker = source.conversations
    picker._opened = True
    picker._rows = [{'id': TID, 'title': '旧对话', 'cwd': '/tmp'}]
    picker.select(TID)
    target = source.sessions.members[-1]
    requested = []
    target.focusRequested.connect(requested.append)
    if focus_elsewhere == 'another_card':
        other._visible = True
        other.focusCard('task')
    else:
        source.workspaceFocused(False)
    target._apply_state({'threadAttachState': 'ready', 'connectionState': 'ready',
        'codexThreadId': TID, 'codexSyncState': 'ready', 'taskOutcome': 'completed'})
    assert target.visible and not source.visible and not target.focused and not requested
    if focus_elsewhere == 'another_card':
        assert source.sessions.active is other and other.focused
    else:
        assert source.sessions.active is target and not source.sessions.focused
    source.close()


def test_compact_header_controls_align_and_close_without_focusing(inline_ui, tmp_path):
    owner, source = prepare(inline_ui)
    source._apply_state({'taskOutcome':'completed','agent':'已整理完成，可继续补充。'})
    second = source.sessions.create(source)
    overlay = AgentOverlay(source, owner); overlay.reduce_motion = True
    focused=[]
    try:
        overlay.focus(source.sessions.window_key(second,'task')); QTest.qWait(300)
        overlay._release_focus(); QTest.qWait(300)
        window=overlay.windows['task'];root=window.rootObject()
        assert root.findChild(QObject, 'agentCardBody').property('opacity') == 0
        names=('agentCompactCloseButton','agentCompactStatus','agentCompactTaskSwitcher',
               'agentCompactNewTaskButton','agentCompactLinkButton')
        items=[root.findChild(QObject,name) for name in names]
        assert all(i.isVisible() and i.width()==i.height()==24 for i in items)
        assert len({i.mapToScene(QPointF(0,i.height()/2)).y() for i in items})==1
        assert window.grabWindow().save(str(tmp_path/'compact-header.png'))
        source.focusRequested.connect(focused.append)
        point=items[0].mapToScene(QPointF(12,12))
        assert overlay.drag.hit_test(window,point)==2
        QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,point.toPoint());QTest.qWait(80)
        assert not source.visible and not focused
    finally:
        overlay.close();source.close()
