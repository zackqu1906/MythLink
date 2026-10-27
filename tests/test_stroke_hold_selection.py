import threading
import time

import pytest

from proximic_ring.touchpad.stroke.stroke_hold_selection import hold_selection
from proximic_ring.touchpad.touchpad_strokes import TouchpadStrokeOutput
from ring_python_sdk.touchpad import TouchpadContact, TouchpadMove, TouchpadClickVerdict


def held_path(dx=0, dy=0):
    return [(0., 0.)]*101 + [(dx*i, dy*i) for i in range(1, 11)]


@pytest.mark.parametrize('vector,direction,action,target', [
    ((1,0), 'right', 'select', 4), ((1,1), 'down-right', 'select', 2),
    ((0,1), 'down', 'select', 0), ((-1,1), 'down-left', 'select', 1),
    ((-1,0), 'left', 'select', 3), ((-1,-1), 'up-left', 'punctuation', '，'),
    ((0,-1), 'up', 'page', 'next-page'), ((1,-1), 'up-right', 'punctuation', '。'),
])
def test_hold_then_direct_slide_uses_all_eight_directions(vector, direction, action, target):
    result = hold_selection(held_path(*vector), finished=True)
    assert (result['direction'], result['action'], result['target']) == (direction, action, target)
    assert result['hold_ms'] == 500


def test_hold_boundary_cancel_and_return_to_center():
    assert hold_selection([(0,0)]*100)['phase'] == 'holding'
    assert hold_selection([(0,0)]*101)['phase'] == 'selecting'
    assert hold_selection([(0,0)]*100, finished=True)['phase'] == 'idle'
    assert hold_selection([(0,0)]*101, finished=True)['action'] == 'cancel-selection'
    assert hold_selection(held_path(1,0)+[(0,0)], finished=True)['action'] == 'cancel-selection'


def test_minor_jitter_allowed_but_writing_and_midstroke_pause_cannot_arm():
    jitter = [(0,0), (.01,.01)]*51
    assert hold_selection(jitter)['phase'] == 'selecting'
    assert hold_selection([(i*.1,0) for i in range(30)]+[(2.9,0)]*150)['phase'] == 'writing'
    assert hold_selection([(0,0),(2,0)]+[(0,0)]*150)['phase'] == 'writing'
    assert hold_selection([(0,0), (.5,0)]*51)['phase'] == 'writing'
    assert hold_selection([(0,0),(float('nan'),0)])['phase'] == 'idle'


def start_output():
    ready = threading.Event()
    states, selections, strokes, taps = [], [], [], []
    output = TouchpadStrokeOutput(connection=threading.Event(), on_ready=ready.set,
        on_end=lambda *_: None, on_stroke=strokes.append, on_tap=lambda: taps.append(True),
        on_selection=selections.append, on_selection_state=states.append)
    output.start()
    assert ready.wait(1)
    output.activate()
    return output, states, selections, strokes, taps


def batch(output, events):
    with output.lock:
        output.events.extend((event, None) for event in events)
    output.wake.set()


def wait_for(predicate):
    deadline = time.monotonic()+1
    while not predicate() and time.monotonic() < deadline:
        time.sleep(.005)
    assert predicate()


@pytest.mark.parametrize('click', [False, True])
def test_pipeline_hold_consumes_contact_before_click_or_handwriting(click):
    output, states, selections, strokes, taps = start_output()
    try:
        now = time.monotonic()
        batch(output, [TouchpadContact('down',0,now)] +
              [TouchpadMove(0,0,.99,i,now) for i in range(101)])
        wait_for(lambda: any(s['phase'] == 'selecting' for s in states))
        assert not selections  # Live feedback never commits.
        now = time.monotonic()
        batch(output, [TouchpadMove(1,0,.99,i,now) for i in range(101,111)] +
              [TouchpadContact('up',111,now), TouchpadClickVerdict(0,111,click,now)])
        wait_for(lambda: bool(selections))
        assert selections[0]['decision']['target'] == 4
        assert len(selections) == 1 and not strokes and not taps
    finally:
        output.stop(); output.thread.join(1)


def test_backdated_release_trims_lift_movement_and_cancels_stationary_hold():
    output, states, selections, strokes, taps = start_output()
    try:
        now = time.monotonic()
        batch(output, [TouchpadContact('down',0,now)] +
              [TouchpadMove(0 if i <= 100 else 10,0,.99,i,now) for i in range(105)])
        wait_for(lambda: any(s['phase'] == 'selecting' for s in states))
        now = time.monotonic()
        batch(output, [TouchpadContact('up',101,now), TouchpadClickVerdict(0,101,True,now)])
        wait_for(lambda: bool(selections))
        assert selections[0]['decision']['action'] == 'cancel-selection'
        assert not strokes and not taps
    finally:
        output.stop(); output.thread.join(1)


def test_gap_after_early_release_notification_does_not_commit_partial_contact():
    output, states, selections, strokes, taps = start_output()
    try:
        now = time.monotonic()
        batch(output, [TouchpadContact('down',0,now)] +
              [TouchpadMove(0 if i <= 100 else 1,0,.99,i,now) for i in range(105)])
        wait_for(lambda: any(s['phase'] == 'selecting' for s in states))
        now = time.monotonic()
        # SDK edges can precede move delivery. Missing 105..108 invalidates
        # the complete contact even though its earlier samples armed the menu.
        batch(output, [TouchpadContact('up',110,now),
                       TouchpadMove(1,0,.99,109,now),
                       TouchpadClickVerdict(0,110,False,now)])
        wait_for(lambda: states[-1]['phase'] == 'idle')
        assert not selections and not strokes and not taps
    finally:
        output.stop(); output.thread.join(1)


@pytest.mark.parametrize('interrupt', ['gap', 'reset', 'stale'])
def test_interrupted_hold_never_selects(interrupt):
    output, states, selections, strokes, taps = start_output()
    try:
        now = time.monotonic()
        batch(output, [TouchpadContact('down',0,now)] +
              [TouchpadMove(0,0,.99,i,now) for i in range(101)])
        wait_for(lambda: any(s['phase'] == 'selecting' for s in states))
        if interrupt == 'gap':
            output.submit(TouchpadMove(10,0,.99,105,time.monotonic()))
        elif interrupt == 'reset':
            output.submit(TouchpadContact('reset',101,time.monotonic()))
        wait_for(lambda: states[-1]['phase'] == 'idle')
        now = time.monotonic()
        batch(output, [TouchpadContact('up',110,now), TouchpadClickVerdict(0,110,True,now)])
        time.sleep(.03)
        assert not selections and not strokes and not taps
    finally:
        output.stop(); output.thread.join(1)
