"""Contact-to-Quartz drag acceptance with fake input/output only."""
from dataclasses import replace
import asyncio
import json
import logging
import os
import threading
import time
from types import SimpleNamespace

import pytest

from proximic_ring.touchpad.touchpad_mouse import TouchpadMouseOutput
from proximic_ring.touchpad import click_diagnostics as diagnostics
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
        kCGMouseEventClickState='count', kCGMouseEventDeltaX='delta_x', kCGMouseEventDeltaY='delta_y',
        kCGHIDEventTap=6, kCGScrollEventUnitPixel=0,
        CGPreflightPostEventAccess=lambda: True,
        CGEventCreateMouseEvent=lambda _, kind, point, button: dict(kind=kind, point=point, button=button),
        CGEventSetIntegerValueField=lambda event, field, value: event.update({field: value}),
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


def movement(mode='SINGLE', *, dx=7, dy=0, identifier=1, **kwargs):
    return Raw2PointerMove(dx, dy, 1, 1, time.monotonic(), mode=mode, session_id='test',
                           episode_id=identifier, **kwargs)


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


@pytest.fixture
def drag_records(caplog):
    caplog.set_level(logging.INFO, logger=diagnostics.__name__)
    def read():
        assert diagnostics.flush_click_diagnostics()
        return [json.loads(r.getMessage().split('[TOUCHPAD_DRAG] ', 1)[1])
                for r in caplog.records if '[TOUCHPAD_DRAG] ' in r.getMessage()]
    yield read
    diagnostics.flush_click_diagnostics()


def begin_drag(worker, posts):
    start = contact()
    worker.submit(start)
    until(lambda: worker.drag.state == 'ready')
    assert not posts  # A completed dwell is not a mouse down.
    worker.submit(movement())
    until(lambda: kinds(posts) == ['down', 'drag'])
    assert posts[0]['count'] == 1
    return start


def test_normal_motion_never_turns_into_drag_during_a_pause(pointer):
    worker, _, posts, *_ = pointer
    worker.submit(contact())
    worker.submit(movement())
    until(lambda: kinds(posts) == ['move'])
    time.sleep(.42)
    worker.submit(movement())
    until(lambda: len(posts) == 2)
    assert kinds(posts) == ['move', 'move'] and worker.drag.state == 'moving'


def test_contact_holds_through_movement_and_releases_at_last_position(pointer):
    worker, mouse, posts, *_ = pointer
    start = begin_drag(worker, posts)
    worker.submit(movement(dx=3, dy=4))
    until(lambda: kinds(posts) == ['down', 'drag', 'drag'])
    end = time.monotonic()
    worker.submit(replace(start, phase='END', end_timestamp=end, timestamp=end))
    until(lambda: posts[-1]['kind'] == 'up')
    worker.submit(click(end))
    worker.submit(movement())
    until(lambda: posts[-1]['kind'] == 'move')
    assert kinds(posts) == ['down', 'drag', 'drag', 'up', 'move']
    assert posts[3]['point'] == (10, 4) and mouse._drag_up is None


def test_gain_and_inversion_change_during_drag_without_releasing(pointer):
    worker, _, posts, *_ = pointer
    begin_drag(worker, posts)
    worker.configure(clicks=True, double_click=True, right_click=True, scroll=True,
                     scroll_gain=1, gain=2., invert_y=True, dwell_ms=600)
    worker.submit(movement(dx=3, dy=4))
    until(lambda: len(posts) == 3)
    assert posts[-1]['point'] == (13., -8.)
    worker.configure(clicks=True, double_click=True, right_click=True, scroll=True,
                     scroll_gain=1, gain=.5, invert_y=False)
    worker.submit(movement(dx=3, dy=4))
    until(lambda: len(posts) == 4)
    assert kinds(posts) == ['down', 'drag', 'drag', 'drag'] and posts[-1]['point'] == (14.5, -6.)


