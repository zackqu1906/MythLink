"""Contact-to-Quartz drag acceptance with fake input/output only."""
from dataclasses import replace
import os
import threading
import time
from types import SimpleNamespace

import pytest

from proximic_ring.touchpad.touchpad_mouse import TouchpadMouseOutput
from ring_python_sdk.touchpad.events import Raw2PointerClick, Raw2PointerContact, Raw2PointerMove, Raw2Reset
from ring_python_sdk.touchpad.macos import MacSystemMouse


def until(check):
    deadline = time.monotonic() + 1
    while not check() and time.monotonic() < deadline:
        time.sleep(.002)
    assert check()


def native(cls=MacSystemMouse, *args):
    posts, position = [], [0., 0.]
    def post(_, event):
        posts.append(event)
        if 'point' in event:
            position[:] = event['point']
    q = SimpleNamespace(kCGMouseButtonLeft=0, kCGMouseButtonRight=1,
        kCGEventLeftMouseDown='down', kCGEventLeftMouseUp='up',
        kCGEventRightMouseDown='right-down', kCGEventRightMouseUp='right-up',
        kCGEventLeftMouseDragged='drag', kCGEventMouseMoved='move',
        kCGMouseEventClickState=5, kCGHIDEventTap=6, kCGScrollEventUnitPixel=0,
        CGPreflightPostEventAccess=lambda: True,
        CGEventCreateMouseEvent=lambda _, kind, point, button: dict(kind=kind, point=point, button=button),
        CGEventSetIntegerValueField=lambda event, _, count: event.update(count=count),
        CGEventCreateScrollWheelEvent=lambda _, unit, axes, dy: dict(kind='scroll', dy=dy),
        CGEventPost=post)
    mouse = cls(*args, quartz=q, accessibility=SimpleNamespace(AXIsProcessTrusted=lambda: True))
    mouse._position = lambda: tuple(position)
    mouse._clamp = lambda x, y: (x, y)
    mouse.stop_on_escape = False
    return mouse, posts


def contact(phase='BEGIN', *, identifier=1, mode='SINGLE', start=None, end=None, **kwargs):
    now = time.monotonic()
    return Raw2PointerContact(phase, mode, identifier, 1, now,
        now if start is None else start, end_timestamp=end, session_id='test', **kwargs)


def movement(mode='SINGLE', **kwargs):
    return Raw2PointerMove(3, 4, 1, 1, time.monotonic(), mode=mode, session_id='test', **kwargs)


def click(end, *, count=1, preview=False, button='left', identifier=0):
    return Raw2PointerClick(1, time.monotonic(), button=button, count=count, session_id='test',
                            source_timestamp=end, is_preview=preview, click_id=identifier)


@pytest.fixture
def pointer():
    mouse, posts = native()
    ready, done = threading.Event(), threading.Event()
    errors, context, captured = [], [(0, 'closed')], []
    worker = TouchpadMouseOutput(connection=threading.Event(), gain=1, clicks=True, invert_y=False,
        on_ready=ready.set, on_end=lambda _, error: (errors.append(error), done.set()),
        mouse_factory=lambda: mouse, raw2=True, click_context=lambda: context[0],
        on_capture_click=captured.append)
    worker.activate()
    worker.start()
    assert ready.wait(1)
    yield worker, mouse, posts, context, captured
    worker.stop()
    assert done.wait(1)
    assert errors == [None]


def kinds(posts):
    return [e['kind'] for e in posts]


def first_tap(worker, posts):
    start = contact()
    worker.submit(start)
    end = time.monotonic()
    worker.submit(replace(start, phase='END', end_timestamp=end, timestamp=end))
    worker.submit(click(end, preview=True))
    until(lambda: kinds(posts) == ['down', 'up'])
    posts.clear()
    return end


def begin_drag(worker, posts):
    first_tap(worker, posts)
    start = contact(identifier=2)
    worker.submit(start)
    until(lambda: kinds(posts) == ['down'])
    return start


def test_normal_contact_only_moves_even_after_a_non_tap_contact(pointer):
    worker, _, posts, *_ = pointer
    for identifier in (1, 2):
        start = contact(identifier=identifier)
        worker.submit(start)
        worker.submit(movement())
        end = time.monotonic()
        worker.submit(replace(start, phase='END', end_timestamp=end, timestamp=end))
        until(lambda: len(posts) == identifier)
    assert kinds(posts) == ['move', 'move']


