import pytest

from ring_python_sdk.touchpad import TouchpadClick, TouchpadContact, TouchpadMove
from proximic_ring.touchpad_clicks import TouchpadClicks


def click(step, at):
    return TouchpadClick(step, at)


def test_single_is_returned_immediately_without_a_timer_or_further_events():
    clicks = TouchpadClicks()
    first = click(1, 10.)
    result = clicks.feed(first, now=10.)
    assert [(d.kind, d.first, d.second) for d in result] == [("single", first, None)]


@pytest.mark.parametrize('gap, kind', [(.001, 'double'), (.399, 'double'), (.400, 'double'),
                                      (.401, 'single'), (.500, 'single'), (10., 'single')])
def test_400ms_boundary_emits_double_or_new_single_immediately(gap, kind):
    clicks = TouchpadClicks()
    first, second = click(1, 10.), click(2, 10.+gap)
    assert clicks.feed(first, now=10.)[0].kind == 'single'
    result = clicks.feed(second, now=second.timestamp)
    assert [(d.kind, d.first, d.second) for d in result] == [
        (kind, first, second) if kind == 'double' else (kind, second, None)]


def test_triple_and_quadruple_clicks_form_nonoverlapping_pairs():
    clicks = TouchpadClicks()
    results = [clicks.feed(click(i+1, 10.+i*.1), now=10.+i*.1)[0] for i in range(4)]
    assert [d.kind for d in results] == ['single', 'double', 'single', 'double']
    assert results[1].first.step == 1 and results[3].first.step == 3


def test_stale_future_duplicate_and_out_of_order_events_cannot_emit_or_replace_pair():
    clicks = TouchpadClicks()
    first = click(10, 10.)
    assert clicks.feed(first, now=10.)[0].kind == 'single'
    for event, now in [(first, 10.1), (click(9, 10.01), 10.01), (click(11, 9.95), 10.01),
                       (click(11, 10.01), 10.5), (click(11, 11.), 10.2)]:
        assert clicks.feed(event, now=now) == []
    result = clicks.feed(click(12, 10.3), now=10.3)
    assert len(result) == 1 and result[0].kind == 'double' and result[0].first is first
    assert clicks.feed(first, now=10.1) == []


def test_pairing_uses_event_time_even_when_second_callback_is_late():
    clicks = TouchpadClicks()
    clicks.feed(click(1, 10.), now=10.)
    assert clicks.feed(click(2, 10.39), now=10.50)[0].kind == 'double'


def test_motion_and_expired_frames_preserve_pair_but_stream_reset_clears_it():
    clicks = TouchpadClicks()
    assert clicks.feed(click(300, 10.), now=10.)[0].kind == 'single'
    assert clicks.feed(TouchpadMove(1, 1, 1, 301, 10.1), now=10.1) == []
    for step in range(301, 320):
        assert clicks.feed(TouchpadContact('reset', step, 10.1), now=10.1) == []
    assert clicks.feed(click(340, 10.2), now=10.2)[0].kind == 'double'
    assert clicks.feed(click(350, 10.3), now=10.3)[0].kind == 'single'
    assert clicks.feed(TouchpadContact('reset', 0, 10.31), now=10.31) == []
    assert clicks.feed(click(1, 10.32), now=10.32)[0].kind == 'single'


def test_stop_or_mode_change_clears_pairing_without_replaying_first_single():
    observed = []
    clicks = TouchpadClicks(on_gesture=observed.append)
    clicks.feed(click(1, 10.), now=10.)
    clicks.clear()
    assert observed == ['click']
    assert clicks.feed(click(1, 10.1), now=10.1)[0].kind == 'single'
    assert observed == ['click', 'click']


def test_feedback_reports_single_then_double_and_cannot_interrupt_decisions():
    observed = []
    def notify(name):
        observed.append(name)
        raise RuntimeError('optional display unavailable')
    clicks = TouchpadClicks(on_gesture=notify)
    assert clicks.feed(click(1, 10.), now=10.)[0].kind == 'single'
    assert observed == ['click']
    assert clicks.feed(click(2, 10.1), now=10.1)[0].kind == 'double'
    assert clicks.feed(click(3, 11.), now=11.)[0].kind == 'single'
    clicks.clear()
    assert observed == ['click', 'double-click', 'click']