def test_two_down_sequence_no_longer_starts_drag(pointer):
    worker, _, posts, *_ = pointer
    first = contact()
    end = time.monotonic()
    worker.submit(first)
    worker.submit(replace(first, phase='END', end_timestamp=end, timestamp=end))
    worker.submit(click(end, preview=True))
    second = contact(identifier=2)
    worker.submit(second)
    worker.submit(movement(identifier=2))
    until(lambda: posts)
    assert kinds(posts) == ['move'] and not worker.drag.held


@pytest.mark.parametrize('count', [1, 2])
def test_formal_sdk_clicks_still_post_once(pointer, count):
    worker, _, posts, *_ = pointer
    start = contact()
    end = time.monotonic()
    worker.submit(start)
    worker.submit(replace(start, phase='END', end_timestamp=end, timestamp=end))
    worker.submit(click(end, preview=True))
    worker.submit(click(end, count=count))
    worker.submit(movement())
    until(lambda: posts and posts[-1]['kind'] == 'move')
    assert kinds(posts) == ['down', 'up'] * count + ['move']


def test_two_finger_scroll_and_right_click_never_hold_left(pointer):
    worker, _, posts, *_ = pointer
    start = contact(mode='UNKNOWN')
    worker.submit(start)
    worker.submit(replace(start, phase='CLASSIFIED', mode='TWO_FINGER'))
    worker.submit(movement('TWO_FINGER', dx=0, dy=7))
    end = time.monotonic()
    worker.submit(replace(start, phase='END', mode='TWO_FINGER', end_timestamp=end, timestamp=end))
    worker.submit(click(end, button='right'))
    until(lambda: len(posts) == 3)
    assert kinds(posts) == ['scroll', 'right-down', 'right-up']


@pytest.mark.parametrize('reason', ['stop', 'disconnect', 'pause', 'capture', 'toggle', 'drag_toggle', 'reset', 'input_gap', 'cancel', 'stale_up'])
def test_every_interruption_releases_without_another_move(pointer, reason, drag_records):
    worker, mouse, posts, context, _ = pointer
    start = begin_drag(worker, posts)
    if reason == 'stop': worker.stop()
    elif reason == 'disconnect': worker.connection.set()
    elif reason == 'pause': worker.paused = True
    elif reason == 'capture': context[0] = (1, 'capture')
    elif reason == 'toggle':
        worker.configure(clicks=False, double_click=True, right_click=True, scroll=True, scroll_gain=1)
    elif reason == 'drag_toggle':
        worker.configure(clicks=True, double_click=True, right_click=True, scroll=True, scroll_gain=1, dwell_drag=False)
    elif reason in {'reset', 'input_gap'}:
        worker.submit(Raw2Reset('reset', 0, time.monotonic(), session_id='test',
                                generation=1 if reason == 'reset' else 0))
    else:
        worker.submit(replace(start, phase='CANCEL' if reason == 'cancel' else 'END',
                              end_timestamp=time.monotonic(), timestamp=time.monotonic()-1))
    until(lambda: kinds(posts) == ['down', 'drag', 'up'])
    assert mouse._drag_up is None
    started, ended = drag_records()
    assert started['stage'] == 'start' and ended['stage'] == 'end'
    assert started['drag_id'] == ended['drag_id']
    assert ended['reason'] == dict(stop='output_stopped', disconnect='disconnected',
        pause='output_paused', capture='input_owner_changed', toggle='single_click_changed',
        drag_toggle='drag_setting_changed', reset='stream_changed', input_gap='input_gap',
        cancel='contact_event_expired', stale_up='contact_event_expired')[reason]
    if reason in {'cancel', 'stale_up'}:
        assert ended['event_phase'] == ('CANCEL' if reason == 'cancel' else 'END')
        assert ended['event_age_ms'] >= 1000
    if reason == 'input_gap':
        worker.submit(click(start.timestamp))
        worker.submit(movement())
        until(lambda: posts[-1]['kind'] == 'move')
        assert kinds(posts) == ['down', 'drag', 'up', 'move']


