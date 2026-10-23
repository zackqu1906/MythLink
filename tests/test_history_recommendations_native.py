"""Opt-in regression for the compact Cocoa panel and explicit click dispatch."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = r'''
import os, sys, time, json, subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, 'tests')
from Foundation import NSBundle
# Match the packaged helper's accessory launch before Qt can activate its host.
NSBundle.mainBundle().infoDictionary()["LSUIElement"] = True
import AppKit
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from test_history_recommendations import Agent, FakeService, snapshot
from test_history_codex_targets import target_snapshot, THREAD, TITLE, TASK_TITLE
from proximic_ring.platforms.macos.mac_app_activation import configure_app_activation
from proximic_ring.agent_workflow.ui.history_recommendation_controller import HistoryRecommendationController
from proximic_ring.agent_workflow.ui.history_recommendation_overlay import HistoryRecommendationOverlay, placement
from PySide6.QtGui import QCursor, QGuiApplication

app = QApplication([])
app.setQuitOnLastWindowClosed(False)
configure_app_activation(background=True)
QTest.qWait(300)
agent = Agent()
controller = HistoryRecommendationController(agent, cache_path=Path(sys.argv[1])/'unused-cache',
    service_factory=FakeService, overlay_factory=HistoryRecommendationOverlay)
controller._latest = snapshot()
cover = subprocess.Popen([sys.executable, '-c', """
from PySide6.QtWidgets import QApplication, QWidget
app = QApplication([])
window = QWidget()
window.setWindowTitle('Disposable recommendation focus check')
window.setGeometry(40, 70, 320, 180)
window.show()
window.activateWindow()
app.exec()
"""], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    deadline = time.monotonic() + 5
    while (AppKit.NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier() != cover.pid
           and time.monotonic() < deadline):
        QTest.qWait(50)
    foreground = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier()
    assert foreground == cover.pid, "disposable foreground fixture did not activate"
    pointer = QCursor.pos()
    screen = QGuiApplication.screenAt(pointer) or QGuiApplication.primaryScreen()
    expected = placement(pointer, screen.availableGeometry())
    with patch('proximic_ring.agent_workflow.ui.history_recommendation_overlay.QCursor',
               SimpleNamespace(pos=lambda: pointer)):
        controller.open()
    QTest.qWait(100)
    native = controller._overlay.native
    frame = native.panel.frame()
    assert frame.size.width == 300 and frame.size.height == 140, frame
    assert frame.origin.x == expected.x(), (frame, expected)
    assert frame.origin.y == AppKit.NSScreen.screens()[0].frame().size.height - expected.y() - 140, (frame, expected)
    assert AppKit.NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier() == foreground
    assert not native.panel.canBecomeKeyWindow() and not native.panel.canBecomeMainWindow()
    assert native.surface.layer().borderWidth() == 0
    for view in [native.surface, *native.surface.subviews()]:
        assert view.focusRingType() == AppKit.NSFocusRingTypeNone
    controller.step(1)
    assert native.counter.stringValue() == '2 / 4'
    assert native.title.stringValue() == controller.title
    selected_plan = controller.plan
    assert native.plan.stringValue() == selected_plan
    assert native.plan.toolTip() == selected_plan
    native.execute_button.performClick_(None)
    app.processEvents()
    assert len(agent.commands) == 1 and '记录 1' in agent.commands[0]['text']
    assert selected_plan in agent.commands[0]['text']
    assert not controller.visible and not native.panel.isVisible()
    assert AppKit.NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier() == foreground
    controller._latest = target_snapshot()
    opened = []
    controller._codex_opener = lambda tid: opened.append(tid) or True
    controller.open()
    assert native.execute_button.title() == '回到任务'
    assert native.title.stringValue() == TASK_TITLE
    assert native.execute_button.toolTip() == TITLE
    native.execute_button.performClick_(None)
    app.processEvents()
    assert opened == [THREAD] and len(agent.commands) == 1
    assert not controller.visible and not native.panel.isVisible()
    controller._latest = snapshot()
    controller.open()
    QTest.qWait(50)
    assert native.execute_button.toolTip() is None
    assert native.panel.frame().size.width == 300
    native.dismiss_button.performClick_(None)
    assert not native.panel.isVisible() and len(agent.commands) == 1
    controller.open()
    QTest.qWait(4000)
    moved = AppKit.NSEvent.mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure_(
        AppKit.NSEventTypeMouseMoved, AppKit.NSMakePoint(20, 70), 0, time.monotonic(),
        native.panel.windowNumber(), None, 0, 0, 0)
    native.panel.sendEvent_(moved)
    QTest.qWait(3500)
    assert controller.visible and native.panel.isVisible()  # pointer input renewed the seven seconds
    QTest.qWait(3700)
    assert not controller.visible and not native.panel.isVisible()
    final_foreground = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier()
    assert final_foreground == foreground, (foreground, final_foreground, os.getpid())
    assert len(agent.commands) == 1
    print('native borderless 300x140 panel, idle expiry, pointer renewal, no focus change: passed')
finally:
    controller.close()
    cover.terminate()
    cover.wait(timeout=5)
'''


@pytest.mark.skipif(sys.platform != "darwin" or os.environ.get("PROXIMIC_TEST_HISTORY_HUD") != "1",
                    reason="opt-in native panel check with synthetic suggestions and fake Agent")
def test_native_panel_stays_compact_and_nonactivating(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, QT_QPA_PLATFORM="cocoa", QT_QUICK_CONTROLS_STYLE="Basic", PYTHONPATH=str(root / "src"))
    result = subprocess.run([sys.executable, "-c", SCRIPT, str(tmp_path)], cwd=root,
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
