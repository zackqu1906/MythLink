"""Exercise real wheel scrolling and the trial button with a fake Ring output."""
from pathlib import Path
from types import SimpleNamespace
import os

import pytest
from PySide6.QtCore import QObject, QPoint, QPointF, Qt
from PySide6.QtTest import QTest

from test_inline_ui import inline_ui
from test_ui_touchpad import Output, prepare


def open_trial(inline_ui, size):
    c, bridge, _, root, event = inline_ui
    c._utterance_active = False
    c._pending_inline_audio_start = None
    c._interaction_state = 'idle'
    event(phase='interrupted', ready=True, composing=False, writing=False,
          edit_requested=False, awaiting_readback=False)
    tp, source = prepare(c)
    tp.stroke_output_factory = Output
    root.resize(*size)
    root.setProperty('currentPage', 3)
    root.findChild(QObject, 'touchpadPage').setProperty('section', 'stroke')
    root.show()
    root.requestActivate()
    QTest.qWait(400)  # Wait for the bundled CJK fonts before measuring scroll bounds.
    return c, bridge, root, tp, source


@pytest.mark.parametrize('size', [(1440, 940), (940, 700)])
def test_wheel_reaches_all_trial_controls_without_rebounding(inline_ui, tmp_path, size):
    _, _, root, _, _ = open_trial(inline_ui, size)
    viewport = root.findChild(QObject, 'touchpadViewport')
    point = viewport.mapToScene(QPointF(viewport.width() - 30, viewport.height() / 2))
    maximum = max(0, viewport.property('contentHeight') - viewport.height())
    if size[1] == 700:
        assert maximum > 0
    previous = 0
    for _ in range(45):  # The square selection menu makes the trial page taller.
        QTest.wheelEvent(root, point, QPoint(0, -120))
        QTest.qWait(20)
        current = viewport.property('contentY')
        assert current >= previous - 1
        previous = current
    QTest.qWait(150)
    assert abs(viewport.property('contentY') - maximum) < 1
    assert abs(viewport.property('contentY') - previous) < 1
    help_text = root.findChild(QObject, 'strokeTrialHelp')
    bottom = help_text.mapToScene(QPointF(0, help_text.height())).y()
    viewport_bottom = viewport.mapToScene(QPointF(0, viewport.height())).y()
    assert bottom < viewport_bottom
    tools = root.findChild(QObject, 'strokeTools')
    assert tools.mapToScene(QPointF()).y() >= viewport.mapToScene(QPointF()).y()
    output = Path(os.environ.get('MYTHLINK_SCREENSHOT_DIR', str(tmp_path)))
    output.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(output / f'touchpad-bottom-{size[0]}.png'))


@pytest.mark.parametrize('platform', ['win32', 'darwin'])
@pytest.mark.parametrize('already_running', [False, True])
def test_trial_button_starts_local_strokes_and_stops_without_double_click(
        inline_ui, monkeypatch, platform, already_running):
    import proximic_ring.ui.touchpad.touchpad_controller as module
    monkeypatch.setattr(module, 'sys', SimpleNamespace(platform=platform))
    c, bridge, root, tp, source = open_trial(inline_ui, (940, 700))
    if already_running:
        tp.start(); QTest.qWait(20)
        source.future.set_result(None); QTest.qWait(20)
    viewport = root.findChild(QObject, 'touchpadViewport')
    button = root.findChild(QObject, 'strokeTrialToggleButton')
    viewport.setProperty('contentY', 180)
    QTest.qWait(20)
    assert button.property('enabled') and button.property('text') == '开始试写'
    point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
    QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, point)
    QTest.qWait(30)
    assert len(source.starts) == 1
    if not already_running:
        source.future.set_result(None); QTest.qWait(30)
    assert tp.inputMode == 'stroke' and tp.strokeSession.phase == 'running'
    assert tp.strokeSession.local and tp._local_stroke_input
    assert not c._recognition_event.is_set()
    assert button.property('text') == '停止试写'
    tp.addStroke('h')
    char = tp.strokeCandidates[0]
    tp.selectCandidate(0)
    assert root.findChild(QObject, 'strokeTestEditor').property('text') == char
    assert not any(m['type'] == 'stroke_begin' for m in bridge.messages)
    point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
    QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, point)
    QTest.qWait(30)
    assert not tp.active and not tp.strokeSession.engaged and source.stops == 1
    assert c._recognition_event.is_set()