def test_disabling_drag_keeps_formal_clicks_and_motion(pointer):
    worker, _, posts, *_ = pointer
    worker.configure(clicks=True, double_click=True, right_click=True, scroll=True, scroll_gain=1, dwell_drag=False)
    worker.submit(contact())
    worker.submit(movement())
    worker.submit(click(time.monotonic(), count=2))
    until(lambda: len(posts) == 5)
    assert kinds(posts) == ['move', 'down', 'up', 'down', 'up']


def test_deferred_click_during_hold_does_not_interrupt_drag(pointer):
    worker, _, posts, *_ = pointer
    begin_drag(worker, posts)
    worker.submit(click(time.monotonic()-.5))
    worker.submit(movement())
    until(lambda: len(posts) == 3)
    assert kinds(posts) == ['down', 'drag', 'drag']


@pytest.mark.parametrize('singles,doubles', [(True, True), (True, False), (False, True), (False, False)])
def test_drag_is_independent_of_click_switches(pointer, singles, doubles):
    worker, _, posts, *_ = pointer
    worker.configure(clicks=singles, double_click=doubles, right_click=True, scroll=True, scroll_gain=1)
    begin_drag(worker, posts)
    assert [p['count'] for p in posts] == [1, 1]


def dwell_model(ms=400, mode='SINGLE'):
    from proximic_ring.touchpad.touchpad_drag import TouchpadDrag
    signals = []
    drag = TouchpadDrag(lambda: signals.append('release'), lambda: signals.append('ready'))
    start = replace(contact(mode=mode, start=10.), timestamp=10.)
    drag.contact(start, enabled=True, dwell_ms=ms)
    return drag, signals, start


def sample(t, dx=0., dy=0., identifier=1):
    return replace(movement(dx=dx, dy=dy, identifier=identifier), timestamp=t)


@pytest.mark.parametrize('trigger,reason', [
    ('END', 'sdk_end'), ('CANCEL', 'sdk_cancel'),
    ('stale_motion', 'motion_event_expired'), ('other_episode', 'contact_mismatch'),
    ('new_contact', 'contact_replaced'), ('two_finger', 'two_finger'),
    ('out_of_order', 'motion_out_of_order'), ('invalid', 'invalid_motion'),
])
def test_drag_end_log_identifies_the_event_that_released_the_button(monkeypatch, drag_records, trigger, reason):
    now = [10.]
    monkeypatch.setattr(time, 'monotonic', lambda: now[0])
    worker = TouchpadMouseOutput(connection=threading.Event(), gain=1, clicks=True, invert_y=False,
        on_ready=lambda: None, on_end=lambda *_: None, raw2=True)
    mouse, posts = native()
    worker.mouse = mouse
    worker.activate()
    mouse.enable()
    try:
        worker._raw2_event(contact(), (0, 'closed'), None)
        now[0] = 10.5
        worker._apply([worker._raw2_event(movement(), None, None)])
        # Large, repeated changes must not produce end records of their own.
        for i in range(100):
            now[0] += .005
            value = worker._raw2_event(movement(dx=600 if i%2 else -600, dy=400), None, None)
            worker._apply([value])
        assert worker.drag.held and 'up' not in kinds(posts)
        assert [r['stage'] for r in drag_records()] == ['start']
        now[0] += .01
        if trigger in {'END', 'CANCEL'}:
            event = contact(trigger, end=now[0])
        elif trigger == 'new_contact':
            event = contact(identifier=2)
        else:
            event = movement(mode='TWO_FINGER' if trigger == 'two_finger' else 'SINGLE',
                             identifier=2 if trigger == 'other_episode' else 1,
                             dx=float('nan') if trigger == 'invalid' else 8)
            if trigger in {'stale_motion', 'out_of_order'}:
                event = replace(event, timestamp=now[0]-(.25 if trigger == 'stale_motion' else .02))
        if trigger == 'invalid':
            with pytest.raises(ValueError, match='Nonfinite'):
                worker._raw2_event(event, (0, 'closed'), None)
        else:
            worker._raw2_event(event, (0, 'closed'), None)
        worker.drag.cancel()  # Repeated cleanup must not duplicate the end log.
        started, ended = drag_records()
        assert started['drag_id'] == ended['drag_id']
        assert ended['stage'] == 'end' and ended['reason'] == reason
        assert ended['episode_id'] == 1 and ended['event_episode_id'] == event.episode_id
        assert ended['event_timestamp'] == event.timestamp and ended['held_ms'] > 0
        assert ended['event_phase'] == getattr(event, 'phase', None)
        assert ended['release_requested'] and kinds(posts).count('up') == 1
        if trigger == 'END':
            assert ended['event_end_timestamp'] == now[0]
        if trigger == 'stale_motion':
            assert ended['event_age_ms'] == 250 and ended['age_limit_ms'] == 200
        if trigger == 'invalid':
            assert ended['dx'] is None
    finally:
        mouse.disable()


