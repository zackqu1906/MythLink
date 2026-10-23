"""Per-click evidence through the real output/IPC dispatcher; no OS input."""
import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from proximic_ring.touchpad import click_diagnostics as diagnostics
from proximic_ring.touchpad.touchpad_mouse import TouchpadMouseOutput
from proximic_ring.touchpad.touchpad_strokes import TouchpadStrokeOutput
from ring_python_sdk.touchpad import TouchpadClick, TouchpadMove, TouchpadStats


@pytest.fixture
def records(caplog):
    caplog.set_level(logging.INFO, logger=diagnostics.__name__)
    def read():
        assert diagnostics.flush_click_diagnostics()
        return [json.loads(r.getMessage().split('[TOUCHPAD_CLICK] ', 1)[1])
                for r in caplog.records if '[TOUCHPAD_CLICK] ' in r.getMessage()]
    yield read
    diagnostics.flush_click_diagnostics()


def until(predicate):
    deadline = time.monotonic() + 2
    while not predicate() and time.monotonic() < deadline:
        time.sleep(.005)
    assert predicate()


class Mouse:
    def enable(self): pass
    def disable(self): pass
    def apply(self, events):
        clicks = sum(e['kind'] == 'click' for e in events)
        return dict(clicks_posted=clicks, down_posts=clicks, up_posts=clicks,
                    reason='posted' if clicks else 'no_click_posted')
    def double_click_target(self):
        return dict(bundle='test.editor', pid=123)


def output(mouse=None, **options):
    ready, ended = threading.Event(), threading.Event()
    settings = dict(connection=threading.Event(), gain=1, clicks=True, invert_y=False,
                    on_ready=ready.set, on_end=lambda *_: ended.set(),
                    on_double_click=lambda _: None, mouse_factory=lambda: mouse or Mouse())
    settings.update(options)
    return TouchpadMouseOutput(**settings), ready, ended


def finish(worker, ended):
    worker.stop()
    assert ended.wait(2)


def results(records):
    return [r for r in records() if r['stage'] == 'result']


@pytest.mark.parametrize('state,reason', [
    ('inactive', 'output_inactive'), ('stopped', 'output_stopped'),
    ('disconnected', 'disconnected'), ('paused', 'output_paused'),
])
def test_click_rejected_before_enqueue_has_a_terminal_reason(records, state, reason):
    worker, _, _ = output()
    if state != 'inactive': worker.activate()
    if state == 'stopped': worker.stop()
    if state == 'disconnected': worker.connection.set()
    if state == 'paused': worker.paused = True
    worker.submit(TouchpadClick(1, time.monotonic()))
    [row] = results(records)
    assert row['reason'] == reason and row['clicks_posted'] == 0
    assert row['queue_ms'] == 0 and 'ipc_age_ms' not in row


@pytest.mark.parametrize('scenario,reason', [
    ('expired', 'mouse_queue_expired'), ('overflow', 'queue_overflow'),
    ('context', 'click_context_changed'), ('loading', 'selector_loading'),
    ('disabled', 'clicks_disabled'), ('capture', 'capture_callback_requested'),
])
def test_discard_and_capture_reasons_are_distinct(records, scenario, reason):
    context = [(0, 'capture' if scenario == 'capture' else
                   'loading' if scenario == 'loading' else 'closed')]
    worker, ready, ended = output(click_context=lambda: context[0],
                                  clicks=scenario != 'disabled', on_capture_click=lambda _: None)
    worker.activate()
    worker.submit(TouchpadClick(1, time.monotonic() - (.2 if scenario == 'expired' else 0)))
    if scenario == 'overflow':
        for step in range(32):
            worker.submit(TouchpadMove(0, 0, 0, step, time.monotonic()))
    if scenario == 'context': context[0] = (1, 'closed')
    worker.start()
    try:
        assert ready.wait(1)
        until(lambda: len(results(records)) == 1)
        [row] = results(records)
        assert row['reason'] == reason and row['clicks_posted'] == 0
        assert row['queue_ms'] >= 0 and row['total_ms'] >= row['queue_ms']
        assert row['outcome'] == ('routed' if scenario == 'capture' else 'discarded')
    finally:
        finish(worker, ended)
    assert len(results(records)) == 1


def test_stop_clears_queued_click_with_a_receipt(records):
    entered, release = threading.Event(), threading.Event()
    class Starting(Mouse):
        def enable(self):
            entered.set()
            assert release.wait(2)
    worker, _, ended = output(Starting())
    worker.activate()
    worker.submit(TouchpadClick(1, time.monotonic()))
    worker.start()
    assert entered.wait(1)
    worker.stop()
    release.set()
    assert ended.wait(2)
    [row] = results(records)
    assert row['reason'] == 'output_stopped' and row['clicks_posted'] == 0


