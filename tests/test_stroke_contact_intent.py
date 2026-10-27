"""Application intent policy, including the real verdict/collector boundary."""
import threading
import json
from pathlib import Path

import pytest

from proximic_ring.touchpad.stroke.stroke_contact_intent import classify_contact
from proximic_ring.touchpad.stroke.stroke_tap_guard import TapConfirmationGuard
from proximic_ring.touchpad.touchpad_strokes import TouchpadStrokeOutput
from ring_python_sdk.touchpad import TouchpadContact, TouchpadMove, TouchpadClickVerdict, TouchpadClick


def line(length, count=13):
    return [(length*i/(count-1), 0.) for i in range(count)]


@pytest.mark.parametrize('count', [3, 20, 40, 60, 66, 67])
def test_stationary_release_before_333ms_is_a_tap(count):
    assert classify_contact([(0., 0.)]*count, finished=True)['action'] == 'tap'


@pytest.mark.parametrize('length', [2., 3., 6., 12.])
def test_short_directional_motion_is_handwriting_even_inside_landing_window(length):
    points = line(length)
    assert classify_contact(points, finished=True)['action'] == 'stroke'


def test_small_landing_movement_then_rest_can_tap_and_hold():
    landing = line(3.5)
    assert classify_contact(landing+[(3.5, 0.)]*7, finished=True)['action'] == 'tap'
    # 60ms landing + 333ms stable, rounded up to a 5ms sample boundary.
    assert classify_contact(landing+[(3.5, 0.)]*66)['phase'] == 'holding'
    assert classify_contact(landing+[(3.5, 0.)]*67)['phase'] == 'selecting'
    assert classify_contact(landing+[(3.5, 0.)]*67)['hold_ms'] == 395


def test_bounded_jitter_does_not_fail_due_to_accumulated_path():
    points = [(0., 0.), (.1, .1)]*30
    result = classify_contact(points, finished=True)
    assert result['path_length'] > 8 and result['action'] == 'tap'
    assert classify_contact(points+[(0., 0.)]*100)['phase'] == 'selecting'
    # Minor landing noise must not postpone an otherwise successful hold.
    assert classify_contact(points+[(0., 0.)]*8)['phase'] == 'selecting'


def test_333ms_boundary_rounds_up_without_arming_early():
    assert classify_contact([(0., 0.)]*67)['phase'] == 'holding'
    assert classify_contact([(0., 0.)]*68)['hold_ms'] == 335


@pytest.mark.parametrize('points', [
    line(1.75),  # Slightly larger compact release favors clicking.
    line(4.8)+[(4.8, 0.)]*3,  # Landing outside the old 4-unit limit; 15ms rest.
    line(3.)+[(3.+(.1 if i % 2 else 0.), 0.) for i in range(8)],
])
def test_click_bias_recovers_compact_landing_and_jittering_releases(points):
    assert classify_contact(points, finished=True)['action'] == 'tap'


def test_large_motion_with_short_pause_is_still_a_stroke():
    points = line(12.)+[(12., 0.)]*20
    assert classify_contact(points, finished=True)['action'] == 'stroke'


@pytest.mark.parametrize('prefix', [line(10), line(3, 41), line(3)+line(3)[::-1]*4])
def test_writing_then_pause_rearms_selection_and_consumes_prior_motion(prefix):
    points = prefix+[prefix[-1]]*150
    assert classify_contact(prefix)['phase'] != 'selecting'
    result = classify_contact(points, finished=True)
    assert result['phase'] == 'selecting' and result['action'] == 'cancel-selection'
    assert result['reason'] == 'pause-hold'
    assert classify_contact(points+[(prefix[-1][0]+10, prefix[-1][1])], finished=True)['action'] == 'select'


@pytest.mark.parametrize('vector,direction', [
    ((1, 0), 'right'), ((1, 1), 'down-right'), ((0, 1), 'down'),
    ((-1, 1), 'down-left'), ((-1, 0), 'left'), ((-1, -1), 'up-left'),
    ((0, -1), 'up'), ((1, -1), 'up-right'),
])
def test_armed_selection_locks_and_tracks_all_directions(vector, direction):
    held = [(0., 0.)]*101
    points = held+[(vector[0]*i, vector[1]*i) for i in range(1, 11)]
    result = classify_contact(points, finished=True)
    assert result['phase'] == 'selecting' and result['direction'] == direction
    assert result['hold_ms'] == 335
    assert classify_contact(points+[(0., 0.)], finished=True)['action'] == 'cancel-selection'


