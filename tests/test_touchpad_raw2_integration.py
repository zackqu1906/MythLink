"""Raw2 -> shared BLE -> product output -> native events, without real input."""
import asyncio
from dataclasses import replace
from dataclasses import replace
import struct
import threading
import time
from types import SimpleNamespace

import pytest

from ring_python_sdk import RingSession
from ring_python_sdk.audio.processor import AudioProcessor
from ring_python_sdk.touchpad import TouchpadClick, TouchpadContact, TouchpadMove
from ring_python_sdk.touchpad.events import Raw2PointerClick, Raw2PointerMove, Raw2Reset
from proximic_ring.touchpad.touchpad_mouse import TouchpadMouseOutput
from test_ring_mac_sdk_delivery import FakeClient, FakeModel, status, token
from ring_mac_sdk import protocol as p


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(.001)


async def shared_session(monkeypatch, tmp_path):
    from ring_python_sdk.touchpad import raw2
    factory = raw2.Raw2Stream
    monkeypatch.setattr(raw2, 'Raw2Stream', lambda *a, **kw: factory(*a, **kw, model_factory=FakeModel))
    session = RingSession('offline', 1, data_root=tmp_path)
    client = FakeClient(disconnected_callback=lambda _: None)
    from ring_python_sdk.session.sdk_transport import connect_session
    session.session_dir = tmp_path
    await connect_session(session, 'offline', client_factory=lambda *a, **kw: client)
    client.commands.clear()  # INFO handshake is checked separately in connection tests.
    return session, client


def enable_dev9_gestures(session, tmp_path, on_trigger=None):
    from ring_python_sdk.swipe.processor import SwipeProcessor
    session._sdk_transport.device_info = replace(
        session._sdk_transport.device_info, firmware_version='2.1.6-dev.touchpad.9.debug')
    session.swipe = SwipeProcessor(tmp_path/'gestures.csv', on_trigger=on_trigger)
    session.swipe_active = True
    return session.swipe


def test_dev9_starts_raw2_before_resuming_existing_gestures_and_keeps_mic(monkeypatch, tmp_path):
    from test_public_sdk_integration import audio_packet, compact

    async def run():
        session, client = await shared_session(monkeypatch, tmp_path)
        gestures, pcm, frames = [], [], []
        processor = enable_dev9_gestures(session, tmp_path, gestures.append)
        session.mic = AudioProcessor(tmp_path/'audio.wav', on_pcm=lambda *args: pcm.append(args))
        session.mic_active = True
        original = client.write_gatt_char
        swipe_running = True

        async def write(uuid, command, **kw):
            nonlocal swipe_running
            if command == b'\x26\x01':
                swipe_running = False
                client.callback(None, audio_packet())
            elif command == b'\x26\x00':
                # This firmware can coexist only after Raw2's first token.
                assert session.touchpad.receiver.ordinal > 0
                swipe_running = True
                client.callback(None, compact())
            if command == p.START and swipe_running:
                client.commands.append(command)
                client.callback(None, status())  # Real dev9 failure: ACK, no data.
                return
            await original(uuid, command, **kw)
        client.write_gatt_char = write
        try:
            await session.touchpad_on(protocol='raw2', on_event=frames.append, duration_s=None)
            assert client.commands == [b'\x26\x01', p.STOP, p.START, b'\x26\x00']
            assert session.touchpad.receiver.status.stage == 'RUNNING'
            assert gestures and pcm and frames and swipe_running
            assert session.swipe is processor and session.swipe_active and session.mic_active
            assert session.touchpad.paused_swipe is None
            client.callback(None, token(1, reset=False))
            await until(lambda: session.touchpad.receiver.ordinal == 2)
            await session.touchpad_off()
            assert not any(command[:1] == b'\x20' for command in client.commands)
            assert session.swipe is processor and session.swipe_active and session.mic_active
        finally:
            processor.close()
            session.mic.close()
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['model', 'cancel', 'user_stop'])
def test_dev9_restores_paused_gestures_if_start_ends_before_raw2_command(monkeypatch, tmp_path, failure):
    from ring_python_sdk.touchpad import raw2
    stream_class = raw2.Raw2Stream

    async def run():
        session, client = await shared_session(monkeypatch, tmp_path)
        processor = enable_dev9_gestures(session, tmp_path)
        session.mic_active = True
        if failure == 'model':
            def unavailable():
                raise RuntimeError('model unavailable')
            monkeypatch.setattr(raw2, 'Raw2Stream', lambda *a, **kw: stream_class(*a, **kw, model_factory=unavailable))
        start = asyncio.create_task(session.touchpad_on(protocol='raw2', on_event=lambda _: None))
        try:
            await until(lambda: b'\x26\x01' in client.commands)
            if failure == 'cancel':
                start.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await start
            elif failure == 'user_stop':
                await session.touchpad_off()
                with pytest.raises(RuntimeError, match='cancelled'):
                    await start
            else:
                with pytest.raises(Exception, match='model unavailable'):
                    await start
            assert p.START not in client.commands and p.STOP not in client.commands
            assert client.commands[-1] == b'\x26\x00'
            assert session.swipe is processor and session.swipe_active and session.mic_active
            assert session.touchpad is None and not session.touchpad_active
        finally:
            processor.close()
    asyncio.run(run())