def test_duplicate_and_double_click_each_have_one_result(records):
    worker, ready, ended = output()
    worker.activate()
    worker.start()
    try:
        assert ready.wait(1)
        first = TouchpadClick(1, time.monotonic())
        worker.submit(first)
        until(lambda: len(results(records)) == 1)
        worker.submit(first)
        until(lambda: len(results(records)) == 2)
        worker.submit(TouchpadClick(2, time.monotonic()))
        until(lambda: len(results(records)) == 3)
        rows = results(records)
        assert [r['reason'] for r in rows] == [
            'posted', 'click_duplicate_or_out_of_order', 'double_click_stroke_requested']
        assert [r['clicks_posted'] for r in rows] == [1, 0, 0]
        assert len({r['click_id'] for r in rows}) == 3
    finally:
        finish(worker, ended)


def test_context_change_after_hint_cancels_with_explicit_reason(records):
    context = [(0, 'closed')]
    def hint(_):
        context[0] = (1, 'closed')
    worker, ready, ended = output(on_gesture=hint, click_context=lambda: context[0])
    worker.activate()
    worker.start()
    try:
        assert ready.wait(1)
        worker.submit(TouchpadClick(1, time.monotonic()))
        until(lambda: len(results(records)) == 1)
        [row] = results(records)
        assert row['reason'] == 'click_context_changed' and row['decision'] == 'single'
        assert not any(r['stage'] == 'dispatch' for r in records())
    finally:
        finish(worker, ended)


def test_lost_ipc_reply_is_unknown_not_zero_and_does_not_retry(records):
    class Broken(Mouse):
        last_diagnostic = dict(reason='channel_timeout', delivery='unknown')
        calls = 0
        def apply_click(self, trace):
            self.calls += 1
            raise TimeoutError('reply lost')
    mouse = Broken()
    worker, ready, ended = output(mouse)
    worker.activate()
    worker.start()
    assert ready.wait(1)
    worker.submit(TouchpadClick(1, time.monotonic()))
    assert ended.wait(2)
    [row] = results(records)
    assert row['reason'] == 'channel_timeout' and row['outcome'] == 'unknown'
    assert row['clicks_posted'] is row['down_posts'] is row['up_posts'] is None
    assert mouse.calls == 1


@pytest.fixture
def native(monkeypatch):
    import proximic_ring.platforms.macos.native_access_worker as module
    from proximic_ring.platforms.macos.native_touchpad import WorkerTouchpadMouse
    read_fd, write_fd = os.pipe()
    posted, native_records = [], []
    q = SimpleNamespace(CGPreflightPostEventAccess=lambda: True, kCGHIDEventTap=0,
        kCGEventLeftMouseDown=1, kCGEventLeftMouseUp=2, kCGEventMouseMoved=3,
        kCGMouseEventClickState=1, CGEventSetIntegerValueField=lambda *args: None,
        CGEventPost=lambda _, event: posted.append(event))
    mouse = WorkerTouchpadMouse(read_fd, quartz=q,
        accessibility=SimpleNamespace(AXIsProcessTrusted=lambda: True))
    mouse._position = lambda: (20, 30)
    mouse._clamp = lambda x, y: (x, y)
    mouse._event = lambda kind, point: kind
    mouse.enable()
    dispatcher = module.Dispatcher()
    dispatcher.touchpad_mouse = mouse
    monkeypatch.setattr(module, 'click_record', lambda record, **_: native_records.append(record))
    yield module, dispatcher, mouse, posted, native_records
    mouse.disable()
    os.close(read_fd)
    os.close(write_fd)


@pytest.mark.parametrize('age,reason,posts', [
    (0., 'posted', 1), (.100, 'posted', 1), (.249, 'posted', 1), (.250, 'posted', 1),
    (.251, 'ipc_expired', 0), (-.001, 'ipc_future_timestamp', 0),
])
def test_native_request_250ms_boundary_and_actual_posts(native, monkeypatch, age, reason, posts):
    module, dispatcher, _, posted, rows = native
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: 1000.))
    result = dispatcher.handle(dict(operation='touchpad_apply', created=1000.-age,
        events=[dict(kind='click')], click_trace=dict(click_id='boundary')))['result']
    assert result['reason'] == reason
    assert result['ipc_age_ms'] == pytest.approx(age*1000)
    assert result['ipc_limit_ms'] == 250
    assert result['clicks_posted'] == result['down_posts'] == result['up_posts'] == posts
    assert len(posted) == 2*posts
    assert [r['stage'] for r in rows] == ['native_received', 'native_result']
    assert all(r['click_id'] == 'boundary' for r in rows)