def test_contact_holds_through_movement_and_releases_at_last_position(pointer):
    worker, mouse, posts, *_ = pointer
    start = begin_drag(worker, posts)
    worker.submit(movement())
    until(lambda: kinds(posts) == ['down', 'drag'])
    assert mouse._drag_up is not None
    end = time.monotonic()
    worker.submit(replace(start, phase='END', end_timestamp=end, timestamp=end))
    until(lambda: kinds(posts) == ['down', 'drag', 'up'])
    worker.submit(click(end))
    worker.submit(movement())
    until(lambda: posts[-1]['kind'] == 'move')
    assert kinds(posts) == ['down', 'drag', 'up', 'move']
    assert posts[2]['point'] == (3, 4)


def test_gain_and_inversion_change_during_drag_without_releasing(pointer):
    worker, _, posts, *_ = pointer
    begin_drag(worker, posts)
    worker.configure(clicks=True, double_click=True, right_click=True, scroll=True,
                     scroll_gain=1, gain=2., invert_y=True)
    worker.submit(movement())
    until(lambda: len(posts) == 2)
    assert posts[-1]['point'] == (6., -8.)
    worker.configure(clicks=True, double_click=True, right_click=True, scroll=True,
                     scroll_gain=1, gain=.5, invert_y=False)
    worker.submit(movement())
    until(lambda: len(posts) == 3)
    assert kinds(posts) == ['down', 'drag', 'drag'] and posts[-1]['point'] == (7.5, -6.)


@pytest.mark.parametrize('double_enabled,counts', [(True, [1, 2]), (False, [1, 1])])
def test_two_taps_reuse_sdk_preview_and_do_not_emit_extra_clicks(pointer, double_enabled, counts):
    worker, _, posts, *_ = pointer
    worker.configure(clicks=True, double_click=double_enabled, right_click=True, scroll=True, scroll_gain=1)
    for identifier in (1, 2):
        start = contact(identifier=identifier)
        worker.submit(start)
        end = time.monotonic()
        worker.submit(replace(start, phase='END', end_timestamp=end, timestamp=end))
        worker.submit(click(end, preview=identifier == 1, count=identifier))
        until(lambda: len(posts) == identifier * 2)
    worker.submit(movement())
    until(lambda: len(posts) == 5)
    assert kinds(posts) == ['down', 'up', 'down', 'up', 'move']
    assert [e['count'] for e in posts[:4]] == [counts[0]] * 2 + [counts[1]] * 2


def test_unknown_contact_waits_for_single_finger_classification(pointer):
    worker, _, posts, *_ = pointer
    first_tap(worker, posts)
    start = contact(identifier=2, mode='UNKNOWN')
    worker.submit(start)
    worker.submit(movement())
    until(lambda: kinds(posts) == ['move'])
    worker.submit(replace(start, phase='CLASSIFIED', mode='SINGLE'))
    worker.submit(movement())
    until(lambda: len(posts) == 3)
    assert kinds(posts) == ['move', 'down', 'drag']


def test_two_finger_scroll_and_right_click_never_hold_left(pointer):
    worker, _, posts, *_ = pointer
    first_tap(worker, posts)
    start = contact(identifier=2, mode='UNKNOWN')
    worker.submit(start)
    worker.submit(replace(start, phase='CLASSIFIED', mode='TWO_FINGER'))
    worker.submit(movement('TWO_FINGER'))
    end = time.monotonic()
    worker.submit(replace(start, phase='END', mode='TWO_FINGER', end_timestamp=end, timestamp=end))
    worker.submit(click(end, button='right', identifier=1))
    until(lambda: len(posts) == 3)
    assert kinds(posts) == ['scroll', 'right-down', 'right-up']


@pytest.mark.parametrize('reason', ['stop', 'disconnect', 'pause', 'capture', 'toggle', 'drag_toggle', 'reset', 'cancel', 'stale_up'])
def test_every_interruption_releases_without_another_move(pointer, reason):
    worker, mouse, posts, context, _ = pointer
    start = begin_drag(worker, posts)
    if reason == 'stop': worker.stop()
    elif reason == 'disconnect': worker.connection.set()
    elif reason == 'pause': worker.paused = True
    elif reason == 'capture': context[0] = (1, 'capture')
    elif reason == 'toggle':
        worker.configure(clicks=False, double_click=True, right_click=True, scroll=True, scroll_gain=1)
    elif reason == 'drag_toggle':
        worker.configure(clicks=True, double_click=True, right_click=True, scroll=True, scroll_gain=1, tap_drag=False)
    elif reason == 'reset':
        worker.submit(Raw2Reset('reset', 0, time.monotonic(), session_id='test', generation=1))
    else:
        worker.submit(replace(start, phase='CANCEL' if reason == 'cancel' else 'END',
                              end_timestamp=time.monotonic(), timestamp=time.monotonic()-1))
    until(lambda: kinds(posts) == ['down', 'up'])
    assert mouse._drag_up is None