def test_shared_connection_preserves_audio_and_resets_both_output_lanes(monkeypatch, tmp_path):
    async def run():
        session, client = await shared_session(monkeypatch, tmp_path)
        frames, pointer, pcm, stopped = [], [], [], []
        session.mic = AudioProcessor(tmp_path/'audio.wav', on_pcm=lambda *args: pcm.append(args))
        session.mic_active = True
        try:
            await session.touchpad_on(protocol='raw2', on_event=frames.append,
                                      on_pointer_move=pointer.append, on_stopped=stopped.append, duration_s=None)
            assert session.touchpad_active
            assert client.commands == [p.STOP, p.START]
            assert any(isinstance(e, TouchpadMove) for e in frames)
            assert pointer and pointer[0].session_id
            old_key = (pointer[0].session_id, pointer[0].generation)
            client.callback(None, token(1, epoch=5))
            await until(lambda: any(getattr(e, 'generation', 0) == 1 for e in pointer))
            resets = [e for e in frames if e.kind == 'contact' and e.state == 'reset']
            assert [(e.session_id, e.generation) for e in resets] == [old_key, (old_key[0], 1)]
            from test_public_sdk_integration import audio_packet
            client.callback(None, audio_packet())
            assert len(pcm) == 1 and session.mic_active
            await session.touchpad_off()
            before = len(pointer)
            client.callback(None, token(2, epoch=5, reset=False))
            assert len(pointer) == before
            assert session.touchpad is None and not session.touchpad_active
            assert client.commands == [p.STOP, p.START, p.STOP]
            assert stopped == [None] and client.is_connected and session.mic_active
        finally:
            session.mic.close()
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['gap', 'idle', 'overflow'])
def test_shared_stream_failure_cleans_up_without_disconnecting_mic(monkeypatch, tmp_path, failure):
    async def run():
        session, client = await shared_session(monkeypatch, tmp_path)
        stopped = []
        session.mic_active = True
        await session.touchpad_on(protocol='raw2', on_event=lambda _: None,
                                  on_stopped=stopped.append, duration_s=None)
        if failure == 'gap':
            client.callback(None, token(3, reset=False))
        elif failure == 'overflow':
            for i in range(1, 67):
                client.callback(None, token(i, reset=False))
        await until(lambda: bool(stopped))
        await session._wait_touchpad_cleanup()
        assert stopped[0] is not None
        assert session.touchpad is None and not session.touchpad_active
        assert client.commands[-1] == p.STOP
        assert client.is_connected and session.mic_active
    asyncio.run(run())


def test_stop_during_raw2_start_waits_for_stop_ack_on_shared_demux(monkeypatch, tmp_path):
    async def run():
        session, client = await shared_session(monkeypatch, tmp_path)
        original = client.write_gatt_char
        started = asyncio.Event()
        async def write(uuid, command, **kw):
            if command == p.START:
                client.commands.append(command)
                client.callback(None, status())
                started.set()  # No first token; start is still pending.
            else:
                await original(uuid, command, **kw)
        client.write_gatt_char = write
        task = asyncio.create_task(session.touchpad_on(protocol='raw2', on_event=lambda _: None))
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.wait_for(session.touchpad_off(), 1)
        with pytest.raises(Exception):
            await task
        assert client.commands.count(p.STOP) == 2
        assert not session.touchpad_active and client.is_connected
    asyncio.run(run())


