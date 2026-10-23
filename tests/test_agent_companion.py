"""Companion dismissal preserves work; success has an explicit terminal result."""
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from test_agent_ui import finish_fixture_voice, inline_ui
from test_ui_shell import visual_child
from proximic_ring.ui.agent.agent_overlay import AgentOverlay


def test_companion_states_close_and_reopen_preserve_work(inline_ui, monkeypatch, tmp_path):
    owner = inline_ui[0]
    agent = owner.agent
    finish_fixture_voice(owner)
    sent = []
    monkeypatch.setattr(agent, '_send', sent.append)
    monkeypatch.setattr(agent, '_request_focus',
        lambda action, **_: agent._focus_result(agent._focus_epoch, action, {'status': 'remembered'}))
    agent._visible = agent._focused = True
    overlay = AgentOverlay(agent, owner)
    overlay.reduce_motion = True
    try:
        overlay.focus('task')
        QTest.qWait(50)
        window = overlay.windows['task']
        root = window.rootObject()
        root.setProperty('motionEnabled', False)
        close = visual_child(root, 'agentCloseButton')
        compact_close = visual_child(root, 'agentCompactCloseButton')

        def snapshot(name):
            QTest.qWait(60)
            assert window.grabWindow().save(str(tmp_path / f'{name}.png'))

        def click(item):
            assert item.isVisible() and item.isEnabled()
            point = item.mapToScene(QPointF(item.width()/2, item.height()/2)).toPoint()
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
            QTest.qWait(60)

        visual_child(root, 'agentEditor').setProperty('text', '保留我的草稿')
        snapshot('idle')
        assert close.isVisible() and not root.property('completed')
        click(close)
        assert not agent.visible and not window.isVisible()
        assert agent.draft('task') == '保留我的草稿' and not sent

        # A delayed capture result must not reopen a dismissed companion.
        agent._focus_result(agent._focus_epoch-1, 'capture', {'status': 'remembered'})
        assert not agent.visible
        agent.show()
        agent._apply_state({'turnId': 'turn', 'status': '执行中', 'agent': '正在整理资料…'})
        snapshot('running')
        assert close.isVisible() and not root.property('completed')
        agent.dismiss()
        assert not agent.visible and agent.busy and not sent
        agent.show()

        for outcome, label in [('interrupted', '已取消'), ('failed', '任务失败'), ('completed', '本轮结束')]:
            agent._apply_state({'taskOutcome': outcome, 'status': label, 'agent': '文档已整理完成，可继续补充。'})
            snapshot(outcome)
            assert bool(root.property('completed')) == (outcome == 'completed')
            assert close.isVisible()
        assert root.property('statusTitle') == '任务已完成'
        agent.workspaceFocused(False)
        snapshot('completed-compact')
        assert compact_close.isVisible()
        click(compact_close)
        assert not agent.visible and not window.isVisible()
        assert agent.taskCompleted and agent.feedback == '文档已整理完成，可继续补充。'
        assert agent.draft('task') == '保留我的草稿' and not sent

        agent.show()
        QTest.qWait(50)
        assert root.property('completed') and window.isVisible()
        agent._apply_state({'turnId': 'next', 'status': '执行中', 'taskOutcome': ''})
        QTest.qWait(50)
        assert not root.property('completed') and close.isVisible()
    finally:
        overlay.close()


def test_companion_cannot_hide_during_voice_input(inline_ui):
    owner = inline_ui[0]
    agent = owner.agent
    finish_fixture_voice(owner)
    agent._visible = agent._focused = True
    owner._inline_input._view.update(phase='listening')
    assert not agent.canDismiss
    agent.dismiss()
    assert agent.visible
    finish_fixture_voice(owner)
    assert agent.canDismiss
    agent.dismiss()
    assert not agent.visible
