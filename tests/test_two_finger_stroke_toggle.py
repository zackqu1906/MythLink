"""Real output workers, synthetic Raw2 events; never post system input."""
from dataclasses import replace
import threading
import time

import pytest

from proximic_ring.touchpad.touchpad_mouse import TouchpadMouseOutput
from proximic_ring.touchpad.touchpad_strokes import TouchpadStrokeOutput
from ring_python_sdk.touchpad import TouchpadClick
from ring_python_sdk.touchpad.events import Raw2PointerClick, Raw2PointerContact, Raw2PointerMove, Raw2Reset
from test_touchpad_click_routing import until


def click(identifier, *, button='right', count=1, generation=0):
    now = time.monotonic()
    return Raw2PointerClick(identifier, now, button=button, count=count, click_id=identifier,
                            session_id='ring', generation=generation, source_timestamp=now)


class Mouse:
    target = dict(bundle='test.editor', pid=123)

    def __init__(self): self.calls = []
    def enable(self): pass
    def disable(self): pass
    def apply(self, events):
        self.calls.extend(events)
        return dict(reason='posted', clicks_posted=sum(e['kind'] == 'click' for e in events))
    def double_click_target(self): return self.target


@pytest.fixture
def pointer():
    mouse, switched, errors, hints = Mouse(), [], [], []
    context = [(0, 'closed')]
    worker = TouchpadMouseOutput(connection=threading.Event(), gain=1, clicks=True, invert_y=False,
        raw2=True, on_ready=lambda: None, on_end=lambda _, error: errors.append(error),
        mouse_factory=lambda: mouse, on_double_click=switched.append, on_gesture=hints.append,
        click_context=lambda: context[0])
    worker.activate()
    worker.start()
    yield worker, mouse, switched, hints, context
    worker.stop()
    worker.thread.join(1)
    assert errors == [None]


@pytest.mark.parametrize('right_enabled', [True, False])
def test_two_finger_double_enters_without_opening_context_menu(pointer, right_enabled):
    worker, mouse, switched, hints, _ = pointer
    worker.configure(clicks=False, double_click=False, right_click=right_enabled, scroll=True, scroll_gain=1)
    worker.submit(click(1))
    until(lambda: worker._pending_right is not None)
    assert not mouse.calls
    worker.submit(click(2))
    until(lambda: bool(switched))
    assert switched == [Mouse.target] and worker.paused
    assert not mouse.calls and hints == ['two-finger-double-click']
    worker.submit(click(3))
    worker.submit(Raw2PointerMove(10, 10, 1, 10, time.monotonic(), session_id='ring'))
    time.sleep(.05)
    assert switched == [Mouse.target] and not mouse.calls


def test_single_right_waits_but_single_finger_double_is_still_system_double(pointer):
    worker, mouse, switched, _, _ = pointer
    worker.submit(click(1, button='left', count=2))
    until(lambda: len(mouse.calls) == 1)
    began = time.monotonic()
    worker.submit(click(2))
    until(lambda: worker._pending_right is not None)
    assert len(mouse.calls) == 1
    until(lambda: len(mouse.calls) == 2)
    assert time.monotonic() - began >= .38
    assert [(e['button'], e['count']) for e in mouse.calls] == [('left', 2), ('right', 1)]
    assert not switched


@pytest.mark.parametrize('boundary', ['reset', 'capture', 'stop', 'disable_right'])
def test_pending_right_cannot_leak_across_input_boundary(pointer, boundary):
    worker, mouse, switched, _, context = pointer
    worker.submit(click(1))
    until(lambda: worker._pending_right is not None)
    if boundary == 'reset':
        worker.submit(Raw2Reset('reset', 0, time.monotonic(), session_id='ring', generation=1))
    elif boundary == 'capture':
        context[0] = (1, 'capture')
    elif boundary == 'stop':
        worker.stop()
    else:
        worker.configure(clicks=True, double_click=True, right_click=False, scroll=True, scroll_gain=1)
    time.sleep(.45)
    assert not mouse.calls and not switched