@pytest.mark.parametrize('failing', [False, True])
def test_repeated_stop_does_not_cancel_delayed_cleanup_ack(monkeypatch, tmp_path, failing):
    async def run():
        session, client = await shared_session(monkeypatch, tmp_path)
        await session.touchpad_on(protocol='raw2', on_event=lambda _: None, duration_s=None)
        stream = session.touchpad
        original = client.write_gatt_char
        stopping, release = asyncio.Event(), asyncio.Event()
        async def write(uuid, command, **kw):
            if command == p.STOP:
                stopping.set()
                await release.wait()
            await original(uuid, command, **kw)
        client.write_gatt_char = write
        if failing:
            client.callback(None, token(3, reset=False))
        else:
            stream.stop_now()
        await asyncio.wait_for(stopping.wait(), 1)
        # User off, session cleanup and close all gate the same stream.
        off = asyncio.create_task(session.touchpad_off())
        stream.stop_now()
        await asyncio.sleep(.02)
        assert not off.done() and stream.transport.ready
        release.set()
        await asyncio.wait_for(off, 1)
        await session._wait_touchpad_cleanup()
        assert stream.transport.stop_confirmed
        assert client.commands.count(p.STOP) == 2
        assert client.is_connected and session.touchpad is None
    asyncio.run(run())


@pytest.mark.parametrize('stop_confirmed', [False, True])
def test_missing_first_token_reports_input_timeout_and_recovers_existing_gestures(monkeypatch, tmp_path, stop_confirmed, caplog):
    from ring_python_sdk.swipe.processor import SwipeProcessor
    from proximic_ring.audio.ring import RingAudioSource
    from proximic_ring.voice.app_runtime import _gesture_health_status
    from test_public_sdk_integration import compact
    from ring_mac_sdk.errors import RingError

    async def run():
        session, client = await shared_session(monkeypatch, tmp_path)
        observed, stopped = [], []
        session.swipe = SwipeProcessor(tmp_path/'gestures.csv', on_trigger=observed.append)
        processor = session.swipe
        session.swipe_active = session.mic_active = True
        client.callback(None, compact())
        source = RingAudioSource(selector='offline')
        source._touchpad_endpoint = (asyncio.get_running_loop(), session)
        source.gestures_active = True
        original = client.write_gatt_char
        async def write(uuid, command, **kw):
            if command == p.START:
                client.commands.append(command)
                client.callback(None, status())  # ACK succeeds, but no token follows.
            elif command[:1] == b'\x26':
                client.commands.append(command)
            elif command == p.STOP and p.START in client.commands and not stop_confirmed:
                client.commands.append(command)
                bad = bytearray(status(0))
                struct.pack_into('<i', bad, 4, -16)
                client.callback(None, bad)
            else:
                await original(uuid, command, **kw)
        client.write_gatt_char = write
        try:
            with pytest.raises(RingError, match='3 秒内未收到首个 Raw2 数据包') as error:
                await session.touchpad_on(protocol='raw2', on_event=lambda _: None,
                                          on_stopped=stopped.append, duration_s=None)
            assert error.value.reason == 'input_timeout'
            assert stopped == [error.value]
            assert not session.touchpad_active and session.touchpad is None
            assert session.swipe is processor and session.swipe_active and session.mic_active
            assert client.is_connected
            diagnostics = source.gesture_diagnostics()
            if stop_confirmed:
                assert client.commands == [p.STOP, p.START, p.STOP, b'\x26\x01', b'\x26\x00']
                assert diagnostics['recovery_state'] == 'awaiting_packets'
                assert _gesture_health_status(dict(diagnostics, stream_ready=True)) == 'recovering'
                client.callback(None, compact(seq=2))
                assert len(observed) == 2
                assert source.gesture_diagnostics()['recovery_state'] == 'confirmed'
            else:
                assert client.commands == [p.STOP, p.START, p.STOP]
                assert diagnostics['recovery_state'] == 'failed'
                assert _gesture_health_status(dict(diagnostics, stream_ready=True)) == 'recovery_failed'
        finally:
            processor.close()
    with caplog.at_level('INFO'):
        asyncio.run(run())
    assert 'phase=first_token token_packets=0 inferred_tokens=0' in caplog.text
    assert '[RAW2_CONTROL] command=START stage=complete' in caplog.text