@pytest.mark.parametrize('points', [[], [(0,)], [(0, float('nan'))],
    [(0, 0), (float('inf'), 0)], [(0, 0), (1e308, 0), (-1e308, 0)], [(0, 0), (3, 0)]])
def test_invalid_or_insufficient_moving_geometry_cannot_commit(points):
    assert classify_contact(points, finished=True)['action'] == 'reject'


def contact_events(points, *, start=0, sdk_click=False):
    end = start+len(points)
    moves = [TouchpadMove(0, 0, .99, start, 10.)]
    moves += [TouchpadMove(b[0]-a[0], b[1]-a[1], .99, start+i, 10.)
              for i, (a, b) in enumerate(zip(points, points[1:]), 1)]
    return ([TouchpadContact('down', start, 10.)]+moves+
            [TouchpadContact('up', end, 10.)]+
            ([TouchpadClick(end, 10.)] if sdk_click else [])+
            [TouchpadClickVerdict(start, end, sdk_click, 10.)])


def run_batches(monkeypatch, batches, *, doubles=None):
    strokes, taps, selections, decisions, states, errors = [], [], [], [], [], []
    output = TouchpadStrokeOutput(connection=threading.Event(), on_ready=lambda: output.activate(),
        on_double_click=(lambda: doubles.append(True)) if doubles is not None else None,
        on_end=lambda reason, error: errors.append(error), on_stroke=strokes.append,
        on_tap=lambda: pytest.fail('must deliver full tap geometry'), on_tap_contact=taps.append,
        on_selection=selections.append, on_selection_state=states.append,
        contact_classifier=classify_contact, on_contact_intent=decisions.append)
    pending = iter(batches)

    class Wake:
        def wait(self, timeout):
            batch = next(pending, None)
            if batch is None:
                output.stopped.set()
            else:
                output.events.extend((event, None) for event in batch)
        def clear(self): pass
        def set(self): pass

    output.wake = Wake()
    monkeypatch.setattr('proximic_ring.touchpad.touchpad_strokes.time.monotonic', lambda: 10.)
    output._run()
    assert errors == [None]
    return strokes, taps, selections, decisions, states


@pytest.mark.parametrize('sdk_click', [True, False])
def test_application_recovers_taps_and_preserves_short_strokes_regardless_of_sdk(monkeypatch, sdk_click):
    tap = line(2)+[(2., 0.)]*8
    stroke = line(2)
    batches = [contact_events(tap, sdk_click=sdk_click),
               contact_events(stroke, start=len(tap), sdk_click=sdk_click)]
    strokes, taps, selections, decisions, _ = run_batches(monkeypatch, batches)
    assert strokes == [stroke] and len(taps) == 1 and not selections
    assert [d['intent_decision']['action'] for d in decisions] == ['tap', 'stroke']
    assert all(d['sdk_is_click'] is sdk_click for d in decisions)
    assert TapConfirmationGuard().decide(taps[0])['allowed']  # No second 1.5 path veto.


def test_slow_non_sdk_click_uses_application_tap_policy(monkeypatch):
    strokes, taps, selections, _, _ = run_batches(monkeypatch, [contact_events([(0., 0.)]*64)])
    assert len(taps) == 1 and taps[0]['duration_ms'] == 320
    assert not strokes and not selections


@pytest.mark.parametrize('sdk_click', [True, False])
def test_pipeline_pause_after_writing_consumes_whole_contact_once(monkeypatch, sdk_click):
    prefix = line(12.)
    points = prefix+[prefix[-1]]*68+[(12.+i, 0.) for i in range(1, 11)]
    events = contact_events(points, sdk_click=sdk_click)
    strokes, taps, selections, decisions, _ = run_batches(monkeypatch, [events[:14], events[14:-2], events[-2:]])
    assert len(selections) == len(decisions) == 1 and not strokes and not taps
    assert selections[0]['decision']['direction'] == 'right'
    assert selections[0]['decision']['reason'] == 'pause-hold'


def test_double_click_is_recognized_without_delaying_single_tap_selection(monkeypatch):
    doubles = []
    tap = [(0., 0.)]*10
    _, taps, _, _, _ = run_batches(monkeypatch, [contact_events(tap, sdk_click=True)], doubles=doubles)
    assert len(taps) == 1 and not doubles
    doubles = []
    _, taps, selections, _, _ = run_batches(monkeypatch, [contact_events(tap, sdk_click=True),
        contact_events(tap, start=20, sdk_click=True)], doubles=doubles)
    # The first tap still selects at once; the second only completes the double click.
    assert doubles == [True] and len(taps) == 1 and not selections


