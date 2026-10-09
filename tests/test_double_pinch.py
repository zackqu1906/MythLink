"""Sequence and real protocol replay checks for double-pinch composition."""

from dataclasses import replace
import struct

import pytest

from ring_python_sdk.swipe import DoublePinchDetector, SwipeProcessor, SwipeResult


def pinch(seq, time_ms, *, class_id=8, protocol_version=2, **kwargs):
    return SwipeResult(protocol_version, "trigger", seq, class_id, time_ms, **kwargs)


@pytest.mark.parametrize("gap,matched", [(119, False), (120, True), (300, True), (600, True), (601, False)])
def test_interval_boundaries(gap, matched):
    detector = DoublePinchDetector()
    first, second = pinch(1, 1000), pinch(2, 1000 + gap)
    assert detector.feed(first) is None
    result = detector.feed(second)
    assert (result is not None) == matched
    if matched:
        assert result.name == "double-index-pinch"
        assert (result.first, result.second, result.interval_ms) == (first, second, gap)


def test_single_or_held_pinch_and_classifications_do_not_form_a_pair():
    detector = DoublePinchDetector()
    first = pinch(1, 1000)
    assert detector.feed(first) is None
    for time_ms in range(1100, 1800, 100):
        assert detector.feed(replace(first, kind="event", uptime_ms=time_ms)) is None
        assert detector.feed(replace(first, uptime_ms=time_ms)) is None  # Same sequence.


def test_bounce_does_not_move_first_pinch_and_classification_does_not_interrupt():
    detector = DoublePinchDetector()
    detector.feed(pinch(1, 1000))
    assert detector.feed(pinch(2, 1050)) is None
    assert detector.feed(replace(pinch(50, 1200, class_id=6), kind="event")) is None
    result = detector.feed(pinch(3, 1300))
    assert (result.first.seq, result.interval_ms) == (1, 300)


def test_expired_pinch_starts_a_new_pair_and_pairs_do_not_overlap():
    detector = DoublePinchDetector()
    results = [detector.feed(pinch(i, time_ms))
               for i, time_ms in enumerate((1000, 1800, 2100, 2400, 2700), start=1)]
    assert [result.second.seq for result in results if result] == [3, 5]
    assert [result.first.seq for result in results if result] == [2, 4]


@pytest.mark.parametrize("class_id", [6, 9])
def test_other_gesture_or_other_finger_breaks_pair(class_id):
    detector = DoublePinchDetector()
    detector.feed(pinch(1, 1000))
    assert detector.feed(pinch(2, 1150, class_id=class_id)) is None
    assert detector.feed(pinch(3, 1300)) is None
    assert detector.feed(pinch(4, 1600)).first.seq == 3


def test_replayed_packets_cannot_create_or_break_a_new_pair():
    detector = DoublePinchDetector()
    first = pinch(1, 1000)
    detector.feed(first)
    assert detector.feed(first) is None
    assert detector.feed(pinch(2, 1300)) is not None
    detector.feed(pinch(3, 1600))
    assert detector.feed(first) is None
    result = detector.feed(pinch(4, 1900))
    assert result.first.seq == 3


def test_gap_requires_a_new_pair():
    detector = DoublePinchDetector()
    detector.feed(pinch(1, 1000))
    assert detector.feed(pinch(3, 1300)) is None
    assert detector.feed(pinch(4, 1600)).first.seq == 3


def test_sequence_and_device_clock_wrap():
    detector = DoublePinchDetector()
    detector.feed(pinch(65535, 0xFFFFFF80))
    result = detector.feed(pinch(0, 172))
    assert result.interval_ms == 300


def test_center_time_is_used_instead_of_packet_delivery_time():
    detector = DoublePinchDetector()
    detector.feed(pinch(1, 5000, center_uptime_ms=1000))
    result = detector.feed(pinch(2, 5010, center_uptime_ms=1300))
    assert result.interval_ms == 300


def test_backwards_clock_clock_basis_change_and_disconnect_clear_pending():
    detector = DoublePinchDetector()
    detector.feed(pinch(1, 1000))
    assert detector.feed(pinch(2, 500)) is None
    assert detector.feed(pinch(3, 900, center_uptime_ms=800)) is None
    detector.reset()
    assert detector.feed(pinch(1, 1100, center_uptime_ms=1100)) is None
    assert detector.feed(pinch(2, 1400, center_uptime_ms=1400)) is not None


def test_legacy_tap_and_v2_tap_do_not_pair_across_protocol_change():
    detector = DoublePinchDetector(pinch_name="tap")
    detector.feed(pinch(1, 1000, class_id=5, protocol_version=1))
    # Switching protocol cancels even when time and sequence would otherwise match.
    assert detector.feed(pinch(2, 1300, class_id=5)) is None
    assert detector.feed(pinch(3, 1600, class_id=5)).name == "double-tap"
    detector.reset()
    detector.feed(pinch(1, 1000, class_id=5, protocol_version=1))
    assert detector.feed(pinch(2, 1300, class_id=5, protocol_version=1)).name == "double-tap"


def test_middle_pinch_opt_in():
    detector = DoublePinchDetector(pinch_name="middle-pinch")
    detector.feed(pinch(1, 1000, class_id=9))
    assert detector.feed(pinch(2, 1300, class_id=9)).name == "double-middle-pinch"


@pytest.mark.parametrize("kwargs", [
    {"pinch_name": "pinch-down"}, {"min_interval_ms": 0}, {"min_interval_ms": -1},
    {"min_interval_ms": 700}, {"max_interval_ms": float("nan")},
    {"max_interval_ms": float("inf")}, {"max_interval_ms": 2**31},
])
def test_invalid_configuration(kwargs):
    with pytest.raises(ValueError):
        DoublePinchDetector(**kwargs)


def test_compact_firmware_packets_reach_detector_and_ignore_duplicates(tmp_path):
    detector, pairs = DoublePinchDetector(), []

    def on_trigger(event):
        result = detector.feed(event)
        if result:
            pairs.append(result)

    processor = SwipeProcessor(tmp_path / "raw.csv", on_trigger=on_trigger,
                               print_triggers=False)
    try:
        for seq, time_ms in ((1, 1000), (1, 1000), (2, 1300), (2, 1300), (3, 1600)):
            packet = b"\x26\x07" + struct.pack("<HBIf", seq, 8, time_ms, 0.9)
            processor.handle_notification(None, bytearray(packet))
        assert processor.stats.callback_error_count == 0
        assert len(pairs) == 1
        assert pairs[0].interval_ms == 300
    finally:
        processor.close()