def test_actual_inference_timeout_is_distinct_from_missing_input(monkeypatch, tmp_path):
    from ring_python_sdk.touchpad import raw2
    stream_class = raw2.Raw2Stream
    class SlowModel(FakeModel):
        def step(self, features):
            time.sleep(.25)
            return super().step(features)
    async def run():
        session, client = await shared_session(monkeypatch, tmp_path)
        monkeypatch.setattr(raw2, 'Raw2Stream', lambda *a, **kw: stream_class(*a, **kw, model_factory=SlowModel))
        with pytest.raises(Exception, match='推理或调度超过 200 毫秒'):
            await session.touchpad_on(protocol='raw2', on_event=lambda _: None)
        assert session.touchpad_error.reason == 'inference_timeout'
        assert client.commands == [p.STOP, p.START, p.STOP]
    asyncio.run(run())


class Mouse:
    def __init__(self): self.calls = []; self.enabled = False
    def enable(self): self.enabled = True
    def disable(self): self.enabled = False
    def apply(self, values): self.calls.extend(values); return {'clicks_posted': 1, 'reason': 'posted'}


def output(**features):
    mouse = Mouse()
    done = threading.Event()
    worker = TouchpadMouseOutput(connection=threading.Event(), gain=1, clicks=features.pop('clicks', True), invert_y=False,
        on_ready=lambda: None, on_end=lambda *_: done.set(), mouse_factory=lambda: mouse,
        on_double_click=lambda *_: pytest.fail('Raw2 double click must not enter handwriting'), raw2=True, **features)
    worker.activate()
    return worker, mouse, done


def move(*, mode='SINGLE', generation=0, stamp=None):
    return Raw2PointerMove(3., 4., 1., 1, time.monotonic() if stamp is None else stamp,
                        mode=mode, session_id='test', generation=generation)


def click(identifier=1, *, count=1, button='left', generation=0):
    return Raw2PointerClick(1, time.monotonic(), count=count, button=button,
                         click_id=identifier, session_id='test', generation=generation)


def drain(worker, mouse, done, count):
    worker.start()
    try:
        deadline = time.monotonic()+1
        while len(mouse.calls) < count and time.monotonic() < deadline:
            time.sleep(.001)
    finally:
        worker.stop()
        assert done.wait(1)


@pytest.mark.parametrize('feedback_fails', [False, True])
def test_raw2_pointer_scroll_buttons_and_no_double_pairing(feedback_fails):
    hints = []
    def feedback(name):
        hints.append(name)
        if feedback_fails:
            raise RuntimeError('optional feedback unavailable')
    worker, mouse, done = output(on_gesture=feedback)
    for event in [move(), move(mode='TWO_FINGER'), click(), click(2, count=2), click(3, button='right'), click(3, button='right')]:
        worker.submit(event)
    drain(worker, mouse, done, 5)
    assert [e['kind'] for e in mouse.calls] == ['move', 'scroll', 'click', 'click', 'click']
    assert mouse.calls[1]['dy'] == -4 and 'dx' not in mouse.calls[1]
    assert [(e['button'], e['count']) for e in mouse.calls[2:]] == [('left', 1), ('left', 2), ('right', 1)]
    assert hints == ['two-finger-scroll', 'left-click', 'left-double-click', 'right-click']


def test_double_and_right_click_are_enabled_when_single_click_starts_disabled():
    worker, mouse, done = output(clicks=False)
    for event in [click(), click(2, count=2), click(3, button='right')]:
        worker.submit(event)
    drain(worker, mouse, done, 2)
    assert mouse.clicks_enabled
    assert [(e['button'], e['count']) for e in mouse.calls] == [('left', 2), ('right', 1)]


@pytest.mark.parametrize('disabled,index', [('clicks', 0), ('double_click', 1), ('right_click', 2), ('scroll', 3)])
def test_four_independent_switches_apply_live_before_dispatch(disabled, index):
    hints = []
    worker, mouse, done = output(on_gesture=hints.append)
    for e in [click(), click(2, count=2), click(3, button='right'), move(mode='TWO_FINGER')]:
        worker.submit(e)
    settings = dict(clicks=True, double_click=True, right_click=True, scroll=True, scroll_gain=2.)
    settings[disabled] = False
    worker.configure(**settings)
    drain(worker, mouse, done, 3)
    expected = [('left', 1), ('left', 2), ('right', 1), ('scroll', None)]
    expected.pop(index)
    assert [(e.get('button', e['kind']), e.get('count')) for e in mouse.calls] == expected
    expected_hints = ['left-click', 'left-double-click', 'right-click', 'two-finger-scroll']
    expected_hints.pop(index)
    assert hints == expected_hints


