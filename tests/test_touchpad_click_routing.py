"""Actual output workers with fake native mouse; no system events are posted."""
import threading
import time

from proximic_ring.touchpad_mouse import TouchpadMouseOutput
from proximic_ring.touchpad_strokes import TouchpadStrokeOutput
from ring_python_sdk.touchpad import TouchpadClick, TouchpadContact, TouchpadMove


def until(predicate):
    deadline = time.monotonic() + 1
    while not predicate() and time.monotonic() < deadline:
        time.sleep(.005)
    assert predicate()


class Mouse:
    def __init__(self):
        self.point = (10, 20)
        self.clicked = []
        self.double_targets = 0
        self.target = {"bundle": "test.editor", "pid": 123, "point": [10, 20]}
    def enable(self): pass
    def disable(self): pass
    def apply(self, batch):
        for event in batch:
            if event["kind"] == "move":
                self.point = (self.point[0] + event["dx"], self.point[1] + event["dy"])
            else:
                assert event["kind"] == "click"
                self.clicked.append((self.point, time.monotonic()))
    def double_click_target(self):
        self.double_targets += 1
        return self.target


def pointer():
    mouse, ended, ready, switches = Mouse(), threading.Event(), threading.Event(), []
    output = TouchpadMouseOutput(connection=threading.Event(), gain=1, clicks=True, invert_y=False,
        on_ready=ready.set, on_end=lambda *_: ended.set(), mouse_factory=lambda: mouse,
        on_double_click=switches.append)
    output.start()
    assert ready.wait(1)
    output.activate()
    return output, mouse, ended, switches


def test_pointer_single_is_immediate_and_following_movement_does_not_replay_it():
    output, mouse, ended, switches = pointer()
    try:
        began = time.monotonic()
        output.submit(TouchpadClick(1, began))
        until(lambda: bool(mouse.clicked))
        point, posted_at = mouse.clicked[0]
        assert point == (10, 20) and posted_at - began < .2
        output.submit(TouchpadMove(30, 40, 1, 2, time.monotonic()))
        until(lambda: mouse.point == (40, 60))
        time.sleep(.42)
        assert len(mouse.clicked) == 1 and mouse.double_targets == 0 and not switches
    finally:
        output.stop()
        assert ended.wait(1)


def test_pointer_double_switches_once_and_holds_movement_until_mode_decision():
    output, mouse, ended, switches = pointer()
    try:
        output.submit(TouchpadClick(1, time.monotonic()))
        until(lambda: len(mouse.clicked) == 1)
        output.submit(TouchpadContact("reset", 350, time.monotonic()))
        output.submit(TouchpadClick(2, time.monotonic()))
        until(lambda: bool(switches))
        assert len(mouse.clicked) == 1 and mouse.double_targets == 1
        assert switches == [mouse.target] and output.paused
        output.submit(TouchpadMove(30, 40, 1, 3, time.monotonic()))
        time.sleep(.03)
        assert mouse.point == (10, 20)
        output.activate()
        output.submit(TouchpadMove(30, 40, 1, 4, time.monotonic()))
        until(lambda: mouse.point == (40, 60))
    finally:
        output.stop()
        assert ended.wait(1)


def test_stop_and_sensor_reset_clear_pairing_without_undoing_immediate_clicks():
    output, mouse, ended, _ = pointer()
    output.submit(TouchpadClick(1, time.monotonic()))
    until(lambda: len(mouse.clicked) == 1)
    output.submit(TouchpadContact("reset", 0, time.monotonic()))
    output.submit(TouchpadClick(3, time.monotonic()))
    until(lambda: len(mouse.clicked) == 2)
    output.stop()
    assert ended.wait(1)
    assert len(mouse.clicked) == 2 and mouse.double_targets == 0


def test_stroke_single_never_confirms_and_double_exits_without_publishing_ink():
    ready, ended, switched = threading.Event(), threading.Event(), threading.Event()
    taps, strokes, hints = [], [], []
    output = TouchpadStrokeOutput(connection=threading.Event(), on_ready=ready.set,
        on_end=lambda *_: ended.set(), on_stroke=strokes.append, on_tap=lambda: taps.append(1),
        on_double_click=switched.set, on_gesture=hints.append)
    output.start()
    assert ready.wait(1)
    output.activate()
    try:
        output.submit(TouchpadClick(1, time.monotonic()))
        until(lambda: hints == ["click"])
        assert not switched.is_set() and not taps and not strokes
        time.sleep(.42)
        output.submit(TouchpadClick(2, time.monotonic()))
        until(lambda: hints == ["click", "click"])
        output.submit(TouchpadContact("reset", 350, time.monotonic()))
        output.submit(TouchpadClick(3, time.monotonic()))
        assert switched.wait(1)
        assert not output.active and not taps and not strokes
        assert hints == ["click", "click", "double-click"]
    finally:
        output.stop()
        assert ended.wait(1)


def test_click_hints_still_recognize_when_native_click_output_is_disabled():
    mouse, ready, ended, hints = Mouse(), threading.Event(), threading.Event(), []
    output = TouchpadMouseOutput(connection=threading.Event(), gain=1, clicks=False, invert_y=False,
        on_ready=ready.set, on_end=lambda *_: ended.set(), mouse_factory=lambda: mouse,
        on_double_click=lambda _: (_ for _ in ()).throw(AssertionError("must not switch")),
        on_gesture=hints.append)
    output.start()
    assert ready.wait(1)
    output.activate()
    try:
        output.submit(TouchpadClick(1, time.monotonic()))
        until(lambda: hints == ["click"])
        output.submit(TouchpadClick(2, time.monotonic()))
        until(lambda: hints == ["click", "double-click"])
        assert not mouse.clicked and mouse.double_targets == 0 and not output.paused
    finally:
        output.stop()
        assert ended.wait(1)
