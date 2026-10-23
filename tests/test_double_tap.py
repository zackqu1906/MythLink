"""Double-tap timing and isolation from single taps and unrelated gestures."""

from dataclasses import replace

import pytest

from ring_python_sdk.swipe import DoubleTapDetector, DoubleTapEvent, SwipeResult


def tap(seq, time_ms, **kwargs):
    return SwipeResult(2, "trigger", seq, 5, time_ms, **kwargs)


@pytest.mark.parametrize("gap,matched", [(99, False), (100, True), (150, True), (200, True), (201, False)])
def test_two_confirmed_taps_use_inclusive_device_time_window(gap, matched):
    detector = DoubleTapDetector()
    first = tap(1, 5000, center_uptime_ms=1000)
    second = tap(2, 5010, center_uptime_ms=1000 + gap)
    assert detector.feed(first) is None
    result = detector.feed(second)
    assert (result is not None) == matched
    if matched:
        assert isinstance(result, DoubleTapEvent)
        assert result.name == "double-tap"
        assert (result.first, result.second, result.interval_ms) == (first, second, gap)


def test_single_classifications_and_duplicates_never_emit_double_tap():
    detector = DoubleTapDetector()
    first = tap(1, 1000)
    assert detector.feed(first) is None
    assert detector.feed(replace(first, seq=2, kind="event", uptime_ms=1300)) is None
    assert detector.feed(first) is None
    assert detector.feed(tap(2, 1150)).name == "double-tap"
    assert detector.feed(tap(2, 1150)) is None
    assert detector.feed(tap(3, 1300)) is None  # Three taps are only one pair.


@pytest.mark.parametrize("class_id", [1, 6, 8, 9])
def test_other_confirmed_gestures_interrupt_taps(class_id):
    detector = DoubleTapDetector()
    assert detector.feed(tap(1, 1000)) is None
    assert detector.feed(replace(tap(2, 1150), class_id=class_id)) is None
    assert detector.feed(tap(3, 1300)) is None
    assert detector.feed(tap(4, 1450)).first.seq == 3


def test_timeout_missing_trigger_and_reset_each_require_fresh_pair():
    detector = DoubleTapDetector()
    assert detector.feed(tap(1, 1000)) is None
    assert detector.feed(tap(2, 1300)) is None  # Expired.
    assert detector.feed(tap(4, 1450)) is None  # Missing trigger.
    detector.reset()  # New connection cannot inherit a pending tap.
    assert detector.feed(tap(1, 2400)) is None
    assert detector.feed(tap(2, 2550)).interval_ms == 150


def test_legacy_protocol_and_sequence_clock_wrap_are_supported():
    detector = DoubleTapDetector()
    assert detector.feed(replace(tap(65535, 0xFFFFFF80), protocol_version=1)) is None
    pair = detector.feed(replace(tap(0, 22), protocol_version=1))
    assert pair.name == "double-tap" and pair.interval_ms == 150
