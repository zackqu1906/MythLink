"""Cold/warm attachment to firmware whose old stream state may survive BLE."""
import asyncio
import struct
import time
from types import SimpleNamespace

import pytest

from ring_python_sdk import RingSession
from ring_python_sdk.session import sensors
from proximic_ring.audio import ring as module


def confirmed():
    return b'\x26\x07' + struct.pack('<HBIf', 1, 6, 1234, .9)


@pytest.mark.parametrize('audio_enabled', [False, True])
def test_each_connection_settles_and_resets_unknown_firmware_before_start(monkeypatch, tmp_path, audio_enabled):
    import ring_python_sdk
    sessions=[]
    class Session(RingSession):
        async def connect(self):
            self.session_dir=tmp_path/str(len(sessions))
            self.rx_uuid='test-rx'
            self.connected_at=time.monotonic()
            self.writes=[]
            self.device_stale=True  # Survived the previous process/connection.
            outer=self
            class Client:
                is_connected=True
                async def write_gatt_char(self, uuid, packet, *, response):
                    outer.writes.append(packet)
                    if packet==b'\x26\x01':
                        outer.device_stale=False
                        outer.reset_at=time.monotonic()
                    elif packet==b'\x26\x00':
                        assert time.monotonic()-outer.connected_at>=.04
                        assert not outer.device_stale
                        assert time.monotonic()-outer.reset_at>=.025
                        # Firmware can deliver a result before START returns.
                        outer._demux(None, confirmed())
            self.client=Client()
            sessions.append(self)
            return True

        async def mic_on(self, encoding, *, on_pcm):
            self.writes.append(b'mic-on')
            self.mic_active=True
            self.mic=SimpleNamespace(output_path=tmp_path/'audio.wav')
            on_pcm(0,struct.pack('<320h',*([1000]*320)))

        async def mic_off(self):
            self.mic_active=False

        async def disconnect(self):
            self.client.is_connected=False

    async def no_battery(*_):pass
    monkeypatch.setattr(ring_python_sdk,'RingSession',Session)
    monkeypatch.setattr(module,'_INITIAL_MIC_SETTLE_S',.04)
    monkeypatch.setattr(sensors,'_SWIPE_RESET_SETTLE_S',.025)
    monkeypatch.setattr(module.RingAudioSource,'_battery_updates',no_battery)
    for _ in range(2):
        gestures=[]
        source=module.RingAudioSource(data_root=tmp_path, audio_enabled=audio_enabled,
                                      gesture_observer=gestures.append)
        try:
            source.connect()
            assert not source.gestures_active and not sessions[-1].writes
            source.start_stream()
            assert sessions[-1].writes==[b'\x26\x01',b'\x26\x00']+([b'mic-on'] if audio_enabled else [])
            assert len(gestures)==1 and gestures[0].class_id==6
            assert source.gesture_diagnostics()['startup_evidence']=='device_packets'
            assert source.gesture_diagnostics()['device_trigger_count']==1
        finally:
            source.close()
        assert not source.gestures_active and sessions[-1].swipe is None


@pytest.mark.parametrize('fail_packet', [b'\x26\x01', b'\x26\x00'])
def test_failed_reset_or_start_never_publishes_gestures_ready(tmp_path, fail_packet):
    states=[]
    source=module.RingAudioSource(gesture_observer=lambda _:None, gesture_state_observer=states.append)
    session=RingSession('test',1,data_root=tmp_path)
    session.session_dir=tmp_path
    commands=[]
    class Client:
        is_connected=True
        async def write_gatt_char(self, uuid, packet, *, response):
            commands.append(packet)
            if packet==fail_packet:raise RuntimeError('startup write failed')
    session.client=Client()
    with pytest.raises(RuntimeError,match='startup write failed'):
        asyncio.run(source._start_gestures(session))
    assert states==[False] and not source.gestures_active
    assert not session.swipe_active and session.swipe is None
    assert commands==([b'\x26\x01'] if fail_packet==b'\x26\x01' else [b'\x26\x01',b'\x26\x00'])
    assert not source._gesture_activity.active


@pytest.mark.parametrize('cancel_method',['task','disconnect'])
def test_cancel_reset_does_not_send_late_start_or_publish_ready(tmp_path,cancel_method):
    async def run():
        stopped=asyncio.Event()
        commands=[]
        session=RingSession('test',1,data_root=tmp_path)
        class Client:
            is_connected=True
            async def write_gatt_char(self, uuid, packet, *, response):
                commands.append(packet)
                stopped.set()
                await asyncio.Event().wait()
        session.client=Client()
        source=module.RingAudioSource(gesture_observer=lambda _:None)
        task=asyncio.create_task(source._start_gestures(session))
        await stopped.wait()
        if cancel_method=='task':
            task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
        else:
            source._stop.set()
            await task
        assert commands==[b'\x26\x01'] and not source.gestures_active
        assert not source._gesture_activity.active and session.swipe is None
    asyncio.run(run())


def test_idle_stream_is_not_restarted_or_claimed_as_observed(tmp_path):
    async def run():
        session=RingSession('test',1,data_root=tmp_path)
        session.session_dir=tmp_path
        commands=[]
        class Client:
            is_connected=True
            async def write_gatt_char(self, uuid, packet, *, response):commands.append(packet)
        session.client=Client()
        source=module.RingAudioSource(gesture_observer=lambda _:None)
        source._touchpad_endpoint=(asyncio.get_running_loop(),session)
        try:
            await source._start_gestures(session)
            assert source.gestures_active
            assert source.gesture_diagnostics()['startup_evidence']=='start_sent'
            assert source.gesture_diagnostics()['device_packet_count']==0
            # Repeated status sampling must neither infer failure nor send START.
            for _ in range(10):source.gesture_diagnostics()
            assert commands==[b'\x26\x01',b'\x26\x00']
            session._demux(None, confirmed())
            assert source.gesture_diagnostics()['startup_evidence']=='device_packets'
        finally:
            await session.swipe_off()
            source._gesture_activity.close()
    asyncio.run(run())


def test_disconnect_during_initial_settle_never_starts_sensors(monkeypatch,tmp_path):
    import ring_python_sdk
    async def run():
        disconnected=[]
        source=module.RingAudioSource(data_root=tmp_path,audio_enabled=False,
                                      gesture_observer=lambda _:None)
        class Session:
            client=SimpleNamespace(is_connected=True)
            mic_active=False
            def __init__(self,**_):pass
            async def connect(self):
                asyncio.get_running_loop().call_later(.01,source._stop.set)
                return True
            async def swipe_on(self,**_):pytest.fail('START after disconnect requested')
            async def disconnect(self):disconnected.append(True)
        async def no_battery(*_):pass
        monkeypatch.setattr(ring_python_sdk,'RingSession',Session)
        monkeypatch.setattr(module,'_INITIAL_MIC_SETTLE_S',.1)
        monkeypatch.setattr(module.RingAudioSource,'_battery_updates',no_battery)
        source._start_stream.set()
        await source._run_async()
        assert disconnected==[True] and not source.gestures_active
    asyncio.run(run())
