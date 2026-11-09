import struct
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from proximic_ring.audio import ring as module


@pytest.fixture
def ring(monkeypatch, tmp_path, request):
    import ring_python_sdk

    state = dict(connect=0, disconnect=0, gestures=0, mic_on=0, callbacks=[],
                 fail=getattr(request, "param", ""), touchpad_fail="")

    class Session:
        def __init__(self, **kwargs):
            self.client = SimpleNamespace(is_connected=True)
            self.mic_active = False
            self.mic = None
            self.touchpad_active = False
            state["session"] = self

        async def connect(self):
            state["connect"] += 1
            return True

        async def mic_on(self, encoding, *, on_pcm):
            state["mic_on"] += 1
            if state["fail"] == "throws":
                raise RuntimeError("MIC ON failed")
            self.mic_active = True
            self.mic = SimpleNamespace(output_path=tmp_path / "capture.wav")
            state["callbacks"].append(on_pcm)
            if state["fail"] != "no-pcm":
                on_pcm(0, struct.pack("<320h", *([4096] * 320)))

        async def mic_off(self):
            self.mic_active = False

        async def disconnect(self):
            state["disconnect"] += 1
            self.client.is_connected = False

        async def touchpad_on(self, **options):
            if state['touchpad_fail']:
                raise RuntimeError(state['touchpad_fail'])
            self.touchpad_stopped = options['on_stopped']
            self.touchpad_active = True

        async def touchpad_off(self):
            if self.touchpad_active:
                self.touchpad_active = False
                self.touchpad_stopped(None)

    async def start_gestures(self, session):
        state["gestures"] += 1
        self.gestures_active = True

    async def noop(*args): pass

    monkeypatch.setattr(ring_python_sdk, "RingSession", Session)
    monkeypatch.setattr(module.RingAudioSource, "_start_gestures", start_gestures)
    monkeypatch.setattr(module.RingAudioSource, "_start_imu_best_effort", noop)
    monkeypatch.setattr(module.RingAudioSource, "_battery_updates", noop)
    for name, value in (("_INITIAL_MIC_SETTLE_S", 0), ("_MIC_RESTART_PAUSE_S", .01),
                        ("_INITIAL_PCM_TIMEOUT_S", .08), ("_WATCHDOG_POLL_S", .005),
                        ("_EARLY_STARTUP_STALL_S", 100)):
        monkeypatch.setattr(module, name, value)
    source = module.RingAudioSource(data_root=tmp_path, audio_enabled=False)
    source.connect()
    source.start_stream()
    yield source, state
    source.close()
    assert state["connect"] == state["disconnect"] == 1


@pytest.mark.parametrize("ring", ["throws", "no-pcm"], indirect=True)
def test_computer_input_never_probes_a_micless_ring_or_restarts_its_audio(ring, monkeypatch):
    source, state = ring
    monkeypatch.setattr(module, "_EARLY_STARTUP_STALL_S", .01)
    monkeypatch.setattr(module, "_WATCHDOG_STALL_S", .02)
    monkeypatch.setattr(module, "_WATCHDOG_CONFIRM_S", .01)
    source.begin_buffering()  # Generic resume must respect the selected input.
    # Even a stale resume signal must not override the explicit source choice.
    source._pause_stream_requested.clear()
    threading.Event().wait(.2)
    assert source.error is None and source.pcm_callbacks == 0
    assert state["mic_on"] == 0 and not state["callbacks"]
    assert not source._watchdog_armed.is_set() and not source._buffer_audio.is_set()
    assert source.gestures_active and state["gestures"] == 1
    assert state["session"].client.is_connected and state["disconnect"] == 0


def test_computer_to_ring_and_back_reuses_ble_and_discards_late_old_pcm(ring):
    source, state = ring
    assert not state["callbacks"]
    previous = None
    for _ in range(3):
        source.set_audio_enabled(True)
        if previous is not None:
            count = source.pcm_callbacks
            previous(2, struct.pack("<320h", *([10000] * 320)))
            assert source.pcm_callbacks == count
        np.testing.assert_allclose(source.read(320), np.full(320, .125, np.float32))
        current = state["callbacks"][-1]
        source.set_audio_enabled(False)
        count = source.pcm_callbacks
        current(1, struct.pack("<320h", *([10000] * 320)))
        assert source.pcm_callbacks == count
        assert source.error is None and source.gestures_active
        assert state["session"].client.is_connected
        previous = current
    assert state["gestures"] == 1 and state["disconnect"] == 0


@pytest.mark.parametrize("failure", ["throws", "no-pcm"])
def test_failed_mic_start_leaves_ble_available_for_rollback_or_retry(ring, failure):
    source, state = ring
    state["fail"] = failure
    with pytest.raises(RuntimeError):
        source.set_audio_enabled(True)
    assert source.error is None and state["disconnect"] == 0
    source.set_audio_enabled(False)
    assert not state["session"].mic_active
    state["fail"] = ""
    source.set_audio_enabled(True)
    assert source.read(320) is not None and source.gestures_active


def test_disconnect_while_waiting_for_pcm_unblocks_switch(ring):
    source, state = ring
    state["fail"] = "no-pcm"
    errors = []

    def start():
        try:
            source.set_audio_enabled(True)
        except Exception as error:
            errors.append(error)

    worker = threading.Thread(target=start)
    worker.start()
    source.close()
    worker.join(1)
    assert not worker.is_alive() and errors


