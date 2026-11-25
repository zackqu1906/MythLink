"""Ring SDK input and firmware gestures share a clock, but arrive independently."""
from types import SimpleNamespace
import threading
import time

import pytest

from proximic_ring.gestures.firmware_gestures import FirmwareGestureWorker
from proximic_ring.touchpad import touchpad_mouse
from ring_python_sdk.swipe import SwipeResult
from ring_python_sdk.touchpad.events import (
    Raw2PointerClick, Raw2PointerContact, Raw2PointerMove, Raw2Reset, TouchpadContact, TouchpadMove,
)


@pytest.fixture
def pointer(monkeypatch):
    clock = [10.]
    monkeypatch.setattr(touchpad_mouse, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    output = touchpad_mouse.TouchpadMouseOutput(connection=threading.Event(), gain=3, clicks=True,
        invert_y=False, on_ready=lambda: None, on_end=lambda *_: None)
    output.activate()
    return output, clock


@pytest.mark.parametrize('mode', ['SINGLE', 'TWO_FINGER', 'legacy'])
def test_contact_owns_stationary_drag_and_two_finger_input_until_lift(pointer, mode):
    output, clock = pointer
    idle = output.firmware_gesture_epoch()
    def edge(phase, episode=1):
        if mode == 'legacy':
            return TouchpadContact('down' if phase == 'BEGIN' else 'up', 1, clock[0])
        return Raw2PointerContact(phase, mode, episode, 1, clock[0], 10.)

    output.submit(edge('BEGIN'))
    clock[0] += 8  # A held drag or stationary contact has no periodic motion events.
    if mode != 'legacy':
        output.submit(edge('END', episode=0))
    assert output.firmware_gesture_epoch() is None
    output.submit(edge('END'))
    clock[0] += .249
    assert output.firmware_gesture_epoch() is None
    clock[0] = 18.250
    assert output.firmware_gesture_epoch() not in (None, idle)
    if mode == 'legacy':
        assert not output.events  # Arbitration cannot synthesize mouse actions.


def test_motion_fallback_rejects_noise_and_renews_only_on_pointer_activity(pointer):
    output, clock = pointer
    idle = output.firmware_gesture_epoch()
    def move(dx, stamp):
        clock[0] = stamp
        output.submit(TouchpadMove(dx, 0, 0., 1, stamp))
    for i in range(8):
        move((-1)**i, 10. + i * .01)
    move(3, 10.2)
    move(3, 10.31)  # First movement is already outside the 100 ms window.
    assert output.firmware_gesture_epoch() == idle
    move(3, 10.35)
    assert output.firmware_gesture_epoch() is None
    move(7, 10.55)  # Extend protection across a brief pause.
    move(0, 10.79)
    assert output.firmware_gesture_epoch() is None
    clock[0] = 10.81
    assert output.firmware_gesture_epoch() not in (None, idle)


def test_reset_stale_stream_cancel_and_delayed_click_do_not_latch_guard(pointer):
    output, clock = pointer
    def edge(phase, generation, stamp):
        return Raw2PointerContact(phase, 'SINGLE', 1, 1, stamp, stamp,
                                  session_id='test', generation=generation)
    output.submit(edge('BEGIN', 1, 10.))
    assert output.firmware_gesture_epoch() is None
    output.submit(Raw2Reset('reset', 0, 10., session_id='test', generation=2))
    output.submit(edge('BEGIN', 1, 10.))
    assert output.firmware_gesture_epoch() is not None
    output.submit(edge('BEGIN', 2, 10.))
    clock[0] = 12.
    output.submit(edge('CANCEL', 2, 11.))  # Stale cleanup still releases the hold.
    output.submit(Raw2PointerClick(2, 12., source_timestamp=11., session_id='test', generation=2))
    assert output.firmware_gesture_epoch() is not None
    output.submit(Raw2PointerMove(.1, 0., 1., 3, 12., mode='TWO_FINGER', session_id='test', generation=2))
    assert output.firmware_gesture_epoch() is None
    output.stop()
    assert output.firmware_gesture_epoch() is not None


def test_reactivation_after_missing_release_while_paused(pointer):
    output, clock = pointer
    output.submit(TouchpadContact('down', 1, clock[0]))
    output.paused = True
    output.submit(TouchpadContact('up', 2, clock[0]))
    output.activate()
    assert output.firmware_gesture_epoch() is not None


def test_pointer_interrupts_pending_pair_and_fast_tap_before_firmware_publication(pointer):
    output, clock = pointer
    received, delivered = [], threading.Event()
    def publish(event):
        received.append(event)
        delivered.set()
    worker = FirmwareGestureWorker(on_gesture=publish)
    worker.gesture_epoch = output.firmware_gesture_epoch
    worker.can_end_tap_immediately = lambda: True
    worker.start()
    try:
        first = SwipeResult(2, 'trigger', 1, 8, 1000)
        worker.submit(first)
        deadline = time.monotonic() + 1
        while worker._double_index.pending_first is not first and time.monotonic() < deadline:
            time.sleep(.002)
        assert worker._double_index.pending_first is first
        output.submit(TouchpadContact('down', 1, clock[0]))
        worker.submit(SwipeResult(2, 'trigger', 2, 5, 1100))
        worker.submit_r1(SimpleNamespace(class_id=0x88, confidence=.9, center_uptime_ms=1100, sequence=3))
        output.submit(TouchpadContact('up', 2, clock[0]))
        worker.submit(SwipeResult(2, 'trigger', 3, 8, 1200))
        clock[0] += .250
        worker.submit(SwipeResult(2, 'trigger', 4, 8, 1400))
        assert delivered.wait(1.5)
        assert [(e.name, e.sequence) for e in received] == [('index-pinch', 4)]
        delivered.clear()
        worker.submit_r1(SimpleNamespace(class_id=0x88, confidence=.9, center_uptime_ms=1700, sequence=5))
        assert delivered.wait(1)
        assert received[-1].name == 'double-index-pinch'
    finally:
        worker.close()
