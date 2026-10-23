"""Firmware worker must never send either half of a recognized double tap."""

import time

from proximic_ring.gestures.firmware_gestures import FirmwareGestureWorker, SINGLE_TAP_WAIT_S
from ring_python_sdk.swipe import SwipeResult


def trigger(seq, timestamp_ms, *, class_id=5):
    return SwipeResult(2, "trigger", seq, class_id, timestamp_ms,
                       center_uptime_ms=timestamp_ms, confirmed_confidence=.9)


def wait_for(predicate, timeout=1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.005)
    return predicate()


def test_double_tap_is_one_immediate_event_with_no_preceding_or_later_single():
    events = []
    worker = FirmwareGestureWorker(on_gesture=events.append)
    worker.start()
    try:
        worker.submit(trigger(1, 1000))
        time.sleep(.04)
        assert events == []
        worker.submit(trigger(1, 1000))  # Firmware retransmission is not the second tap.
        worker.submit(trigger(2, 1150))
        assert wait_for(lambda: len(events) == 1)
        time.sleep(SINGLE_TAP_WAIT_S + .03)
        assert [event.name for event in events] == ["double-tap"]
        assert events[0].raw.interval_ms == 150
        assert worker.snapshot()["gesture_counts"] == {"double-tap": 1}
    finally:
        worker.close()


def test_single_tap_waits_for_pair_window_then_uses_normal_route():
    events = []
    worker = FirmwareGestureWorker(on_gesture=lambda event: events.append((event, time.monotonic())))
    worker.start()
    try:
        started = time.monotonic()
        worker.submit(trigger(1, 1000))
        time.sleep(.14)
        assert events == []
        assert wait_for(lambda: len(events) == 1)
        assert events[0][0].name == "tap"
        assert events[0][1] - started >= .20
        assert worker.snapshot()["gesture_counts"] == {"tap": 1}
    finally:
        worker.close()


def test_other_confirmed_gesture_resolves_pending_single_in_order():
    events = []
    worker = FirmwareGestureWorker(on_gesture=lambda event: events.append(event.name))
    worker.start()
    try:
        worker.submit(trigger(1, 1000))
        worker.submit(trigger(2, 1100, class_id=6))
        assert wait_for(lambda: len(events) == 2)
        assert events == ["tap", "snap"]
        worker.submit(trigger(3, 1200))
        worker.submit(trigger(4, 1350))
        assert wait_for(lambda: len(events) == 3)
        assert events == ["tap", "snap", "double-tap"]
    finally:
        worker.close()


def test_outside_device_time_window_becomes_two_singles():
    events = []
    worker = FirmwareGestureWorker(on_gesture=lambda event: events.append(event.name))
    worker.start()
    try:
        worker.submit(trigger(1, 1000))
        worker.submit(trigger(2, 1201))
        assert wait_for(lambda: len(events) == 2)
        assert events == ["tap", "tap"]
    finally:
        worker.close()


def test_disconnect_discards_a_pending_single():
    events = []
    worker = FirmwareGestureWorker(on_gesture=events.append)
    worker.start()
    worker.submit(trigger(1, 1000))
    assert wait_for(lambda: worker.snapshot()["received_events"] == 1)
    worker.close()
    time.sleep(SINGLE_TAP_WAIT_S + .03)
    assert events == []


def test_double_middle_pinch_never_sends_either_single_submission():
    from proximic_ring.gestures.firmware_gestures import SINGLE_MIDDLE_WAIT_S
    events=[];worker=FirmwareGestureWorker(on_gesture=lambda e:events.append(e.name))
    worker.start()
    try:
        worker.submit(trigger(1,1000,class_id=9))
        time.sleep(.04)
        assert events==[]
        worker.submit(trigger(2,1200,class_id=9))
        assert wait_for(lambda:events==['double-middle-pinch'])
        time.sleep(SINGLE_MIDDLE_WAIT_S+.02)
        assert events==['double-middle-pinch']
    finally:worker.close()


def test_single_middle_pinch_resolves_before_different_gesture():
    events=[];worker=FirmwareGestureWorker(on_gesture=lambda e:events.append(e.name))
    worker.start()
    try:
        worker.submit(trigger(1,1000,class_id=9))
        worker.submit(trigger(2,1100,class_id=1))
        assert wait_for(lambda:len(events)==2)
        assert events==['middle-pinch','swipe-up']
    finally:worker.close()


def test_pending_middle_pinch_is_discarded_on_disconnect():
    events=[];worker=FirmwareGestureWorker(on_gesture=lambda e:events.append(e.name))
    worker.start();worker.submit(trigger(1,1000,class_id=9));worker.close()
    assert events==[]


def test_double_index_pinch_does_not_open_single_pinch_menu():
    from proximic_ring.gestures.firmware_gestures import SINGLE_INDEX_WAIT_S
    events=[];worker=FirmwareGestureWorker(on_gesture=lambda e:events.append(e.name))
    worker.start()
    try:
        worker.submit(trigger(1,1000,class_id=8));time.sleep(.04)
        assert events==[]
        worker.submit(trigger(2,1400,class_id=8))
        assert wait_for(lambda:events==['double-index-pinch'])
        time.sleep(SINGLE_INDEX_WAIT_S+.02)
        assert events==['double-index-pinch']
    finally:worker.close()


def test_single_index_pinch_still_delivers_after_pair_window():
    events=[];worker=FirmwareGestureWorker(on_gesture=lambda e:events.append(e.name));worker.start()
    try:
        worker.submit(trigger(1,1000,class_id=8))
        assert wait_for(lambda:events==['index-pinch'],timeout=1.2)
    finally:worker.close()
