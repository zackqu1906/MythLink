"""Selection ink must point at the action the shared state will commit."""
import math
from pathlib import Path

import pytest

from proximic_ring.touchpad.stroke.stroke_contact_intent import classify_contact


@pytest.fixture
def trace_modules(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'tools'))
    import stroke_trial_menu
    import stroke_trial_trace
    return stroke_trial_menu, stroke_trial_trace


def test_snapshot_uses_true_arming_frame_and_keeps_last_sample(trace_modules):
    menu, _ = trace_modules
    landing = [(30 + 3.5*i/12, 40.) for i in range(13)]
    held = landing + [landing[-1]]*100
    points = held + [(33.5 + i*.1, 40.) for i in range(1, 1001)]
    state = menu.trial_contact(points)
    assert state['hold_ms'] == 395
    assert state['display']['origin'] == list(points[79])
    assert state['display']['points'][0] == points[79]
    assert state['display']['points'][-1] == points[-1]
    assert len(state['display']['points']) <= 201
    # Display metadata must never change the release action or sensor path.
    assert menu.trial_contact(points, finished=True) == classify_contact(points, finished=True)
    assert len(points) == 1113


def test_motion_then_pause_uses_new_origin_without_showing_preceding_ink(trace_modules):
    menu, _ = trace_modules
    points = [(float(i), 0.) for i in range(13)] + [(12., 0.)]*68
    held = menu.trial_contact(points)
    assert held['phase'] == 'selecting' and held['reason'] == 'pause-hold'
    assert held['display']['origin'] == [12., 0.]
    assert all(point == (12., 0.) for point in held['display']['points'])
    points += [(12., -10.)]
    state = menu.trial_contact(points)
    assert state['display']['points'][-1] == points[-1] and state['direction'] == 'up'
    assert menu.trial_selection(menu.trial_contact(points, finished=True))['target'] == '。'


@pytest.mark.parametrize('size', [(1440, 810), (1280, 640), (640, 900), (900, 400)])
def test_origin_and_endpoint_follow_real_cells_for_all_angles(trace_modules, size):
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtWidgets import QApplication, QWidget, QPushButton, QGridLayout
    menu, trace = trace_modules
    app = QApplication.instance() or QApplication(['selection-trace', '-platform', 'offscreen'])
    host = QWidget()
    grid = QGridLayout(host)
    grid.setContentsMargins(17, 23, 31, 19)
    grid.setSpacing(11)
    overlay = trace.TrialTraceView()
    overlay.setParent(host)
    overlay.setMinimumSize(0, 0)
    overlay.set_overlay_mode(True)
    for direction, row, col in [(d, r, c) for d, r, c, _ in menu.CANDIDATE_CELLS] + [
            (d, 0, col) for d, col, *_ in menu.PUNCTUATION_CELLS]:
        button = QPushButton(direction)
        button.setMinimumSize(0, 0)
        grid.addWidget(button, row, col)
        overlay.direction_buttons[direction] = button
    host.resize(*size)
    host.show()
    app.processEvents()
    overlay.setGeometry(host.rect())
    overlay.raise_()
    held = [(18., -7.)]*101
    center = overlay.direction_buttons['center']
    expected_origin = QRectF(center.geometry()).center()
    try:
        for angle in range(360):
            for distance in (0., 2.999, 3., 30., 3000.):
                point = (18 + math.cos(math.radians(angle))*distance,
                         -7 + math.sin(math.radians(angle))*distance)
                state = menu.trial_contact(held + [point])
                overlay.set_selection_state(state)
                path, origin = overlay.selection_geometry()
                assert origin == expected_origin and path[0] == origin
                target = overlay.direction_buttons[state['direction'] or 'center']
                assert target.geometry().contains(path[-1].toPoint())
                assert overlay.testAttribute(Qt.WA_TransparentForMouseEvents)
        # Appending/reversing a path must never move its earlier points.
        prefix = held + [(22., -7.), (28., -7.)]
        overlay.set_selection_state(menu.trial_contact(prefix))
        before, origin = overlay.selection_geometry()
        overlay.set_selection_state(menu.trial_contact(prefix + [(8., -7.)]))
        after, new_origin = overlay.selection_geometry()
        assert after[:-1] == before and origin == new_origin
        # A late handwriting preview cannot move selection's live endpoint.
        overlay.show_trace([(1000., 0.), (2000., 2000.)])
        assert overlay.selection_geometry() == (after, new_origin)
        overlay.set_selection_state(menu.trial_contact(prefix + [held[0]]))
        assert overlay.selection_geometry()[0][-1] == origin
        overlay.set_selection_state(dict(phase='idle'))
        assert overlay.selection_geometry() == ([], None)
    finally:
        host.close()
        app.processEvents()
