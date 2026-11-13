"""Render the integrated writing workspace at real logical viewport sizes."""
import os
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QUrl, QPointF
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest

from test_inline_ui import inline_ui
from test_ui_touchpad import prepare, Output


@pytest.mark.parametrize('size', [(1280, 900), (1024, 600), (375, 812)])
def test_workspace_renders_without_horizontal_overflow(inline_ui, size, tmp_path):
    c, _, _, _, event = inline_ui
    c._utterance_active = False
    c._pending_inline_audio_start = None
    c._interaction_state = 'idle'
    event(phase='interrupted', ready=True, composing=False, writing=False,
          edit_requested=False, awaiting_readback=False)
    tp, source = prepare(c)
    tp.stroke_output_factory = Output
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty('previewController', c)
    qml_dir = Path(__file__).resolve().parents[1]/'src/proximic_ring/ui/qml'
    engine.loadData(b'''import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import "."
ApplicationWindow {
    visible: true
    width: 1280; height: 900
    FigmaTheme { id: theme }
    font.family: theme.family
    color: theme.background
    Material.accent: theme.primary
    Material.primary: theme.primary
    TouchpadPage { anchors.fill: parent; anchors.margins: 16; controller: previewController }
}''', QUrl.fromLocalFile(str(qml_dir/'WorkspacePreview.qml')))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    try:
        window.resize(*size)
        window.requestActivate()
        QTest.qWait(400)  # Bundled CJK fonts finish loading before measuring.
        tp.setLocalStrokeInput(True)
        tp.addStroke('p'); tp.addStroke('s')
        QTest.qWait(100)
        viewport = window.findChild(QObject, 'touchpadViewport')
        assert viewport.property('contentWidth') <= viewport.width()
        for name in ('strokeSearchShell', 'strokeCandidateRow', 'strokeTools', 'strokeFuzzyStrengthRow'):
            item = window.findChild(QObject, name)
            origin = item.mapToScene(QPointF())
            assert origin.x() >= 0, name
            assert origin.x()+item.width() <= window.width()+1, name
        for index in range(6):
            image = window.findChild(QObject, 'strokeCandidateGlyph'+str(index))
            if image is not None:
                assert abs(image.width()-image.height()) < 1
        # Reserve the external tab even on narrow screens; revealing it cannot shift the grid.
        menu = window.findChild(QObject, 'strokeSelectionMenu')
        geometry = (menu.mapToScene(QPointF()), menu.width(), menu.height())
        tp._selection_state = dict(phase='selecting', direction='left', clear_armed=True, clear_progress=1)
        tp.selectionChanged.emit(); QTest.qWait(30)
        pull = window.findChild(QObject, 'strokeClearPull')
        assert pull.isVisible() and pull.mapToScene(QPointF()).x() >= 0
        assert pull.mapToScene(QPointF(pull.width(), 0)).x() <= window.width()
        assert (menu.mapToScene(QPointF()), menu.width(), menu.height()) == geometry
        output = Path(os.environ.get('MYTHLINK_SCREENSHOT_DIR', str(tmp_path)))
        output.mkdir(parents=True, exist_ok=True)
        assert window.grabWindow().save(str(output/f'workspace-{size[0]}x{size[1]}.png'))
        viewport.setProperty('contentY', max(0, viewport.property('contentHeight')-viewport.height()))
        QTest.qWait(100)
        assert window.grabWindow().save(str(output/f'workspace-{size[0]}x{size[1]}-bottom.png'))
    finally:
        window.close()
        engine.deleteLater()
        QTest.qWait(30)


def test_local_contact_actions_share_the_trial_menu_and_do_not_send_native_commits(inline_ui):
    from test_touchpad_page_layout import open_trial
    c, bridge, window, tp, source = open_trial(inline_ui, (1024, 700))
    window.findChild(QObject, 'strokeTestEditor').forceActiveFocus()
    tp.setLocalStrokeInput(True)
    tp.setLocalStrokeFocus(True)
    tp.toggleLocalStrokeTrial(); QTest.qWait(20)
    source.future.set_result(None)
    # Readiness passes through two queued callbacks as the output switches modes.
    for _ in range(50):
        if tp.strokeSession.phase == 'running':
            break
        QTest.qWait(20)
    assert tp.strokeSession.local and tp.strokeSession.phase == 'running'
    committed = []
    tp.localCharacterCommitted.connect(committed.append)
    try:
        tp._candidates = list('一二三四五六')
        assert tp.candidatePageSize == 5
        run = tp._run
        flashed = []
        tp.selectionCommitted.connect(flashed.append)
        tp._receive(run, 'selection', (run.output, {'decision': {'phase': 'selecting', 'direction': 'down-right'}}))
        assert committed == ['五'] and flashed == ['down-right']
        tp.insertPunctuation('。')
        assert committed == ['五', '。']
        # Two-finger left deletes: a pending stroke first, then a typed character.
        deleted = []
        tp.localBackspaceRequested.connect(lambda: deleted.append(True))
        tp.addStroke('h'); tp.addStroke('s')
        tp._receive(run, 'selection', (run.output, {'decision': {'phase': 'selecting', 'direction': 'left'}}))
        assert tp._code == 'h' and not deleted
        tp.clearStrokes()
        tp._receive(run, 'selection', (run.output, {'decision': {'phase': 'selecting', 'direction': 'left'}}))
        assert deleted == [True] and flashed[-1] == 'left'
        # Releasing in the centre cancels instead of committing.
        tp._receive(run, 'selection', (run.output, {'decision': {'phase': 'selecting', 'direction': None}}))
        assert committed == ['五', '。']
        assert not any(m['type'] == 'stroke_commit' for m in bridge.messages)
    finally:
        tp.close()
        QTest.qWait(20)