def test_failed_drag_logger_cannot_prevent_release():
    drag, signals, _ = dwell_model()
    def broken(_):
        raise OSError('unavailable diagnostic sink')
    drag.record = broken
    assert drag.move(sample(10.5, 7))[0] == 'drag'
    drag.cancel()
    assert signals == ['ready', 'release'] and not drag.held


@pytest.mark.parametrize('ms', [300, 400, 600])
def test_dwell_boundary_and_no_mouse_down_until_real_motion(ms):
    drag, signals, _ = dwell_model(ms)
    drag.tick(10+ms/1000-.001)
    assert not signals
    drag.tick(10+ms/1000)
    drag.tick(12.)
    assert signals == ['ready'] and not drag.held
    assert drag.move(sample(10+ms/1000, 3)) is None
    assert drag.move(sample(10+ms/1000+.01, 4)) == ('drag', 7., 0., True)


def test_small_back_and_forth_jitter_is_buffered_but_large_path_is_motion():
    drag, signals, _ = dwell_model()
    for i in range(8):
        assert drag.move(sample(10+.01*i, 1 if i%2==0 else -1)) is None
    assert drag.move(sample(10.09, 1)) == ('move', 1., 0., False)
    drag.tick(11.)
    assert not signals and drag.state == 'moving'
    assert drag.move(sample(11.1, 7)) == ('move', 7, 0., False)


def test_slow_drift_after_readiness_does_not_accumulate_into_drag():
    drag, signals, _ = dwell_model()
    # 0.2 px per 5 ms, 4 px per 100 ms: above 100 px in total, still no drag.
    for i in range(700):
        assert drag.move(sample(10.4+.005*i, .2)) is None
    assert signals == ['ready'] and not drag.held
    assert drag.move(sample(14., 7)) == ('drag', 7., 0., True)


def test_delayed_pre_deadline_motion_can_disqualify_readiness():
    drag, _, _ = dwell_model()
    drag.tick(10.41)
    assert drag.move(sample(10.35, 7)) == ('move', 7., 0., False)
    assert drag.move(sample(10.5, 8)) == ('move', 8, 0., False)


def test_unknown_or_two_finger_contact_cannot_arm():
    drag, signals, start = dwell_model(mode='UNKNOWN')
    drag.tick(11.)
    assert not signals
    drag.contact(replace(start, phase='CLASSIFIED', mode='TWO_FINGER'), enabled=True, dwell_ms=400)
    drag.tick(12.)
    assert not signals and not drag.held


def test_lifting_before_moving_does_not_click_or_drag():
    drag, signals, start = dwell_model()
    drag.tick(10.5)
    drag.contact(replace(start, phase='END', end_timestamp=10.6), enabled=True, dwell_ms=400)
    assert signals == ['ready'] and not drag.held
    assert drag.move(sample(10.7, 8)) == ('move', 8, 0., False)


