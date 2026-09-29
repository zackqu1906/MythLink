"""Public firmware packets through SDK lifecycle and app dispatch, without BLE."""
import asyncio
import struct
import threading
import wave
from types import SimpleNamespace

import pytest

from ring_python_sdk import RingSession
from ring_python_sdk.audio.processor import AudioProcessor
from ring_python_sdk.ble.control import send_mic_control
from ring_python_sdk.swipe.processor import SwipeProcessor
from ring_python_sdk.swipe.events import SwipeResult
from proximic_ring.firmware_gestures import FirmwareGestureWorker, GESTURE_NAMES
from proximic_ring.audio.ring import RingAudioSource
from proximic_ring.app_runtime import _gesture_health_status, RuntimeSettings


def compact(cid=5, seq=1):
    return b'\x26\x07' + struct.pack('<HBIf', seq, cid, 1234, .75)


def audio_packet(seq=1):
    block = struct.pack('<hBBH', 100, 0, 0, 1600) + bytes(800)
    return b'\x20\x03' + struct.pack('<HHHI', seq, 0, 1, seq * 100) + block


def test_compact_events_dispatch_all_saved_names_and_ignore_intermediates(tmp_path):
    events, finished = [], threading.Event()
    producer = threading.get_ident()
    def observe(event):
        assert threading.get_ident() != producer
        events.append(event)
        if len(events) == len(GESTURE_NAMES):
            finished.set()
    worker = FirmwareGestureWorker(on_gesture=observe)
    processor = SwipeProcessor(tmp_path / 'gestures.csv', on_trigger=worker.submit,
                               print_triggers=False)
    worker.start()
    try:
        worker.submit(SwipeResult(2, 'event', 0, 5, 0))
        worker.submit(SwipeResult(1, 'trigger', 0, 5, 0))
        for seq, cid in enumerate(GESTURE_NAMES):
            processor.handle_notification(None, compact(cid, seq))
        processor.handle_notification(None, compact(222, 30))
        assert finished.wait(2)
    finally:
        worker.close()
        processor.close()
    assert [e.name for e in events] == list(GESTURE_NAMES.values())
    assert all(e.confidence == .75 and e.timestamp_ms == 1234 for e in events)
    assert worker.snapshot()['processed_events'] == 11
    assert _gesture_health_status({'stream_ready': True, 'last_event_age_ms': 3600000}) == 'running'
    assert _gesture_health_status({'stream_ready': False}) == 'starting'


def test_slow_dispatch_is_bounded_and_discards_stale_backlog():
    entered, release = threading.Event(), threading.Event()
    events = []
    def observe(event):
        events.append(event.sequence)
        entered.set()
        assert release.wait(2)
    worker = FirmwareGestureWorker(on_gesture=observe, queue_capacity=2)
    worker.start()
    def submit(seq):
        worker.submit(SwipeResult(2, 'trigger', seq, 5, 0, confirmed_confidence=.9))
    try:
        submit(0)
        assert entered.wait(1)
        for seq in range(1, 101):
            submit(seq)
        assert worker.snapshot()['queue_depth'] == 2
        assert worker.snapshot()['dropped_events'] == 98
    finally:
        release.set()
        worker.close()
    submit(101)
    assert worker.snapshot()['received_events'] == 101


def test_adpcm_default_decodes_immediately_without_session_memory_or_opus(tmp_path, monkeypatch):
    def no_opus(*a, **kw):
        pytest.fail('ADPCM tried to load Opus')
    import ring_python_sdk.audio.opus_codec as opus
    monkeypatch.setattr(opus, 'OpusBlockDecoder', no_opus)
    callbacks = []
    path = tmp_path / 'mic.wav'
    processor = AudioProcessor(path, on_pcm=lambda seq, pcm: callbacks.append((seq, pcm)))
    for seq in range(100):
        processor.handle_notification(None, audio_packet(seq))
    processor.handle_notification(None, audio_packet(99))  # Duplicate retired block.
    assert len(callbacks) == 100
    assert callbacks[0] == (0, struct.pack('<1600h', *([100] * 1600)))
    assert not processor._public_adpcm.parts
    assert len(processor._buffer) == 0
    processor.close()
    with wave.open(str(path)) as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getnframes()) == (16000, 1, 2, 160000)
    processor.handle_notification(None, audio_packet(100))
    assert len(callbacks) == 100