def test_duplicate_click_delivery_never_commits_twice(monkeypatch):
    events = contact_events([(0., 0.)]*10, sdk_click=True)
    _, taps, _, decisions, _ = run_batches(monkeypatch, [events, events[-2:]])
    assert len(taps) == len(decisions) == 1


def test_backdated_release_discards_lift_motion_after_armed_hold(monkeypatch):
    down = [TouchpadContact('down', 0, 10.)]
    moves = [TouchpadMove(0 if i <= 100 else 10, 0, .99, i, 10.) for i in range(105)]
    release = [TouchpadContact('up', 101, 10.), TouchpadClickVerdict(0, 101, False, 10.)]
    strokes, taps, selections, _, _ = run_batches(monkeypatch, [down+moves, release])
    assert len(selections) == 1 and selections[0]['decision']['action'] == 'cancel-selection'
    assert not taps and not strokes


@pytest.mark.parametrize('failure', ['gap', 'reset', 'stale', 'missing-verdict', 'early-up-gap'])
def test_incomplete_stream_cannot_commit(monkeypatch, failure):
    events = contact_events([(0., 0.)]*110, sdk_click=True)
    if failure == 'gap':
        events.pop(50)
    elif failure == 'reset':
        events.insert(50, TouchpadContact('reset', 49, 10.))
    elif failure == 'stale':
        events[50] = TouchpadMove(0, 0, .99, 49, 9.)
    elif failure == 'missing-verdict':
        events = events[:-2]
    else:
        batches = [events[:106], [TouchpadContact('up', 110, 10.),
                   TouchpadMove(0, 0, .99, 109, 10.), TouchpadClickVerdict(0, 110, False, 10.)]]
    strokes, taps, selections, _, _ = run_batches(monkeypatch, batches if failure == 'early-up-gap' else [events])
    assert not strokes and not selections
    assert not any(TapConfirmationGuard().decide(tap)['allowed'] for tap in taps)


def test_cooldown_uses_contact_frames_when_ui_callbacks_are_batched():
    guard = TapConfirmationGuard(clock=lambda: 10.)
    guard.committed(dict(stream_id='stream-a', end_step=100))
    assert guard.decide(dict(stream_id='stream-a', start_step=160))['allowed']
    assert guard.decide(dict(stream_id='stream-a', start_step=120))['reason'] == 'commit-cooldown'
    assert guard.decide(dict(stream_id='stream-b', start_step=160))['reason'] == 'commit-cooldown'
    guard.committed()  # Manual confirmation switches back to host time.
    assert guard.last_contact is None


def test_recorded_landing_examples_and_existing_hold_remain_selectable():
    fixture = json.loads((Path(__file__).parent/'fixtures/contact_intent_regression.json').read_text(encoding='utf-8'))
    assert len(fixture['samples']) == 5
    for sample in fixture['samples']:
        points = sample['points']
        decision = classify_contact(points, finished=True)
        assert decision['phase'] == sample['expected_phase'], sample['time']
        assert decision['direction'] == sample['expected_direction'], sample['time']
        # A menu that appeared during this contact cannot become handwriting.
        armed = False
        for end in range(1, len(points)+1):
            state = classify_contact(points[:end])
            if armed:
                assert state['phase'] == 'selecting'
            armed = armed or state['phase'] == 'selecting'


def test_ui_accepts_classified_jitter_tap_and_logs_new_policy(tmp_path, monkeypatch):
    from test_complete_stroke_input import load_window, prepare
    app, window = load_window(tmp_path, monkeypatch)
    try:
        prepare(window)
        window.add_manual('s')
        window.candidates = ['小']
        points = line(2)+[(2., 0.)]*8
        window.accept_sdk_tap(dict(points=points, intent_decision=classify_contact(points, finished=True),
                                   stream_id='trial', start_step=0, end_step=len(points)))
        assert window.editor.text() == '小' and not window.code
        assert window.tap_guard.last_contact['stream_id'] == 'trial'
    finally:
        window.close()
        app.processEvents()


def test_ui_pause_after_motion_selects_existing_code_without_adding_a_stroke(tmp_path, monkeypatch):
    from test_complete_stroke_input import load_window, prepare
    app, window = load_window(tmp_path, monkeypatch)
    try:
        prepare(window)
        window.add_manual('s')
        points = line(12.)+[(12., 0.)]*68
        strokes, taps, selections, _, _ = run_batches(monkeypatch, [contact_events(points)])
        assert not strokes and not taps and len(selections) == 1
        window.accept_selection(selections[0])
        assert window.editor.text() == '小' and not window.code and not window.evidence
    finally:
        window.close()
        app.processEvents()
