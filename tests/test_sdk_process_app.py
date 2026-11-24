"""Application integration uses the same public SDK as an external consumer."""
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
import struct
import threading
from types import SimpleNamespace
import time

import pytest

from process_sdk_fakes import Client, Model
from ring_python_sdk.sdk_provider import protocol as p


class MiclessR1(Client):
    async def write_gatt_char(self, uuid, command, *, response):
        if command == p.INFO:
            version = b'test.0.0.1.preview'
            info = bytes.fromhex('2a020102000001000203000000000801010200')
            self.callback(None, info + bytes([len(version)]) + version)
        elif command == p.BATTERY:
            self.callback(None, b'\x29\x02' + struct.pack('<HB', 4080, 85))
        elif command in (b'\x26\x00', b'\x26\x01'):
            self.callback(None, b'\x26\x08' + command[1:] + b'\x00\x00')
            if command == b'\x26\x00':
                probabilities = [float(i == 6) for i in range(12)]
                self.callback(None, b'\x26\x06' + struct.pack('<HB12fIIf', 1, 6, *probabilities, 1234, 1234, 1.0))
        elif command[:1] == b'\x20':
            raise AssertionError('R1 must never receive a microphone command')
        else:
            await super().write_gatt_char(uuid, command, response=response)


def test_application_source_uses_isolated_sdk_without_system_input(monkeypatch, tmp_path):
    from proximic_ring.audio.ring import RingAudioSource
    from ring_python_sdk import RingSession
    from ring_python_sdk.process import ProcessTransport

    original = ProcessTransport.__init__
    def transport(self, session, target, **kwargs):
        session._process_model_factory = Model
        original(self, session, target, client_factory=Client)
    async def sync_time(*args, **kwargs):
        pass
    monkeypatch.setattr(ProcessTransport, '__init__', transport)
    monkeypatch.setattr(RingSession, 'sync_time', sync_time)
    source = RingAudioSource(device=SimpleNamespace(name='Ringo Test', address='offline'),
                             data_root=tmp_path)
    frames = []
    runtime = None
    try:
        source.connect()
        session = source._touchpad_endpoint[1]
        assert session.connection_backend == 'process'
        runtime = session._sdk_transport.runtime
        source.start_touchpad(protocol='raw2', on_event=frames.append, duration_s=None).result(5)
        deadline = time.monotonic()+3
        while not frames and time.monotonic() < deadline:
            time.sleep(.01)
        assert frames
        source.stop_touchpad().result(5)
        assert session.client.is_connected
    finally:
        source.close()
    assert runtime is not None
    assert not runtime.io_process.is_alive() and not runtime.touch_process.is_alive()


def test_micless_r1_connects_with_ring_audio_selected_and_accepts_computer_audio(monkeypatch, tmp_path):
    import numpy as np
    from proximic_ring.audio import ring
    from proximic_ring.audio.live_audio_settings import AudioConfiguration, LiveAudioSettings
    from proximic_ring.voice import app_runtime
    from ring_python_sdk import RingSession
    from ring_python_sdk.process import ProcessTransport

    original = ProcessTransport.__init__

    def transport(self, session, target, **kwargs):
        original(self, session, target, client_factory=MiclessR1)

    async def sync_time(*args, **kwargs):
        pass

    monkeypatch.setattr(ProcessTransport, '__init__', transport)
    monkeypatch.setattr(RingSession, 'sync_time', sync_time)
    monkeypatch.setattr(ring, '_connection_backend', lambda: 'process')
    monkeypatch.setattr(ring, '_INITIAL_MIC_SETTLE_S', 0)

    stop, recognition, retry, started, processed = (threading.Event() for _ in range(5))
    sources, gestures, battery, availability, configured = (Queue() for _ in range(5))
    opened, closed, models = [], [], []
    changes = LiveAudioSettings(AudioConfiguration())
    settings = app_runtime.RuntimeSettings(
        ring_device=SimpleNamespace(name='R10000', address='offline'), data_dir=tmp_path,
        speech_control_mode='gesture', push_to_talk=False, desktop_output=False,
    )

    def microphone(selection):
        def read(frames):
            return None if stop.wait(.01) else np.zeros(frames, dtype=np.float32)

        return SimpleNamespace(error=None, read=read, open=lambda: opened.append(selection),
                               close=lambda: closed.append(selection))

    def build(*args, **kwargs):
        models.append(True)
        return SimpleNamespace(active=False, gesture_busy=False, close=lambda: None,
                               process=lambda *args, **kwargs: processed.set())

    def audio_configured(request, error):
        changes.acknowledge(request)
        configured.put(error)

    monkeypatch.setattr(app_runtime, 'MicrophoneSource', microphone)
    monkeypatch.setattr(app_runtime, '_build_session_controller', build)
    with ThreadPoolExecutor(max_workers=1) as pool:
        runtime = pool.submit(
            app_runtime.RecognitionRuntime(settings).run, stop, recognition,
            on_update=lambda _: None, on_state=lambda _: None, on_connected=lambda: None,
            on_disconnected=lambda: None, on_started=started.set, on_gesture=gestures.put,
            on_battery=lambda *status: battery.put(status),
            on_ring_source=sources.put, on_voice_availability=availability.put,
            audio_settings=changes, on_audio_configured=audio_configured, retry_voice_event=retry,
        )
        try:
            assert started.wait(8)
            source = sources.get_nowait()
            reason = availability.get_nowait()
            assert '没有可用的麦克风' in reason
            assert gestures.get(timeout=3).name == 'snap'
            assert battery.get(timeout=3) == (85, 4080, 0)
            assert source.gestures_active and not source.audio_enabled
            assert not opened and not models and not source.pcm_callbacks
            with pytest.raises(RuntimeError, match='没有可用的麦克风'):
                source.set_audio_enabled(True)

            changes.allowed.set()
            changes.select(audio_source='microphone', microphone_device='test-mic')
            assert configured.get(timeout=3) == ''
            retry.set()
            assert availability.get(timeout=4) == ''
            recognition.set()
            assert processed.wait(3)

            changes.select(audio_source='ring')
            assert configured.get(timeout=3) == reason
            assert changes.applied.audio_source == 'microphone'
            assert source.gestures_active and source.error is None and not stop.is_set()
            assert opened == ['test-mic'] and not closed
        finally:
            stop.set()
            runtime.result(timeout=8)