def test_local_trial_fades_real_ink_and_draws_hold_selection_from_sensor_tuples(inline_ui):
    from test_touchpad_page_layout import open_trial
    from proximic_ring.touchpad.stroke.stroke_common import common_characters
    c, bridge, window, tp, source = open_trial(inline_ui, (1024, 700))
    window.findChild(QObject, 'strokeTestEditor').forceActiveFocus()
    tp.setLocalStrokeInput(True)
    tp.setLocalStrokeFocus(True)
    tp.toggleLocalStrokeTrial(); QTest.qWait(20)
    source.future.set_result(None); QTest.qWait(20)
    seen = []
    tp.strokeTraceChanged.connect(lambda points, finished: seen.append((points, finished)))
    try:
        run = tp._run
        ink = [(float(i), float(i)) for i in range(20)]
        tp._receive(run, 'trace', (run.output, (ink, True)))
        tp._receive(run, 'trace', (run.output, ([], True)))
        # Finished ink is handed to the view to fade; sensor cleanup does not erase it.
        assert seen == [(seen[0][0], True)] and len(seen[0][0]) == len(ink)
        trail = window.findChild(QObject, 'strokeTrailDisplay')
        assert trail.property('standard') is True and trail.property('points')
        tp.addStroke('p'); tp.addStroke('s')
        assert tp._candidates and set(tp._candidates) <= common_characters()
        points = [(0., 0.)] + [(i * .8, i * .8) for i in range(1, 12)]
        tp._receive(run, 'selection_state', (run.output, dict(phase='selecting', progress=100,
            direction='down-right', display=dict(origin=(0., 0.), points=points))))
        QTest.qWait(30)
        assert tp.selectionState['display']['points'][-1] == [8.8, 8.8]
        assert window.findChild(QObject, 'strokeSelectionHint').property('text') == '双指选字 · 抬手确认'
        assert window.findChild(QObject, 'strokeSelectionMenu').property('activeDirection') == 'down-right'
    finally:
        tp.close()
        QTest.qWait(20)


@pytest.mark.parametrize('double', [False, True])
def test_local_double_click_returns_to_mouse_and_takes_back_its_first_tap(inline_ui, double, monkeypatch):
    import time
    from test_touchpad_page_layout import open_trial
    c, bridge, window, tp, source = open_trial(inline_ui, (1024, 700))
    monkeypatch.setattr(type(tp), 'raw2Supported', property(lambda _: False))
    editor = window.findChild(QObject, 'strokeTestEditor')
    editor.forceActiveFocus()
    tp.setLocalStrokeInput(True)
    tp.setLocalStrokeFocus(True)
    tp.toggleLocalStrokeTrial(); QTest.qWait(20)
    source.future.set_result(None); QTest.qWait(20)
    try:
        run = tp._run
        tp.addStroke('h')
        first = tp.strokeCandidates[0]
        tap = dict(points=[(0., 0.)]*10, intent_decision=dict(action='tap'))
        tp._receive(run, 'contact_tap', (run.output, tap))
        QTest.qWait(20)
        assert editor.property('text') == first + ' '  # Default single tap confirms then inserts space.
        if double:
            tp._receive(run, 'double_click', (run.output, None, time.monotonic()))
            QTest.qWait(30)
            assert editor.property('text') == ''
            assert not tp.strokeSession.engaged and tp.inputMode == 'pointer'
        else:
            assert tp.strokeSession.engaged and tp.inputMode == 'stroke'
    finally:
        tp.close()
        QTest.qWait(20)


def test_main_window_contains_the_integrated_workspace(inline_ui, tmp_path):
    from test_touchpad_page_layout import open_trial
    _, _, window, tp, _ = open_trial(inline_ui, (1280, 820))
    tp.addStroke('p'); tp.addStroke('s')
    QTest.qWait(100)
    assert window.findChild(QObject, 'strokeCompositionPanel').property('visible')
    output = Path(os.environ.get('MYTHLINK_SCREENSHOT_DIR', str(tmp_path)))
    output.mkdir(parents=True, exist_ok=True)
    assert window.grabWindow().save(str(output/'main-window-1280x820.png'))