@pytest.mark.parametrize('gate,reason', [
    ('disabled', 'native_disabled'), ('clicks', 'clicks_disabled'),
    ('permission', 'permission_denied'),
])
def test_native_gate_and_permission_have_zero_post_receipts(native, gate, reason):
    _, dispatcher, mouse, posted, rows = native
    if gate == 'disabled': mouse.disable()
    if gate == 'clicks': mouse.clicks_enabled = False
    if gate == 'permission':
        mouse._permissions.error = PermissionError('revoked')
    message = dict(operation='touchpad_apply', created=time.monotonic(),
                   events=[dict(kind='click')], click_trace=dict(click_id='blocked'))
    if gate == 'permission':
        with pytest.raises(PermissionError): dispatcher._touchpad(message)
    else:
        dispatcher.handle(message)
    assert rows[-1]['reason'] == reason and rows[-1]['clicks_posted'] == 0
    assert not posted


def test_partial_native_post_is_preserved_on_error(native):
    _, dispatcher, mouse, posted, rows = native
    def post(_, event):
        if event == 2: raise RuntimeError('up failed')
        posted.append(event)
    mouse.q.CGEventPost = post
    with pytest.raises(RuntimeError, match='up failed'):
        dispatcher.handle(dict(operation='touchpad_apply', created=time.monotonic(),
            events=[dict(kind='click')], click_trace=dict(click_id='partial')))
    row = rows[-1]
    assert row['reason'] == 'native_error'
    assert row['clicks_posted'] == 0 and row['down_posts'] == 1 and row['up_posts'] == 0
    assert dispatcher.last_touchpad_receipt['down_posts'] == 1


@pytest.mark.parametrize('age', [float('nan'), float('inf')])
def test_invalid_request_time_is_rejected_with_json_safe_diagnostics(native, age):
    _, dispatcher, _, posted, rows = native
    result = dispatcher.handle(dict(operation='touchpad_apply', created=age,
        events=[dict(kind='click')], click_trace=dict(click_id='invalid')))['result']
    assert result['reason'] == 'ipc_invalid_timestamp' and not posted
    assert result['ipc_age_ms'] is None
    json.dumps(rows, allow_nan=False)


@pytest.mark.parametrize('age,reason', [(.25, 'double_target_ready'), (.251, 'ipc_expired')])
def test_double_target_uses_the_same_250ms_request_limit(native, monkeypatch, age, reason):
    module, dispatcher, mouse, posted, _ = native
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: 1000.))
    monkeypatch.setattr(mouse, 'double_click_target', lambda: dict(bundle='test.editor', pid=123))
    result = dispatcher.handle(dict(operation='touchpad_double_click_target', created=1000.-age,
        click_trace=dict(click_id='double')))['result']
    assert result['reason'] == reason
    assert bool(result['target']) == (age == .25)
    assert result['clicks_posted'] == 0 and not posted


def test_same_id_links_output_to_native_receipt(native, records):
    from proximic_ring.platforms.macos.native_touchpad import NativeTouchpadMouse
    _, dispatcher, _, _, native_rows = native
    class Channel:
        last_diagnostic = {}
        def call(self, operation, **params):
            if operation == 'touchpad_enable': return {}
            return dispatcher.handle(dict(operation=operation, **params))['result']
        def close(self): pass
    mouse = NativeTouchpadMouse(channel_factory=lambda **_: Channel())
    worker, ready, ended = output(mouse)
    worker.activate()
    worker.start()
    try:
        assert ready.wait(1)
        worker.submit(TouchpadClick(1, time.monotonic()))
        until(lambda: len(results(records)) == 1)
        [row] = results(records)
        assert row['outcome'] == 'posted' and row['clicks_posted'] == 1
        assert row['down_posts'] == row['up_posts'] == 1
        assert row['queue_ms'] >= 0 and row['ipc_roundtrip_ms'] >= row['ipc_age_ms'] >= 0
        assert row['click_id'] == native_rows[-1]['click_id']
    finally:
        finish(worker, ended)


