"""Live controls share BLE and keep slow pointer output off the notification loop."""
import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from proximic_ring.audio.ring import RingAudioSource
from proximic_ring.touchpad_mouse import TouchpadMouseOutput
from ring_python_sdk.touchpad import TouchpadMove, TouchpadClick


def test_dynamic_controls_use_existing_loop_without_stopping_audio():
    async def run():
        source = RingAudioSource()
        calls = []
        class Session:
            touchpad_active = False
            mic_active = True
            async def touchpad_on(self, **options):
                assert asyncio.get_running_loop() is loop
                calls.append(options)
                self.callback = options['on_stopped']
                self.touchpad_active = True
            async def touchpad_off(self):
                self.touchpad_active = False
                self.callback(None)
        session = Session()
        loop = asyncio.get_running_loop()
        source._touchpad_endpoint = (loop, session)
        events = []
        future = await asyncio.to_thread(source.start_touchpad, on_event=events.append, duration_s=None)
        await asyncio.wrap_future(future)
        assert source.touchpad_active and calls[0]['duration_s'] is None
        await asyncio.wrap_future(source.stop_touchpad())
        assert not source.touchpad_active and session.mic_active and not source._stop.is_set()
        assert not source._touchpad_requests
        source._touchpad_endpoint = None
        with pytest.raises(ConnectionError):
            await asyncio.wrap_future(source.start_touchpad(on_event=events.append))
    asyncio.run(run())


def test_touchpad_failure_does_not_poison_audio_source():
    async def run():
        source = RingAudioSource()
        class Session:
            async def touchpad_on(self, **options): raise RuntimeError('model unavailable')
        source._touchpad_endpoint = (asyncio.get_running_loop(), Session())
        with pytest.raises(RuntimeError, match='model unavailable'):
            await asyncio.wrap_future(source.start_touchpad(on_event=lambda e: None))
        assert source.error is None and not source._stop.is_set()
    asyncio.run(run())


class Mouse:
    def __init__(self): self.calls = []; self.esc = False; self.enabled = False
    def enable(self): self.enabled = True
    def disable(self): self.enabled = False
    def escape_pressed(self): return self.esc
    def apply(self, batch): self.calls.extend(batch)


def test_output_coalesces_moves_preserves_click_and_drops_old_events():
    ready, done = threading.Event(), threading.Event()
    mouse = Mouse()
    output = TouchpadMouseOutput(connection=threading.Event(), gain=1, clicks=True, invert_y=False,
        on_ready=ready.set, on_end=lambda *args: done.set(), mouse_factory=lambda: mouse)
    # Fill before start to deterministically verify ordering/coalescing.
    output.activate()
    t = time.monotonic()
    output.submit(TouchpadMove(99, 99, 1, 0, t-1))
    output.submit(TouchpadMove(1, 2, 1, 1, t))
    output.submit(TouchpadMove(3, 4, 1, 2, t))
    output.submit(TouchpadClick(3, t))
    output.submit(TouchpadMove(5, 6, 1, 4, t))
    output.start()
    assert ready.wait(1)
    for _ in range(100):
        if mouse.calls: break
        time.sleep(.005)
    output.stop()
    assert done.wait(1)
    assert mouse.calls == [dict(kind='move', dx=4, dy=6), dict(kind='click'), dict(kind='move', dx=5, dy=6)]


@pytest.mark.parametrize('reason', ['manual', 'disconnect'])
def test_output_stops_even_without_token_traffic(reason):
    ready, done, connection = threading.Event(), threading.Event(), threading.Event()
    mouse = Mouse()
    output = TouchpadMouseOutput(connection=connection, gain=1, clicks=True, invert_y=False,
        on_ready=ready.set, on_end=lambda *args: done.set(), mouse_factory=lambda: mouse)
    output.start()
    assert ready.wait(1)
    if reason == 'manual': output.stop()
    else: connection.set()
    assert done.wait(.3)
    assert not mouse.enabled


def test_output_keeps_running_past_old_limits_until_manual_stop(monkeypatch):
    import proximic_ring.touchpad_mouse as module
    clock = [time.monotonic()]
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    ready, done = threading.Event(), threading.Event()
    mouse = Mouse()
    output = TouchpadMouseOutput(connection=threading.Event(), gain=1, clicks=True, invert_y=False,
        on_ready=ready.set, on_end=lambda *args: done.set(), mouse_factory=lambda: mouse)
    output.activate(); output.start()
    try:
        assert ready.wait(1)
        clock[0] += 3600  # Beyond every former 30–600 second option.
        mouse.esc = True
        output.submit(TouchpadMove(2, 3, 1, 1, clock[0]))
        for _ in range(100):
            if mouse.calls: break
            time.sleep(.005)
        assert mouse.calls == [dict(kind='move', dx=2, dy=3)]
        assert output.active and mouse.enabled and not done.is_set()
        assert mouse.stop_on_escape is False
    finally:
        output.stop()
    assert done.wait(1) and not mouse.enabled


def test_mouse_adapter_allows_the_app_to_keep_escape_for_other_apps():
    from ring_python_sdk.touchpad.macos import MacSystemMouse
    posted = []
    quartz = SimpleNamespace(CGPreflightPostEventAccess=lambda: True, kCGHIDEventTap=0,
        kCGEventMouseMoved=1, CGEventPost=lambda tap, event: posted.append(event))
    mouse = MacSystemMouse(quartz=quartz, accessibility=SimpleNamespace(AXIsProcessTrusted=lambda: True))
    mouse.escape_pressed = lambda: True
    mouse._position = lambda: (0, 0)
    mouse._clamp = lambda x, y: (x, y)
    mouse._event = lambda kind, point: (kind, point)
    mouse.enable()
    with pytest.raises(InterruptedError): mouse.apply([dict(kind='move', dx=2, dy=3)])
    assert not posted
    mouse.stop_on_escape = False
    mouse.enable()
    mouse.apply([dict(kind='move', dx=2, dy=3)])
    assert posted == [(1, (2, 3))] and mouse.enabled
    mouse.disable()


def test_slow_mouse_output_never_blocks_ble_and_queue_is_bounded():
    entered, release, done = threading.Event(), threading.Event(), threading.Event()
    class Slow(Mouse):
        def apply(self, batch):
            entered.set()
            release.wait(2)
    output = TouchpadMouseOutput(connection=threading.Event(), gain=1, clicks=True, invert_y=False,
        on_ready=lambda: None, on_end=lambda *args: done.set(), mouse_factory=Slow)
    output.activate(); output.start()
    event = TouchpadMove(1, 1, 1, 1, time.monotonic())
    output.submit(event)
    assert entered.wait(1)
    try:
        start = time.monotonic()
        for _ in range(10000): output.submit(event)
        assert time.monotonic() - start < .2
        assert len(output.events) <= 32
        output.stop()
        assert output.stopped.is_set() and not output.mouse.enabled
    finally:
        release.set()
    assert done.wait(1)
