"""Mouse events on cards containing a real Chromium surface, not a text stub."""
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtQuick import QQuickItem

from test_agent_rich_text import rich_window, web_for
from test_inline_ui import inline_ui


def test_mouse_press_with_loaded_rich_content_never_wraps_private_children(rich_window, monkeypatch):
    agent, window = rich_window
    agent._apply_state({'turnId': 'running', 'agent': '# Result\n\n**Clickable card**'})
    web_for(window)
    assert agent.focused
    calls = []
    # A deterministic guard for the conversion that crashed inside PySide.
    monkeypatch.setattr(QQuickItem, 'childItems', lambda self: calls.append(self) or [])
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(120, 22))
    assert window.isVisible()
    assert calls == []


def test_rich_card_cycles_and_pointer_lifecycle(inline_ui):
    from proximic_ring.agent_workflow.ui.agent_overlay import AgentOverlay
    from proximic_ring.diagnostics.agent_card_check import exercise_cards
    from test_agent_ui import finish_fixture_voice
    owner = inline_ui[0]
    finish_fixture_voice(owner)
    overlay = AgentOverlay(owner.agent, owner)
    overlay.reduce_motion = True
    try:
        report = exercise_cards(overlay, cycles=6)
        assert report['rich_load_unload_cycles'] == 6
    finally:
        overlay.close()