@pytest.mark.skipif(sys.platform != 'darwin', reason='production worker imports objc')
@pytest.mark.parametrize('fail_up', [False, True])
def test_real_private_pipe_preserves_native_click_receipts(tmp_path, monkeypatch, fail_up):
    from proximic_ring.platforms.macos.native_access import NativeAccessChannel
    from proximic_ring.platforms.macos.native_touchpad import NativeTouchpadMouse
    from proximic_ring.diagnostics.scene_diagnostics import SceneActionError
    script = tmp_path / 'native_click_worker.py'
    script.write_text('''
import sys
from types import SimpleNamespace
from proximic_ring.platforms.macos import native_access_worker, native_touchpad, mac_app_activation
mac_app_activation.configure_app_activation = lambda **kwargs: None
original = native_touchpad.WorkerTouchpadMouse
def mouse(fd):
    def post(_, event):
        if event == 2 and sys.argv[1] == 'fail': raise RuntimeError('simulated up failure')
    q = SimpleNamespace(CGPreflightPostEventAccess=lambda: True, kCGHIDEventTap=0,
        kCGEventLeftMouseDown=1, kCGEventLeftMouseUp=2, kCGMouseEventClickState=1,
        CGEventSetIntegerValueField=lambda *args: None, CGEventPost=post)
    m = original(fd, quartz=q, accessibility=SimpleNamespace(AXIsProcessTrusted=lambda: True))
    m._position = lambda: (0, 0)
    m._event = lambda kind, point: kind
    return m
native_touchpad.WorkerTouchpadMouse = mouse
native_access_worker.main()
''')
    def factory(**options):
        channel = NativeAccessChannel(**options)
        def start():
            if channel._process is None:
                channel._process = subprocess.Popen(
                    [sys.executable, str(script), 'fail' if fail_up else 'ok'],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    pass_fds=channel._pass_fds, bufsize=0)
        monkeypatch.setattr(channel, '_start', start)
        return channel
    mouse = NativeTouchpadMouse(channel_factory=factory)
    try:
        mouse.enable()
        if fail_up:
            with pytest.raises(SceneActionError):
                mouse.apply_click(dict(click_id='real-pipe'))
            result = mouse.last_diagnostic['click_receipt']
            assert result['reason'] == 'native_error'
        else:
            result = mouse.apply_click(dict(click_id='real-pipe'))
            assert result['reason'] == 'posted' and result['ipc_roundtrip_ms'] >= 0
        assert result['down_posts'] == 1
        assert result['clicks_posted'] == result['up_posts'] == (0 if fail_up else 1)
        assert result['ipc_limit_ms'] == 250 and result['ipc_age_ms'] >= 0
    finally:
        mouse.close()


def test_stroke_click_is_logged_as_routed_not_posted(records):
    ready, ended = threading.Event(), threading.Event()
    worker = TouchpadStrokeOutput(connection=threading.Event(), on_ready=ready.set,
        on_end=lambda *_: ended.set(), on_stroke=lambda _: None, on_tap=lambda: None,
        on_double_click=lambda: None)
    worker.activate()
    worker.start()
    try:
        assert ready.wait(1)
        worker.submit(TouchpadClick(1, time.monotonic()))
        until(lambda: len(results(records)) == 1)
        [row] = results(records)
        assert row['reason'] == 'stroke_mode_no_mouse_click'
        assert row['mode'] == 'stroke' and row['clicks_posted'] == 0
    finally:
        finish(worker, ended)


def test_sdk_reports_click_expired_before_application_callback(monkeypatch):
    from ring_python_sdk.touchpad import stream
    observed, discarded = [], []
    class Processor:
        def __init__(self, _):
            self.stats, self.last_valid = TouchpadStats(), time.monotonic()
        def poll(self):
            return [TouchpadClick(1, time.monotonic()-.2)]
    monkeypatch.setattr(stream, 'TouchpadProcessor', Processor)
    async def run():
        pipeline = stream.TouchpadStream(observed.append, duration_s=None,
                                        on_discarded_event=lambda e, why: discarded.append((e, why)))
        await pipeline.prepare()
        pipeline.start()
        try:
            for _ in range(100):
                if discarded: break
                await asyncio.sleep(.005)
            assert discarded and discarded[0][1] == 'sdk_callback_expired'
            assert not observed
        finally:
            await pipeline.close()
    asyncio.run(run())


def test_slow_log_sink_does_not_delay_click_delivery(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    logger = logging.getLogger(diagnostics.__name__)
    def slow_log(*_):
        entered.set()
        assert release.wait(2)
    monkeypatch.setattr(logger, 'info', slow_log)
    worker, ready, ended = output()
    worker.activate()
    worker.start()
    try:
        assert ready.wait(1)
        worker.submit(TouchpadClick(1, time.monotonic()))
        assert entered.wait(1)
        until(lambda: not worker._pending_clicks)
        assert not release.is_set()
    finally:
        release.set()
        finish(worker, ended)
        assert diagnostics.flush_click_diagnostics()
