"""Foreground cutoffs discard queued and interrupted contacts before delivery."""
import threading

import pytest

from proximic_ring.touchpad.stroke.stroke_contact_intent import classify_contact
from proximic_ring.touchpad.touchpad_strokes import TouchpadStrokeOutput
from ring_python_sdk.touchpad import TouchpadContact, TouchpadMove, TouchpadClickVerdict


def contact(start, timestamp, *, release=True):
    events = [TouchpadContact('down', start, timestamp)]
    events += [TouchpadMove(0, 0, .99, i, timestamp) for i in range(start, start+20)]
    if release:
        events += [TouchpadContact('up', start+20, timestamp),
                   TouchpadClickVerdict(start, start+20, True, timestamp)]
    return events


@pytest.mark.parametrize('pause_seen', [False, True])
def test_foreground_round_trip_drops_old_queue_pending_verdict_and_partial_contact(monkeypatch, pause_seen):
    taps, strokes, selections, errors = [], [], [], []
    clock = [10.]
    cutoff = [9.9]
    output = TouchpadStrokeOutput(connection=threading.Event(), on_ready=lambda: output.activate(),
        on_end=lambda reason, error: errors.append(error), on_stroke=strokes.append,
        on_tap=lambda: pytest.fail('must retain contact geometry'), on_tap_contact=taps.append,
        on_selection=selections.append, contact_classifier=classify_contact)
    output.input_since = lambda: cutoff[0]
    # First hold a completed contact awaiting its click verdict, and start
    # another contact that loses focus before release.
    initial = contact(0, 10.)[:-1] + contact(30, 10., release=False)
    batches = [(9.9, 10., initial)]
    if pause_seen:
        batches.append((None, 10.01, contact(60, 10.01)))
    # The new cutoff also handles a fast round trip between consumer polls.
    batches += [(10.02, 10.03, contact(80, 10.) + [
        TouchpadContact('up', 50, 10.03), TouchpadClickVerdict(30, 50, True, 10.03),
        TouchpadClickVerdict(0, 20, True, 10.03)]),
        (10.02, 10.04, contact(120, 10.04))]
    pending = iter(batches)

    class Wake:
        def wait(self, timeout):
            item = next(pending, None)
            if item is None:
                output.stopped.set()
            else:
                cutoff[0], clock[0], events = item
                # Include previously queued input, bypassing submit's early gate.
                output.events.extend((event, None) for event in events)
        def clear(self): pass
        def set(self): pass

    output.wake = Wake()
    monkeypatch.setattr('proximic_ring.touchpad.touchpad_strokes.time.monotonic', lambda: clock[0])
    output._run()
    assert errors == [None]
    assert [tap['start_step'] for tap in taps] == [120]
    assert not strokes and not selections


def test_submit_rejects_background_and_pre_focus_events():
    output = TouchpadStrokeOutput(connection=threading.Event(), on_ready=lambda: None,
        on_end=lambda *_: None, on_stroke=lambda _: None, on_tap=lambda: None)
    cutoff = [None]
    output.input_since = lambda: cutoff[0]
    output.activate()
    output.submit(TouchpadContact('down', 0, 10.))
    assert not output.events
    cutoff[0] = 11.
    output.submit(TouchpadContact('down', 20, 10.9))
    assert not output.events
    output.submit(TouchpadContact('down', 30, 11.1))
    assert [event.step for event, _ in output.events] == [30]