def test_disabling_drag_keeps_formal_clicks_and_motion(pointer):
    worker, _, posts, *_ = pointer
    worker.configure(clicks=True, double_click=True, right_click=True, scroll=True, scroll_gain=1, tap_drag=False)
    start = contact()
    worker.submit(start)
    worker.submit(movement())
    end = time.monotonic()
    worker.submit(replace(start, phase='END', end_timestamp=end, timestamp=end))
    worker.submit(click(end, preview=True))
    worker.submit(contact(identifier=2))
    worker.submit(movement())
    until(lambda: len(posts) == 2)
    assert kinds(posts) == ['move', 'move']
    worker.submit(click(time.monotonic(), count=2))
    until(lambda: len(posts) == 6)
    assert kinds(posts)[2:] == ['down', 'up', 'down', 'up']


def test_formal_single_during_second_hold_does_not_interrupt_drag(pointer):
    worker, _, posts, *_ = pointer
    end = first_tap(worker, posts)
    worker.submit(contact(identifier=2))
    worker.submit(click(end))
    worker.submit(movement())
    until(lambda: len(posts) == 2)
    assert kinds(posts) == ['down', 'drag']


@pytest.mark.parametrize('singles,doubles,expected', [
    (True, True, ['click', 2]), (True, False, ['click', 1]),
    (False, True, ['click', 2]), (False, False, [1]),
])
def test_drag_is_independent_of_click_switches(singles, doubles, expected):
    from proximic_ring.touchpad.touchpad_drag import TouchpadDrag
    output = []
    drag = TouchpadDrag(output.append, lambda: output.append('up'), lambda _: output.append('click'))
    start = contact(start=10.)
    drag.contact(start, enabled=True, doubles=doubles, context='test')
    drag.contact(replace(start, phase='END', end_timestamp=10.1), enabled=True, doubles=doubles, context='test')
    drag.preview(click(10.1, preview=True), 'test', singles=singles, now=10.11)
    second = replace(contact(identifier=2, start=10.2), timestamp=10.21)
    drag.contact(second, enabled=True, doubles=doubles, context='test')
    assert output == expected
    drag.contact(replace(second, phase='END', end_timestamp=11.), enabled=True, doubles=doubles, context='test')
    assert output == expected + ['up']


@pytest.mark.parametrize('gap', [.24, .26])
def test_second_down_uses_pairing_window_before_delayed_classification(gap):
    from proximic_ring.touchpad.touchpad_drag import TouchpadDrag
    output = []
    drag = TouchpadDrag(output.append, lambda: output.append('up'), lambda _: output.append('click'))
    start = contact(start=10.)
    drag.contact(start, enabled=True, doubles=True, context='test')
    drag.contact(replace(start, phase='END', end_timestamp=10.1), enabled=True, doubles=True, context='test')
    drag.preview(click(10.1, preview=True), 'test', singles=True, now=10.11)
    second = replace(contact(identifier=2, mode='UNKNOWN', start=10.1+gap), timestamp=10.1+gap)
    drag.contact(second, enabled=True, doubles=True, context='test')
    drag.contact(replace(second, phase='CLASSIFIED', mode='SINGLE', timestamp=10.5),
                 enabled=True, doubles=True, context='test')
    assert output == (['click', 2] if gap < .25 else ['click'])


def test_capture_keeps_sdk_click_without_sending_button_down(pointer):
    worker, _, posts, context, captured = pointer
    context[0] = (1, 'capture')
    start = contact()
    worker.submit(start)
    end = time.monotonic()
    worker.submit(replace(start, phase='END', end_timestamp=end, timestamp=end))
    worker.submit(click(end))
    until(lambda: captured == [context[0]])
    assert not posts


def test_expired_down_and_orphan_drag_never_post():
    mouse, posts = native()
    mouse.enable()
    try:
        mouse.apply([dict(kind='button_down', expires_at=time.monotonic()-1),
                     dict(kind='drag', dx=3, dy=4)])
        mouse.release_drag()
        assert not posts
    finally:
        mouse.disable()


def test_native_exception_balances_down():
    mouse, posts = native()
    mouse.enable()
    with pytest.raises(ValueError):
        mouse.apply([dict(kind='button_down'), dict(kind='drag', dx=float('nan'), dy=0)])
    assert kinds(posts) == ['down', 'up']
    assert not mouse.enabled


@pytest.mark.mac_only
def test_ipc_release_is_allowed_after_disable_without_permission_probe(monkeypatch):
    import proximic_ring.platforms.macos.native_access_worker as native_worker
    monkeypatch.setattr(native_worker, 'read_permission_state',
                        lambda: pytest.fail('cleanup must not request permissions'))
    mouse, posts = native()
    dispatcher = native_worker.Dispatcher()
    dispatcher.touchpad_mouse = mouse
    mouse.enable()
    try:
        dispatcher.handle(dict(operation='touchpad_apply', created=time.monotonic(),
                               events=[dict(kind='button_down')]))
        mouse.enabled = False
        dispatcher.handle(dict(operation='touchpad_release'))
        dispatcher.handle(dict(operation='touchpad_release'))
        assert kinds(posts) == ['down', 'up']
    finally:
        mouse.disable()


