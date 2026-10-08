"""Live controls share BLE and keep slow pointer output off the notification loop."""
import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from proximic_ring.audio.ring import RingAudioSource
from proximic_ring.touchpad_mouse import TouchpadMouseOutput
from ring_python_sdk.touchpad import TouchpadMove, TouchpadClick


def test_dynamic_controls_suspend_competing_streams_and_restore_them():
    async def run():
        source = RingAudioSource()
        calls = []
        audio = []
        gesture_states = []
        source.pause_stream = lambda: audio.append("paused")
        source.begin_buffering = lambda: audio.append("resumed")
        source.gesture_observer = lambda _event: None
        source.gesture_state_observer = gesture_states.append
        class Session:
            touchpad_active = False
            mic_active = True
            swipe_active = True
            async def swipe_off(self):
                calls.append("swipe_off")
                self.swipe_active = False
            async def swipe_on(self, **options):
                calls.append("swipe_on")
                self.swipe_active = True
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
        stopped_states = []
        future = await asyncio.to_thread(
            source.start_touchpad, on_event=events.append, duration_s=90,
            on_stopped=lambda error: stopped_states.append((error, list(audio), session.swipe_active)))
        await asyncio.wrap_future(future)
        assert source.touchpad_active and calls[1]['duration_s'] == 90
        assert calls[0] == "swipe_off" and audio == ["paused"]
        assert not session.swipe_active and gesture_states == [False]
        await asyncio.wrap_future(source.stop_touchpad())
        assert not source.touchpad_active and session.mic_active and not source._stop.is_set()
        assert session.swipe_active and calls[-1] == "swipe_on"
        assert audio == ["paused", "resumed"] and gesture_states == [False, True]
        assert stopped_states == [(None, ["paused", "resumed"], True)]
        assert not source._touchpad_requests
        source._touchpad_endpoint = None
        with pytest.raises(ConnectionError):
            await asyncio.wrap_future(source.start_touchpad(on_event=events.append))
    asyncio.run(run())


def test_stroke_gestures_replace_normal_route_and_restore_after_touchpad():
    async def run():
        source = RingAudioSource()
        normal = lambda event: None
        stroke = lambda event: None
        source.gesture_observer = normal
        class Session:
            swipe_active = True
            touchpad_active = False
            mic_active = False
            async def swipe_off(self):
                self.swipe_active = False
                self.callback = None
            async def swipe_on(self, **options):
                assert not self.swipe_active
                self.swipe_active = True
                self.callback = options['on_trigger']
            async def touchpad_on(self, **options):
                self.touchpad_active = True
                self.stopped = options['on_stopped']
            async def touchpad_off(self):
                self.touchpad_active = False
                self.stopped(None)
        session = Session()
        source._touchpad_endpoint = (asyncio.get_running_loop(), session)
        await asyncio.wrap_future(source.start_touchpad(on_event=lambda event: None, on_gesture=stroke))
        assert session.touchpad_active and session.callback is stroke
        await asyncio.wrap_future(source.set_touchpad_gestures(None))
        assert not session.swipe_active
        await asyncio.wrap_future(source.set_touchpad_gestures(stroke))
        assert session.swipe_active and session.callback is stroke
        await asyncio.wrap_future(source.stop_touchpad())
        assert session.callback is normal and session.swipe_active
    asyncio.run(run())


def test_touchpad_failure_does_not_poison_audio_source():
    async def run():
        source = RingAudioSource()
        audio = []
        source.pause_stream = lambda: audio.append("paused")
        source.begin_buffering = lambda: audio.append("resumed")
        class Session:
            mic_active = True
            async def touchpad_on(self, **options): raise RuntimeError('model unavailable')
        source._touchpad_endpoint = (asyncio.get_running_loop(), Session())
        with pytest.raises(RuntimeError, match='model unavailable'):
            await asyncio.wrap_future(source.start_touchpad(on_event=lambda e: None))
        assert audio == ["paused", "resumed"]
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
    output.activate(90)
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


@pytest.mark.parametrize('reason', ['escape', 'disconnect', 'deadline'])
def test_output_stops_even_without_token_traffic(reason):
    ready, done, connection = threading.Event(), threading.Event(), threading.Event()
    mouse = Mouse()
    output = TouchpadMouseOutput(connection=connection, gain=1, clicks=True, invert_y=False,
        on_ready=ready.set, on_end=lambda *args: done.set(), mouse_factory=lambda: mouse)
    output.start()
    assert ready.wait(1)
    if reason == 'escape': mouse.esc = True
    elif reason == 'disconnect': connection.set()
    else: output.activate(.01)
    assert done.wait(.3)
    assert not mouse.enabled


def test_slow_mouse_output_never_blocks_ble_and_queue_is_bounded():
    entered, release, done = threading.Event(), threading.Event(), threading.Event()
    class Slow(Mouse):
        def apply(self, batch):
            entered.set()
            release.wait(2)
    output = TouchpadMouseOutput(connection=threading.Event(), gain=1, clicks=True, invert_y=False,
        on_ready=lambda: None, on_end=lambda *args: done.set(), mouse_factory=Slow)
    output.activate(90); output.start()
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
