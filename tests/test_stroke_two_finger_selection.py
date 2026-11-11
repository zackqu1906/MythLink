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
from ring_python_sdk.touchpad.events import Raw2PointerMove


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


def test_single_finger_hold_no_longer_arms_selection():
    held = [(0., 0.)] * 200 + [(i * .5, 0.) for i in range(1, 30)]
    assert trial_contact(held)['phase'] != 'selecting'
    assert trial_contact([(0., 0.)] * 200)['progress'] == 0
    assert trial_contact(held, finished=True)['action'] in {'stroke', 'tap', 'reject'}


def start_output():
    ready = threading.Event()
    states, selections, strokes, taps = [], [], [], []
    output = TouchpadStrokeOutput(connection=threading.Event(), on_ready=ready.set,
        on_end=lambda *_: None, on_stroke=strokes.append, on_tap=lambda: None,
        on_stroke_details=strokes.append, on_tap_contact=taps.append,
        on_selection=selections.append, on_selection_state=states.append,
        contact_classifier=trial_contact, two_finger_classifier=two_finger_selection)
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