@pytest.mark.parametrize('cancelled', [False, True])
def test_deferred_button_down_preserves_batch_order_and_cancellation(monkeypatch, cancelled):
    mouse, posts = native()
    now = [10.]
    monkeypatch.setattr(time, 'monotonic', lambda: now[0])
    worker = TouchpadMouseOutput(connection=threading.Event(), gain=1, clicks=True, invert_y=False,
        on_ready=lambda: None, on_end=lambda *_: None, raw2=True)
    worker.mouse = mouse
    worker.activate()
    mouse.enable()
    try:
        move = dict(kind='move', dx=7, dy=0)
        now[0] = 10.1
        worker._raw2_event(contact(), (0, 'closed'), None)
        now[0] = 10.6
        drag = worker._raw2_event(sample(10.6, 7), None, None)
        assert not posts and drag['begin_drag']
        if cancelled:
            worker.drag.cancel()
        worker._apply([move, drag])
        assert kinds(posts) == (['move'] if cancelled else ['move', 'down', 'drag'])
        if not cancelled:
            assert posts[1]['point'] == (7, 0) and posts[2]['point'] == (14, 0)
    finally:
        mouse.disable()


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
    from ring_mac_sdk.types import TouchpadStatus
    from ring_python_sdk.touchpad.raw2 import Raw2Stream, StrokeTouchpad
    stream = Raw2Stream.__new__(Raw2Stream)
    stream.closed, stream.pointer, stream._key = False, emit, ('test', 0)
    stream.transport = None
    receiver = StrokeTouchpad(stream, None)
    receiver.accepting = True
    receiver.session, receiver.generation, receiver.sequence, receiver.ordinal = 'test', 0, 0, 20
    receiver.status = TouchpadStatus('RUNNING', session_id='test')
    stream.receiver = receiver
    return Engine(receiver._event), Recognition, receiver


def test_real_sdk_engine_contact_and_preview_reach_pointer_adapter():
    async def run():
        events = []
        engine, Recognition, receiver = sdk_engine(events.append)
        base = time.monotonic_ns()-190_000_000
        for frame in range(36):
            stamp = base + frame*5_000_000
            engine.recognize(Recognition(frame, stamp, stamp, 0, 0, float(15 <= frame < 27), 0), stamp)
        await receiver.delivery.task
        contacts = [e for e in events if e.kind == 'pointer_contact']
        assert [e.phase for e in contacts] == ['BEGIN', 'CLASSIFIED', 'END']
        assert [e.mode for e in contacts] == ['UNKNOWN', 'SINGLE', 'SINGLE']
        preview = next(e for e in events if e.kind == 'click')
        assert preview.is_preview and preview.source_timestamp == contacts[-1].end_timestamp
    asyncio.run(run())


@pytest.mark.parametrize('scenario', ['normal_move', 'dwell_drag', 'double_tap'])
def test_real_sdk_sequence_to_native_pointer(monkeypatch, scenario):
    async def run():
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
        engine, Recognition, receiver = sdk_engine(emit)
        base = int(now[0]*1e9)
        mouse.enable()
        try:
            for frame in range(250):
                stamp = base + frame*5_000_000
                now[0] = stamp/1e9
                first = 15 <= frame < (220 if scenario in {'normal_move', 'dwell_drag'} else 27)
                second = scenario == 'double_tap' and 44 <= frame < 56
                moving = (scenario == 'normal_move' and 30 <= frame < 95 or
                          scenario == 'dwell_drag' and 120 <= frame < 210)
                engine.recognize(Recognition(frame, stamp, stamp, .4 if moving else 0, 0,
                                             float(first or second), 0), stamp)
                if receiver.delivery.task is not None:
                    await receiver.delivery.task
            if scenario == 'normal_move':
                assert posts and set(kinds(posts)) == {'move'}
            elif scenario == 'dwell_drag':
                assert kinds(posts)[0] == 'down' and posts[0]['count'] == 1
                assert set(kinds(posts)[1:-1]) == {'drag'}
                assert kinds(posts)[-1] == 'up'
            else:
                assert kinds(posts) == ['down', 'up', 'down', 'up']
                assert [e['count'] for e in posts] == [1, 1, 2, 2]
            assert not worker.drag.held and mouse._drag_up is None
        finally:
            mouse.disable()
    asyncio.run(run())
