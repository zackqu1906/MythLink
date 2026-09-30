import struct
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from proximic_ring.audio import ring as module


@pytest.fixture
def ring(monkeypatch, tmp_path):
    import ring_python_sdk

    state = dict(connect=0, disconnect=0, gestures=0, callbacks=[], fail="")

    class Session:
        def __init__(self, **kwargs):
            self.client = SimpleNamespace(is_connected=True)
            self.mic_active = False
            self.mic = None
            state["session"] = self

        async def connect(self):
            state["connect"] += 1
            return True

        async def mic_on(self, encoding, *, on_pcm):
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