def test_idle_settings_can_interrupt_pcm_gap_without_disconnecting(ring):
    source, state = ring
    source.set_audio_enabled(True)
    assert source.read(320) is not None
    change_pending, read_started = threading.Event(), threading.Event()
    errors = []

    def pending():
        read_started.set()
        return change_pending.is_set()

    def read():
        try:
            source.read_interruptible(320, should_interrupt=pending)
        except InterruptedError as error:
            errors.append(error)

    worker = threading.Thread(target=read, daemon=True)
    worker.start()
    assert read_started.wait(1)
    change_pending.set()
    worker.join(1)
    assert not worker.is_alive() and errors and source.error is None
    source.set_audio_enabled(False)
    assert state["session"].client.is_connected and state["disconnect"] == 0


@pytest.mark.parametrize('audio_enabled', [True, False])
def test_touchpad_keeps_selected_audio_stream_running(ring, audio_enabled):
    source, state = ring
    if audio_enabled:
        source.set_audio_enabled(True)
    previous_callbacks = len(state['callbacks'])
    source.start_touchpad(on_event=lambda _: None).result(2)
    assert state['session'].mic_active == audio_enabled
    assert source.error is None and source.gestures_active
    if audio_enabled:
        fresh = threading.Timer(.02, lambda: state['callbacks'][-1](1, struct.pack('<320h', *([4096] * 320))))
        fresh.start()
        try:
            source.begin_buffering()
        finally:
            fresh.join()
    else:
        source.begin_buffering()
    if audio_enabled:
        state['callbacks'][-1](1, struct.pack('<320h', *([4096] * 320)))
        assert source.read(320) is not None
    source.stop_touchpad().result(2)
    assert state['session'].mic_active == audio_enabled
    assert len(state['callbacks']) == previous_callbacks
    assert state['disconnect'] == 0 and source.error is None


def test_touchpad_start_failure_does_not_interrupt_microphone(ring):
    source, state = ring
    source.set_audio_enabled(True)
    previous_callbacks = len(state['callbacks'])
    state['touchpad_fail'] = 'model unavailable'
    with pytest.raises(RuntimeError, match='model unavailable'):
        source.start_touchpad(on_event=lambda _: None).result(2)
    assert state['session'].mic_active and source.error is None
    assert len(state['callbacks']) == previous_callbacks
    assert not source._touchpad_activities and state['disconnect'] == 0


def test_second_touchpad_start_preserves_active_stream_and_audio(ring):
    source, state = ring
    source.set_audio_enabled(True)
    source.start_touchpad(on_event=lambda _: None).result(2)
    previous_callbacks = len(state['callbacks'])
    with pytest.raises(RuntimeError, match='正在运行'):
        source.start_touchpad(on_event=lambda _: None).result(2)
    assert state['session'].touchpad_active and state['session'].mic_active
    assert len(source._touchpad_activities) == 1
    assert len(state['callbacks']) == previous_callbacks
    assert not source._pause_stream_requested.is_set()
    source.stop_touchpad().result(2)


def test_disconnect_during_touchpad_does_not_restart_microphone(ring):
    source, state = ring
    source.set_audio_enabled(True)
    source.start_touchpad(on_event=lambda _: None).result(2)
    previous_callbacks = len(state['callbacks'])
    source.close()
    assert len(state['callbacks']) == previous_callbacks


def test_stop_queued_during_touchpad_start_cannot_leave_an_orphan_stream(ring):
    import asyncio
    source, state = ring
    source.set_audio_enabled(True)
    session = state['session']
    original_start = session.touchpad_on
    entered, release = threading.Event(), threading.Event()
    async def delayed_start(**options):
        entered.set()
        assert await asyncio.to_thread(release.wait, 2)
        await original_start(**options)
    session.touchpad_on = delayed_start
    starting = source.start_touchpad(on_event=lambda _: None)
    assert entered.wait(2)
    stopping = source.stop_touchpad()
    assert not stopping.done()
    release.set()
    starting.result(2)
    stopping.result(2)
    assert not session.touchpad_active
    assert source._resume_finished.wait(2) and session.mic_active


def test_raw2_stop_can_interrupt_first_token_wait_with_microphone_active(ring):
    import asyncio
    from ring_mac_sdk.errors import RingError

    source, state = ring
    source.set_audio_enabled(True)
    session = state['session']
    entered = threading.Event()
    async def wait_for_token(**options):
        stopped = asyncio.Event()
        def interrupt(error):
            stopped.set()
            options['on_stopped'](error)
        session.touchpad_stopped = interrupt
        session.touchpad_active = True
        entered.set()
        await stopped.wait()
        raise RingError('cancelled')
    session.touchpad_on = wait_for_token
    starting = source.start_touchpad(protocol='raw2', on_event=lambda _: None)
    assert entered.wait(2)
    source.stop_touchpad().result(2)
    with pytest.raises(RingError, match='cancelled'):
        starting.result(2)
    assert not session.touchpad_active and not source._touchpad_activities
    assert session.mic_active and source.gestures_active and source.error is None


def test_automatic_touchpad_end_keeps_microphone_running(ring):
    import asyncio
    source, state = ring
    source.set_audio_enabled(True)
    source.start_touchpad(on_event=lambda _: None).result(2)
    loop, session = source._touchpad_endpoint
    asyncio.run_coroutine_threadsafe(session.touchpad_off(), loop).result(2)
    assert source._resume_finished.wait(2) and session.mic_active
    assert not source.touchpad_active


def test_audio_source_can_switch_while_touchpad_runs(ring):
    source, state = ring
    source.start_touchpad(on_event=lambda _: None).result(2)
    for enabled in (True, False, True):
        source.set_audio_enabled(enabled)
        assert state['session'].mic_active == enabled
        assert state['session'].touchpad_active
    source.stop_touchpad().result(2)
    assert state['session'].mic_active and not state['session'].touchpad_active
