"""Two-finger motion interface: eight directions, gesture grouping and stream resets."""
import pytest

from ring_python_sdk.touchpad.events import Raw2PointerMove, Raw2Reset
from ring_python_sdk.touchpad.two_finger import (Direction, TwoFingerGestures, TwoFingerScroll,
                                                 classify_direction)


class Clock:
    def __init__(self): self.now = 0.
    def __call__(self): return self.now


def move(dx, dy, mode='TWO_FINGER', session='s', generation=0):
    return Raw2PointerMove(dx, dy, 1., 1, 0., mode=mode, session_id=session, generation=generation)


@pytest.mark.parametrize('dx,dy,expected', [
    (0, -1, Direction.UP), (1, -1, Direction.UP_RIGHT), (1, 0, Direction.RIGHT),
    (1, 1, Direction.DOWN_RIGHT), (0, 1, Direction.DOWN), (-1, 1, Direction.DOWN_LEFT),
    (-1, 0, Direction.LEFT), (-1, -1, Direction.UP_LEFT),
    # Sector edges: ±22.5° around each axis.
    (10, -4.1, Direction.RIGHT), (10, -4.2, Direction.UP_RIGHT), (10, 4.1, Direction.RIGHT),
])
def test_eight_directions_in_screen_coordinates(dx, dy, expected):
    assert classify_direction(dx, dy) is expected


def test_zero_or_invalid_displacement_has_no_direction():
    for dx, dy in [(0, 0), (float('nan'), 1)]:
        with pytest.raises(ValueError):
            classify_direction(dx, dy)


def run(events, *, clock=None, step=.005, **options):
    clock = clock or Clock()
    swipes, scrolls = [], []
    gestures = TwoFingerGestures(on_swipe=swipes.append, on_scroll=scrolls.append, clock=clock, **options)
    for event in events:
        clock.now += step
        gestures.feed(event)
    clock.now += 1
    gestures.tick()
    return swipes, scrolls


def test_one_swipe_per_gesture_with_total_displacement_and_scroll_frames():
    swipes, scrolls = run([move(2, -1)] * 20)
    assert len(swipes) == 1 and len(scrolls) == 20
    swipe = swipes[0]
    assert swipe.direction is Direction.UP_RIGHT or swipe.direction is Direction.RIGHT
    assert (swipe.dx, swipe.dy, swipe.frames) == (40, -20, 20)
    assert swipe.direction is classify_direction(40, -20)
    assert all(isinstance(s, TwoFingerScroll) and (s.dx, s.dy) == (2, -1) for s in scrolls)


def test_brief_class_flicker_does_not_split_but_a_long_one_does():
    flicker = [move(0, 3)] * 10 + [move(0, 0, 'UNKNOWN')] * 5 + [move(0, 3)] * 10
    swipes, _ = run(flicker)  # 25 ms of UNKNOWN < 80 ms
    assert len(swipes) == 1 and swipes[0].dy == 60
    split = [move(0, 3)] * 10 + [move(1, 0, 'SINGLE')] * 30 + [move(0, -3)] * 10
    swipes, _ = run(split)    # 150 ms of SINGLE ends the first gesture
    assert [s.direction for s in swipes] == [Direction.DOWN, Direction.UP]


def test_gesture_ends_after_quiet_gap_and_short_ones_are_dropped():
    clock = Clock()
    swipes = []
    gestures = TwoFingerGestures(on_swipe=swipes.append, clock=clock, min_distance=15)
    for _ in range(10):
        clock.now += .005
        gestures.feed(move(-3, 0))
    clock.now += .1
    assert gestures.tick() is None and not swipes   # still within 150 ms
    clock.now += .1
    assert gestures.tick().direction is Direction.LEFT
    gestures.feed(move(2, 2))                       # 2.8 px: below min_distance
    clock.now += 1
    assert gestures.tick() is None and len(swipes) == 1


def test_new_generation_or_reset_discards_the_gesture_in_progress():
    events = [move(0, 5)] * 5 + [move(0, 5, generation=1)] * 5
    swipes, _ = run(events)
    assert len(swipes) == 1 and swipes[0].generation == 1 and swipes[0].dy == 25
    swipes, _ = run([move(0, 5)] * 5 + [Raw2Reset('reset', 0, 0., session_id='s', generation=2)])
    assert swipes == []


def test_flush_reports_the_open_gesture():
    gestures = TwoFingerGestures(clock=Clock())
    for _ in range(10):
        gestures.feed(move(0, -4))
    assert gestures.flush().direction is Direction.UP and gestures.flush() is None