def test_duplicate_and_expired_clicks_cannot_complete_pair(pointer):
    worker, mouse, switched, _, _ = pointer
    first = click(1)
    worker.submit(first)
    worker.submit(first)
    old = click(2)
    worker.submit(replace(old, timestamp=old.timestamp-.3, source_timestamp=old.timestamp-.3))
    until(lambda: len(mouse.calls) == 1)
    assert not switched and mouse.calls[0]['button'] == 'right'


def test_single_finger_contact_breaks_two_finger_pair(pointer):
    worker, mouse, switched, _, _ = pointer
    worker.submit(click(1))
    until(lambda: worker._pending_right is not None)
    now = time.monotonic()
    worker.submit(Raw2PointerContact('CLASSIFIED', 'SINGLE', 2, 20, now, now, session_id='ring'))
    until(lambda: len(mouse.calls) == 1)
    worker.submit(click(2))
    until(lambda: len(mouse.calls) == 2)
    assert not switched


def test_stream_reset_during_target_lookup_cannot_start_handwriting(pointer):
    worker, mouse, switched, _, _ = pointer
    def target():
        worker.submit(Raw2Reset('reset', 0, time.monotonic(), session_id='ring', generation=1))
        return Mouse.target
    mouse.double_click_target = target
    worker.submit(click(1))
    worker.submit(click(2))
    until(lambda: worker._stream_key == ('ring', 1))
    assert not switched and not mouse.calls and not worker.paused


def test_stroke_exits_only_on_raw2_two_finger_double():
    switched, taps, strokes, hints, errors = [], [], [], [], []
    worker = TouchpadStrokeOutput(connection=threading.Event(), raw2=True,
        on_ready=lambda: None, on_end=lambda _, error: errors.append(error),
        on_stroke=strokes.append, on_tap=lambda: taps.append(True),
        on_double_click=lambda: switched.append(True), on_gesture=hints.append)
    worker.activate()
    worker.start()
    try:
        worker.submit(TouchpadClick(1, time.monotonic()))
        worker.submit(TouchpadClick(2, time.monotonic()))
        worker.submit_pointer(click(1, button='left', count=2))
        time.sleep(.05)
        assert worker.active and not switched
        worker.submit_pointer(click(2))
        worker.submit_pointer(click(3))
        until(lambda: bool(switched))
        assert not worker.active and not taps and not strokes
        assert hints == ['two-finger-double-click']
    finally:
        worker.stop()
        worker.thread.join(1)
    assert errors == [None]


@pytest.mark.parametrize('second', ['single', 'double', 'reset', 'stop'])
def test_stroke_two_finger_single_waits_for_double_click_arbitration(second):
    taps, switched, errors = [], [], []
    worker = TouchpadStrokeOutput(connection=threading.Event(), raw2=True,
        on_ready=lambda: None, on_end=lambda _, error: errors.append(error),
        on_stroke=lambda _: pytest.fail('tap became ink'), on_tap=lambda: pytest.fail('two fingers became one'),
        on_two_finger_tap=lambda: taps.append(True), on_double_click=lambda: switched.append(True))
    worker.activate()
    worker.start()
    try:
        worker.submit_pointer(click(1))
        until(lambda: worker.two_finger_clicks.first is not None)
        assert not taps
        if second == 'double':
            worker.submit_pointer(click(2))
        elif second == 'reset':
            worker.submit(Raw2Reset('reset', 0, time.monotonic(), session_id='ring', generation=1))
        elif second == 'stop':
            worker.stop()
        time.sleep(.46)
        assert taps == ([True] if second == 'single' else [])
        assert switched == ([True] if second == 'double' else [])
    finally:
        worker.stop()
        worker.thread.join(1)
    assert errors == [None]
