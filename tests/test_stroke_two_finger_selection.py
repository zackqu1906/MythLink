"""Two-finger swipes select menu cells; two-finger left deletes."""
import math
import threading
import time

import pytest

from proximic_ring.touchpad.stroke.stroke_menu import (
    ACTION_CELLS, CANDIDATE_CELLS, PAGE_SIZE, PUNCTUATION_CELLS, TWO_FINGER_SELECTION,
    trial_contact, two_finger_selection)
from proximic_ring.touchpad.touchpad_strokes import TouchpadStrokeOutput
from ring_python_sdk.touchpad import TouchpadClickVerdict, TouchpadContact, TouchpadMove
from ring_python_sdk.touchpad.events import Raw2PointerContact, Raw2PointerMove


def swipe(dx, dy, frames=20):
    return [(dx * i / frames, dy * i / frames) for i in range(frames + 1)]


def test_menu_covers_all_eight_directions_once():
    directions = ([d for d, *_ in CANDIDATE_CELLS if d != 'center'] + [d for d, *_ in PUNCTUATION_CELLS]
                  + [d for d, *_ in ACTION_CELLS])
    assert sorted(directions) == sorted(['right', 'down-right', 'down', 'down-left', 'left',
                                         'up-left', 'up', 'up-right'])
    assert PAGE_SIZE == len(CANDIDATE_CELLS) == 5


@pytest.mark.parametrize('angle, direction, action, target', [
    (0, 'right', 'select', 1), (45, 'down-right', 'select', 4), (90, 'down', 'select', 3),
    (135, 'down-left', 'select', 2), (180, 'left', 'delete-one', None),
    (225, 'up-left', 'punctuation', '，'), (270, 'up', 'punctuation', '。'),
    (315, 'up-right', 'punctuation', '？')])
def test_two_finger_direction_maps_to_cell(angle, direction, action, target):
    distance = TWO_FINGER_SELECTION['selectDistance'] * 3
    points = swipe(math.cos(math.radians(angle)) * distance, math.sin(math.radians(angle)) * distance)
    live = two_finger_selection(points)
    assert live['phase'] == 'selecting' and live['direction'] == direction and live['progress'] == 100
    assert live['display']['origin'] == [0., 0.] and live['display']['points'][-1] == points[-1]
    final = two_finger_selection(points, finished=True)
    assert final['action'] == action and final['target'] == target and 'display' not in final


def test_short_two_finger_motion_cancels():
    points = swipe(TWO_FINGER_SELECTION['selectDistance'] * .5, 0)
    live = two_finger_selection(points)
    assert live['direction'] is None and live['progress'] == pytest.approx(50)
    assert two_finger_selection(points, finished=True)['action'] == 'cancel-selection'
    assert two_finger_selection([], finished=True)['action'] == 'cancel-selection'


def test_long_left_pull_arms_clear_only_until_retracted():
    limit = TWO_FINGER_SELECTION['clearDistance']
    points = swipe(-limit, 0)
    live = two_finger_selection(points)
    assert live['clear_armed'] and 'action' not in live
    assert two_finger_selection(points, finished=True)['action'] == 'clear-all'
    points.append((-limit + 1, 0))
    assert not two_finger_selection(points)['clear_armed']
    assert two_finger_selection(points, finished=True)['action'] == 'delete-one'
    points.append((0, 0))
    assert two_finger_selection(points, finished=True)['action'] == 'cancel-selection'
    assert two_finger_selection(swipe(-limit, -limit), finished=True)['action'] == 'punctuation'
    assert two_finger_selection([(0, 0), (float('nan'), 0)], finished=True)['action'] == 'cancel-selection'


def test_clear_fill_tracks_outward_travel_and_retraction_without_committing():
    points = [(0., 0.)]
    for x, y, progress in [(-6, 0, 0), (-18, 0, 0), (-24, 0, .5), (-30, 0, 1),
                            (-40, 0, 1), (-24, 0, .5), (-18, 0, 0), (-30, 30, 0), (0, 0, 0)]:
        points.append((x, y))
        state = two_finger_selection(points)
        assert state['clear_progress'] == progress and 'action' not in state
        assert state['clear_armed'] == (progress == 1)


def test_single_finger_hold_no_longer_arms_selection():
    held = [(0., 0.)] * 200 + [(i * .5, 0.) for i in range(1, 30)]
    assert trial_contact(held)['phase'] != 'selecting'
    assert trial_contact([(0., 0.)] * 200)['progress'] == 0
    assert trial_contact(held, finished=True)['action'] in {'stroke', 'tap', 'reject'}