@pytest.mark.mac_only
def test_native_idle_gate_releases_on_stop_or_parent_pipe_close():
    from proximic_ring.platforms.macos.native_touchpad import WorkerTouchpadMouse
    read_fd, write_fd = os.pipe()
    mouse, posts = native(WorkerTouchpadMouse, read_fd)
    try:
        mouse.enable()
        mouse.apply([dict(kind='button_down')])
        os.close(write_fd)
        write_fd = None
        until(lambda: kinds(posts) == ['down', 'up'])
        assert not mouse.enabled
    finally:
        mouse.disable()
        os.close(read_fd)
        if write_fd is not None: os.close(write_fd)


def sdk_engine(emit):
    from ring_mac_sdk.interaction import Engine, Recognition
    from ring_mac_sdk.touchpad import Touchpad
    from ring_python_sdk.touchpad.raw2 import Raw2Stream
    stream = Raw2Stream.__new__(Raw2Stream)
    stream.closed, stream.pointer, stream._key = False, emit, ('test', 0)
    receiver = Touchpad.__new__(Touchpad)
    receiver.accepting, receiver.error = True, None
    receiver.session, receiver.generation, receiver.sequence, receiver.ordinal = 'test', 0, 0, 20
    receiver.emit = stream._pointer_event
    stream.receiver = receiver
    return Engine(receiver._event), Recognition


def test_real_sdk_engine_contact_and_preview_reach_pointer_adapter():
    events = []
    engine, Recognition = sdk_engine(events.append)
    base = time.monotonic_ns()-190_000_000
    for frame in range(36):
        stamp = base + frame*5_000_000
        engine.recognize(Recognition(frame, stamp, stamp, 0, 0, float(15 <= frame < 27), 0), stamp)
    contacts = [e for e in events if e.kind == 'pointer_contact']
    assert [e.phase for e in contacts] == ['BEGIN', 'CLASSIFIED', 'END']
    assert [e.mode for e in contacts] == ['UNKNOWN', 'SINGLE', 'SINGLE']
    preview = next(e for e in events if e.kind == 'click')
    assert preview.is_preview and preview.source_timestamp == contacts[-1].end_timestamp


@pytest.mark.parametrize('scenario', ['normal_move', 'tap_hold_drag', 'double_tap'])
def test_real_sdk_sequence_to_native_pointer(monkeypatch, scenario):
    mouse, posts = native()
    now = [time.monotonic()]
    monkeypatch.setattr(time, 'monotonic', lambda: now[0])
    worker = TouchpadMouseOutput(connection=threading.Event(), gain=1, clicks=True, invert_y=False,
        on_ready=lambda: None, on_end=lambda *_: None, raw2=True)
    worker.mouse = mouse
    worker.activate()
    def emit(event):
        from proximic_ring.touchpad.click_diagnostics import ClickTrace
        trace = ClickTrace(event) if event.kind == 'click' and not event.is_preview else None
        value = worker._raw2_event(event, (0, 'closed'), trace)
        if value:
            worker._apply([value])
    engine, Recognition = sdk_engine(emit)
    base = int(now[0]*1e9)
    mouse.enable()
    try:
        for frame in range(140):
            stamp = base + frame*5_000_000
            now[0] = stamp/1e9
            first = 15 <= frame < (100 if scenario == 'normal_move' else 27)
            second = scenario != 'normal_move' and 44 <= frame < (115 if scenario == 'tap_hold_drag' else 56)
            moving = (scenario == 'normal_move' and 30 <= frame < 95 or
                      scenario == 'tap_hold_drag' and 60 <= frame < 110)
            engine.recognize(Recognition(frame, stamp, stamp, .4 if moving else 0, 0,
                                         float(first or second), 0), stamp)
        if scenario == 'normal_move':
            assert posts and set(kinds(posts)) == {'move'}
        elif scenario == 'tap_hold_drag':
            assert kinds(posts)[:3] == ['down', 'up', 'down']
            assert set(kinds(posts)[3:-1]) == {'drag'}
            assert kinds(posts)[-1] == 'up'
        else:
            assert kinds(posts) == ['down', 'up', 'down', 'up']
            assert [e['count'] for e in posts] == [1, 1, 2, 2]
        assert not worker.drag.held and mouse._drag_up is None
    finally:
        mouse.disable()