def test_reset_stop_expiry_and_reused_click_id_in_new_generation():
    hints = []
    worker, mouse, done = output(on_gesture=hints.append)
    worker.submit(click())
    worker.submit(Raw2Reset('reset', 0, time.monotonic(), session_id='test', generation=1))
    worker.submit(click(2))  # Old generation is already invalid.
    worker.submit(move(generation=1, stamp=time.monotonic()-.3))
    worker.submit(click(generation=1))
    drain(worker, mouse, done, 1)
    assert len(mouse.calls) == 1 and ':1:1' in mouse.calls[0]['click_id']
    worker.submit(click(3, generation=1))
    assert not worker.events
    assert hints == ['left-click']


def test_raw2_scroll_hints_are_rate_limited_without_losing_motion(monkeypatch):
    from dataclasses import replace
    from proximic_ring.touchpad import touchpad_mouse
    hints = []
    clock = [time.monotonic()]
    monkeypatch.setattr(touchpad_mouse, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    worker, mouse, done = output(on_gesture=hints.append)
    worker.start()
    submitted = 0
    def scroll(*, dy=4., generation=0):
        nonlocal submitted
        worker.submit(replace(move(mode='TWO_FINGER', generation=generation, stamp=clock[0]), dy=dy))
        submitted += 1
        deadline = time.monotonic() + 1
        while len(mouse.calls) < submitted and time.monotonic() < deadline:
            time.sleep(.001)
        assert len(mouse.calls) == submitted
    try:
        scroll(dy=0.)  # Horizontal-only movement must not announce vertical scrolling.
        assert not hints
        for _ in range(25):
            clock[0] += .02
            scroll()
        assert hints == ['two-finger-scroll']
        clock[0] += .6
        scroll()
        assert hints == ['two-finger-scroll'] * 2
        worker.submit(Raw2Reset('reset', 0, clock[0], session_id='test', generation=1))
        scroll(generation=1)
        assert hints == ['two-finger-scroll'] * 3
        assert sum(e['dy'] for e in mouse.calls) == -4 * 27
    finally:
        worker.stop()
        assert done.wait(1)


def test_native_quartz_click_counts_right_button_scroll_and_final_expiry():
    from ring_python_sdk.touchpad.macos import MacSystemMouse
    posts = []
    q = SimpleNamespace(kCGMouseButtonLeft=0, kCGMouseButtonRight=1,
        kCGEventLeftMouseDown=1, kCGEventLeftMouseUp=2, kCGEventRightMouseDown=3,
        kCGEventRightMouseUp=4, kCGMouseEventClickState=5, kCGHIDEventTap=6, kCGScrollEventUnitPixel=0,
        CGEventCreateMouseEvent=lambda _, kind, point, button: dict(kind=kind, button=button),
        CGEventSetIntegerValueField=lambda event, _, count: event.update(count=count),
        CGEventCreateScrollWheelEvent=lambda _, unit, axes, dy: dict(scroll=dy),
        CGEventPost=lambda _, event: posts.append(event))
    mouse = MacSystemMouse(q, SimpleNamespace())
    mouse.enabled = True
    mouse.stop_on_escape = False
    mouse._position = lambda: (0, 0)
    mouse.apply([dict(kind='click', count=2, button='left', click_id='a'),
                 dict(kind='click', count=2, button='left', click_id='a'),
                 dict(kind='click', button='right'), dict(kind='scroll', dy=-4.5),
                 dict(kind='scroll', dy=-.5), dict(kind='click', expires_at=time.monotonic()-1)])
    assert [(e['kind'], e['count']) for e in posts[:6]] == [(1, 1), (2, 1), (1, 2), (2, 2), (3, 1), (4, 1)]
    assert [e['button'] for e in posts[4:6]] == [1, 1]
    assert posts[6:] == [{'scroll': -4}, {'scroll': -1}]