def start_output(**options):
    ready = threading.Event()
    states, selections, strokes, taps = [], [], [], []
    output = TouchpadStrokeOutput(connection=threading.Event(), on_ready=ready.set,
        on_end=lambda *_: None, on_stroke=strokes.append, on_tap=lambda: None,
        on_stroke_details=strokes.append, on_tap_contact=taps.append,
        on_selection=selections.append, on_selection_state=states.append,
        contact_classifier=trial_contact, two_finger_classifier=two_finger_selection, **options)
    output.start()
    assert ready.wait(1)
    output.activate()
    return output, states, selections, strokes, taps


def feed(output, events, two_finger_steps=()):
    for step in two_finger_steps:
        output.submit_pointer(Raw2PointerMove(0., 0., 0., step, time.monotonic(), mode='TWO_FINGER'))
    output.submit_pointer(Raw2PointerMove(0., 0., 0., 999, time.monotonic(), mode='SINGLE'))  # ignored
    with output.lock:
        output.events.extend((event, None) for event in events)
    output.wake.set()


def wait_for(predicate):
    deadline = time.monotonic() + 1
    while not predicate() and time.monotonic() < deadline:
        time.sleep(.005)
    assert predicate()


def contact(dx, dy, frames=40):
    now = time.monotonic()
    return ([TouchpadContact('down', 0, now)] + [TouchpadMove(dx, dy, .99, i, now) for i in range(frames)]
            + [TouchpadContact('up', frames, now), TouchpadClickVerdict(0, frames, False, now)])


def test_two_finger_contact_selects_instead_of_writing():
    output, states, selections, strokes, taps = start_output()
    try:
        events = contact(-.5, 0)
        feed(output, events[:-2], two_finger_steps=range(10, 40))
        # While held, the menu shows the two-finger path instead of ink.
        wait_for(lambda: any(s.get('source') == 'two-finger' and s['direction'] == 'left' for s in states))
        assert not selections
        feed(output, events[-2:])
        wait_for(lambda: bool(selections))
        decision = selections[0]['decision']
        assert decision['source'] == 'two-finger' and decision['direction'] == 'left'
        assert decision['action'] == 'delete-one'
        assert len(selections) == 1 and not strokes and not taps
        assert any(s.get('source') == 'two-finger' for s in states)
        wait_for(lambda: states[-1]['phase'] == 'idle')
    finally:
        output.stop(); output.thread.join(1)


def test_single_finger_contact_still_writes():
    output, states, selections, strokes, taps = start_output()
    try:
        feed(output, contact(.5, .5))
        wait_for(lambda: bool(strokes))
        assert not selections and not any(s.get('source') == 'two-finger' for s in states)
    finally:
        output.stop(); output.thread.join(1)


def test_long_left_contact_clears_once_on_release_without_early_delete():
    output, states, selections, strokes, taps = start_output()
    try:
        events = contact(-1, 0)
        feed(output, events[:-2], two_finger_steps=range(10, 40))
        wait_for(lambda: any(s.get('clear_armed') for s in states))
        assert not selections and not strokes and not taps
        feed(output, events[-2:])
        wait_for(lambda: bool(selections))
        assert len(selections) == 1 and selections[0]['decision']['action'] == 'clear-all'
        assert not strokes and not taps
    finally:
        output.stop(); output.thread.join(1)


def test_stationary_two_finger_press_shows_selection_before_any_movement():
    output, states, selections, strokes, taps = start_output(raw2=True)
    try:
        now = time.monotonic()
        output.submit_pointer(Raw2PointerContact('CLASSIFIED', 'TWO_FINGER', 1, 12, now, now,
                                                session_id='ring', start_step=1))
        wait_for(lambda: any(s['phase'] == 'selecting' for s in states))
        assert states[-1]['source'] == 'two-finger' and states[-1]['direction'] is None
        assert not strokes and not taps and not selections
        output.submit_pointer(Raw2PointerContact('END', 'TWO_FINGER', 1, 20, time.monotonic(), now,
                                                session_id='ring', start_step=1, end_step=15))
        wait_for(lambda: states[-1]['phase'] == 'idle')
    finally:
        output.stop(); output.thread.join(1)


def test_contact_classification_routes_delete_without_a_pointer_motion_frame():
    output, states, selections, strokes, taps = start_output(raw2=True)
    try:
        events = contact(-.5, 0)
        now = time.monotonic()
        output.submit_pointer(Raw2PointerContact('CLASSIFIED', 'TWO_FINGER', 1, 10, now, now,
                                                session_id='ring', start_step=0))
        feed(output, events)
        wait_for(lambda: bool(selections))
        assert selections[0]['decision']['action'] == 'delete-one'
        assert not strokes and not taps
    finally:
        output.stop(); output.thread.join(1)
