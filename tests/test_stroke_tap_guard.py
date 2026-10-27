import json
import threading
import time
import pytest

from proximic_ring.touchpad.stroke.stroke_tap_guard import TapConfirmationGuard
from proximic_ring.touchpad.touchpad_strokes import TouchpadStrokeOutput
from ring_python_sdk.touchpad import TouchpadContact, TouchpadMove, TouchpadClick, TouchpadClickVerdict


def test_guard_preserves_light_tap_and_rejects_motion_and_recent_commit():
    clock = [1.]
    guard = TapConfirmationGuard(clock=lambda: clock[0])
    assert guard.decide({'points': [(0, 0), (.1, 0)]})['allowed']
    assert guard.decide({'points': [(0, 0), (1, 0), (0, 0)]})['reason'] == 'moving-contact'
    assert guard.decide({'points': [(0, 0), (1.5, 0)]})['reason'] == 'moving-contact'
    guard.committed()
    clock[0] += .1
    assert guard.decide({})['reason'] == 'commit-cooldown'
    assert guard.decide({'points': [(0, 0), (3, 0)]}, enabled=False)['allowed']
    clock[0] = 1.25
    assert guard.decide({})['allowed']
    guard.committed()
    guard.reset()
    assert guard.decide({})['allowed']
    assert guard.decide({'points': [(0, 0), (float('nan'), 1)]})['reason'] == 'invalid-geometry'
    assert guard.decide({'points': [(0,), (1, 2)]})['reason'] == 'invalid-geometry'


@pytest.mark.parametrize('move_count', [3, 10])
def test_opt_in_receives_click_contact_once_and_does_not_publish_handwriting(move_count):
    contacts, strokes, taps, traces = [], [], [], []
    ready, ended = threading.Event(), threading.Event()
    output = TouchpadStrokeOutput(connection=threading.Event(), on_ready=ready.set,
        on_end=lambda *_: ended.set(), on_stroke=strokes.append, on_tap=lambda: taps.append(True),
        on_tap_contact=contacts.append, on_trace=lambda points, finished: traces.append(points))
    output.start()
    assert ready.wait(1)
    output.activate()
    try:
        now = time.monotonic()
        output.submit(TouchpadContact('down', 10, now))
        end_step = 10 + move_count
        for step in range(10, end_step):
            output.submit(TouchpadMove(1, 0, .99, step, now))
        output.submit(TouchpadContact('up', end_step, now))
        output.submit(TouchpadClick(end_step, now))
        output.submit(TouchpadClickVerdict(10, end_step, True, now))
        deadline = time.monotonic() + 1
        while not contacts and time.monotonic() < deadline:
            time.sleep(.005)
        assert len(contacts) == 1
        assert contacts[0]['start_step'] == 10 and contacts[0]['end_step'] == end_step
        assert contacts[0]['duration_ms'] == move_count * 5
        assert len(contacts[0]['points']) == move_count
        assert contacts[0]['geometry_valid'] is (move_count >= 5)
        assert not strokes and not taps
        assert TapConfirmationGuard().decide(contacts[0])['reason'] == 'moving-contact'
        output.submit(TouchpadClick(end_step, time.monotonic()))
        output.submit(TouchpadClickVerdict(10, end_step, True, time.monotonic()))
        time.sleep(.03)
        assert len(contacts) == 1
        assert not output.pending_contact_details
    finally:
        output.stop()
        assert ended.wait(1)


def test_trial_click_protection_keeps_code_logs_decisions_and_direction_bypasses(tmp_path, monkeypatch):
    from test_complete_stroke_input import load_window, prepare
    app, window = load_window(tmp_path, monkeypatch)
    try:
        prepare(window)
        now = [1.]
        window.tap_guard.clock = lambda: now[0]
        window.target_input.setPlainText('小你你')
        window.start_trial()
        assert not window.tap_guard_toggle.isEnabled()
        window.add_manual('s')
        code, evidence = window.code, list(window.evidence)
        window.accept_sdk_tap(dict(points=[(0, 0), (3, 0)], start_step=10, end_step=20, duration_ms=50))
        assert not window.editor.text()
        assert window.code == code and window.evidence == evidence
        assert window.trace_view.points == [(0, 0), (3, 0)]
        assert not any(e['kind'] == 'commit' for e in window.trial.events)
        window.candidates = ['小']
        window.accept_sdk_tap({'points': [(0, 0), (.2, 0)]})
        assert window.editor.text() == '小' and not window.trace_view.points
        now[0] += .1
        window.candidates = ['你']
        window.accept_sdk_tap({'points': []})
        assert window.editor.text() == '小'
        window.commit(0)  # Explicit direction/button confirmation bypasses cooldown.
        assert window.editor.text() == '小你'
        now[0] += .3
        window.candidates = ['你']
        window.accept_sdk_tap({'points': []})
        report = json.loads(window.report_path.read_text(encoding='utf-8'))
        assert report['completed']
        assert report['tap_confirmations_allowed'] == 2
        assert report['tap_confirmations_suppressed'] == 2
        assert report['tap_suppression_reasons'] == {'moving-contact': 1, 'commit-cooldown': 1}
        assert report['commit_attempts'] == 3
        assert report['tap_guard_enabled'] is True
        assert window.tap_guard_toggle.isEnabled()
        window.start_trial()
        assert window.tap_guard.last_commit is None
    finally:
        window.close()
        app.processEvents()


def test_disabled_guard_restores_sdk_confirmation(tmp_path, monkeypatch):
    from test_complete_stroke_input import load_window, prepare
    app, window = load_window(tmp_path, monkeypatch)
    try:
        prepare(window)
        window.tap_guard_toggle.setChecked(False)
        window.add_manual('s')
        window.candidates = ['小']
        window.accept_sdk_tap({'points': [(0, 0), (8, 0)]})
        assert window.editor.text() == '小'
        window.candidates = ['你']
        window.accept_sdk_tap({})
        assert window.editor.text() == '小你'
    finally:
        window.close()
        app.processEvents()