def test_mic_default_command_first_notification_and_stop_failure_cleanup(tmp_path):
    session = RingSession('test', 1, data_root=tmp_path)
    session.session_dir = tmp_path
    received, commands = [], []
    class Client:
        is_connected = True
        async def write_gatt_char(self, uuid, command, *, response):
            commands.append(command)
            if command[:2] == b'\x20\x00':
                session._demux(None, audio_packet())
            elif command == b'\x20\x01':
                raise RuntimeError('STOP lost')
    session.client = Client()
    async def run():
        await session.mic_on(on_pcm=lambda *args: received.append(args))
        processor = session.mic
        with pytest.raises(RuntimeError, match='STOP lost'):
            await session.mic_off()
        assert processor._closed and session.mic is None and not session.mic_active
    asyncio.run(run())
    assert commands[0] == bytes.fromhex('20 00 01 80 80')
    assert len(received) == 1


@pytest.mark.parametrize('error', [RuntimeError('START lost'), asyncio.CancelledError()])
def test_mic_failed_start_retires_capture_and_attempts_stop(tmp_path, error):
    session = RingSession('test', 1, data_root=tmp_path)
    session.session_dir = tmp_path
    commands, processors = [], []
    class Client:
        is_connected = True
        async def write_gatt_char(self, uuid, command, *, response):
            commands.append(command)
            if command[:2] == b'\x20\x00':
                processors.append(session.mic)
                raise error
    session.client = Client()
    with pytest.raises(type(error)):
        asyncio.run(session.mic_on())
    assert commands[-1] == b'\x20\x01'
    assert processors[0]._closed and session.mic is None and not session.mic_active


def test_quaternion_notifications_are_independent_of_audio_and_gestures(tmp_path):
    session = RingSession('test', 1, data_root=tmp_path)
    packet = b'\x21\x0a' + struct.pack('<HBIB4f', 2, 1, 100, 1, 1, 0, 0, 0)
    frames, commands = [], []
    class Client:
        is_connected = True
        async def write_gatt_char(self, uuid, command, *, response):
            commands.append(command)
            if command[:2] == b'\x21\x00':
                session._demux(None, packet)
    session.client = Client()
    async def run():
        await session.quaternion_on(on_frame=frames.append, sample_rate_hz=50)
        with pytest.raises(RuntimeError, match='quaternion'):
            await session.imu_on()
        await session.stop_all()
        session._demux(None, packet)
    asyncio.run(run())
    assert len(frames) == 1 and frames[0].sample_rate_hz == 50
    assert commands == [bytes.fromhex('21 00 32 00 32 00 d0 07 10 0a 03'), b'\x21\x01']
    assert not session.quaternion_active and session.quaternion_callback is None


def test_app_gesture_lifecycle_without_ring_audio_or_raw_imu(tmp_path):
    states, gestures = [], []
    source = RingAudioSource(data_root=tmp_path, audio_enabled=False,
                             gesture_observer=gestures.append, gesture_state_observer=states.append)
    class Session:
        swipe_active = mic_active = imu_active = False
        async def swipe_on(self, **kwargs):
            self.swipe_active = True
            kwargs['on_trigger']('first firmware gesture')
        async def swipe_off(self): self.swipe_active = False
        async def disconnect(self): self.closed = True
    session = Session()
    async def run():
        await source._start_gestures(session)
        await source._start_imu_best_effort(session)  # No raw observer, no IMU call.
        assert source.gestures_active
        await source._shutdown_session(session)
    asyncio.run(run())
    assert states == [True, False] and gestures == ['first firmware gesture']
    assert not source.gestures_active and session.closed
    assert RuntimeSettings().encoding == source.encoding == 'adpcm'
    assert not RuntimeSettings().collect_imu
