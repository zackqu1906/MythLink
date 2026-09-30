"""SDK touchpad lifecycle, shared BLE routing and optional application integration."""
import asyncio
import struct
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from ring_python_sdk import RingSession
from ring_python_sdk.touchpad import TouchpadMove, TouchpadProcessor
from ring_python_sdk.touchpad.core import START, STOP
from proximic_ring.audio.ring import RingAudioSource


class Backbone:
    def __init__(self):self.threads=[]
    def reset(self):self.threads.append(threading.get_ident())
    def step(self, token):
        self.threads.append(threading.get_ident())
        return np.array([[.4,.1,0.]]*5)


def packet(seq=0, count=10):
    return b'\x21\x05'+struct.pack('<HBIB',seq,count,seq*50,1)+struct.pack('<'+'e'*(count*6),*([0.]*(count*6)))


@pytest.fixture
def fake_inference(monkeypatch):
    from ring_python_sdk.touchpad import stream
    backbones=[]
    def make(_):
        backbone=Backbone();backbones.append(backbone)
        return TouchpadProcessor(backbone=backbone)
    monkeypatch.setattr(stream,'TouchpadProcessor',make)
    return backbones


def make_session(tmp_path):
    session=RingSession('test',1,data_root=tmp_path)
    session.session_dir=tmp_path;session.rx_uuid='rx'
    commands=[]
    class Client:
        is_connected=True
        async def write_gatt_char(self,uuid,data,**kwargs):commands.append(data)
        async def disconnect(self):self.is_connected=False
        async def stop_notify(self,*args):pass
    session.client=Client()
    return session,commands


def test_processor_protocol_gap_reboot_and_no_calibration():
    p=TouchpadProcessor(backbone=Backbone());events=[]
    for n in range(26):
        t=1+n*.05;p.feed(packet(n),arrival=t,now=t)
        for j in range(10):events+=p.poll(t+j*.005+1e-8)
    assert p.stats.tokens==260 and p.stats.warmup_frames==200
    assert any(isinstance(e,TouchpadMove) and e.dx>0 for e in events)
    p.feed(packet(25),arrival=2.3,now=2.3);assert p.stats.duplicate_packets==1
    p.feed(packet(29),arrival=2.4,now=2.4);assert p.stats.resets==1
    p.poll(3);assert p.stats.warmup_frames==0 and p.stats.resets==2
    p.feed(packet(30)[:-1],now=3.1);assert p.stats.invalid_packets==1
    p.feed(packet(31),arrival=3.,now=4.);assert p.stats.stale_packets==1


def test_real_events_on_loop_inference_off_loop_and_shared_routing(tmp_path,fake_inference):
    async def run():
        session,commands=make_session(tmp_path);events=[];seen=asyncio.Event()
        owner=threading.get_ident()
        def event(e):
            assert threading.get_ident()==owner
            events.append(e);seen.set()
        await session.touchpad_on(on_event=event,duration_s=2)
        for seq in range(25):session._demux(None,packet(seq))
        session._demux(None,bytes.fromhex('29 02 00 00 50'))
        assert session.battery_pct==80
        await asyncio.wait_for(seen.wait(),1)
        assert all(i!=owner for i in fake_inference[0].threads)
        await session.touchpad_off();count=len(events)
        session._demux(None,packet(30));await asyncio.sleep(.02)
        assert len(events)==count and not session.touchpad_active and session.touchpad is None
        assert commands==[START,STOP]
    asyncio.run(run())


def test_real_mnn_virtual_ble_stream(tmp_path):
    pytest.importorskip('MNN')
    async def run():
        session,commands=make_session(tmp_path);events=[];seen=asyncio.Event()
        def on_event(event):
            events.append(event)
            seen.set()
        await session.touchpad_on(on_event=on_event,duration_s=5)
        try:
            for seq in range(31):session._demux(None,packet(seq))
            await asyncio.wait_for(seen.wait(),4)
            assert isinstance(events[0],TouchpadMove)
            assert 0<=events[0].contact_probability<=1
            assert np.isfinite((events[0].dx,events[0].dy)).all()
        finally:
            await session.touchpad_off()
        assert commands==[START,STOP]
    asyncio.run(run())


@pytest.mark.parametrize('mode',['imu','quaternion','touchpad'])
def test_existing_mode_cannot_be_replaced(tmp_path,fake_inference,mode):
    session,commands=make_session(tmp_path);setattr(session,mode+'_active',True)
    with pytest.raises(RuntimeError,match='stream'):
        asyncio.run(session.touchpad_on(on_event=lambda e:None))
    assert not commands


def test_touchpad_blocks_raw_modes_and_stop_all_cleans(tmp_path,fake_inference):
    async def run():
        session,commands=make_session(tmp_path)
        await session.touchpad_on(on_event=lambda e:None)
        with pytest.raises(RuntimeError,match='touchpad'):await session.imu_on()
        with pytest.raises(RuntimeError,match='touchpad'):await session.quaternion_on(on_frame=lambda e:None)
        await session.stop_all()
        assert commands==[START,STOP] and session.touchpad is None
    asyncio.run(run())


@pytest.mark.parametrize('error',[RuntimeError('start failed'),asyncio.CancelledError()])
def test_start_failure_and_cancellation_attempt_stop(tmp_path,fake_inference,error):
    async def run():
        session,commands=make_session(tmp_path)
        async def write(uuid,data,**kw):
            commands.append(data)
            if data==START:raise error
        session.client.write_gatt_char=write
        with pytest.raises(type(error)):await session.touchpad_on(on_event=lambda e:None)
        assert session.touchpad is None and not session.touchpad_active
        assert commands==[START,STOP]
    asyncio.run(run())


def test_stop_failure_still_closes_worker(tmp_path,fake_inference):
    async def run():
        session,commands=make_session(tmp_path)
        await session.touchpad_on(on_event=lambda e:None)
        worker=session.touchpad
        async def write(*a,**kw):raise RuntimeError('stop failed')
        session.client.write_gatt_char=write
        with pytest.raises(RuntimeError,match='stop failed'):await session.touchpad_off()
        assert worker.closed and worker.processor is None and not session.touchpad_active
    asyncio.run(run())


def test_timeout_stops_only_touchpad_and_notifies(tmp_path,fake_inference):
    async def run():
        session,commands=make_session(tmp_path);ended=asyncio.Event();errors=[]
        def stopped(error):errors.append(error);ended.set()
        await session.touchpad_on(on_event=lambda e:None,on_stopped=stopped,duration_s=.025)
        await asyncio.wait_for(ended.wait(),1)
        assert errors==[None] and commands==[START,STOP]
        assert session.client.is_connected and session.touchpad is None
    asyncio.run(run())


def test_callback_error_and_disconnect_drop_pending_events(tmp_path,fake_inference):
    async def run():
        session,commands=make_session(tmp_path);ended=asyncio.Event()
        def event(e):raise ValueError('consumer failed')
        await session.touchpad_on(on_event=event,on_stopped=lambda error:ended.set())
        for seq in range(24):session._demux(None,packet(seq))
        await asyncio.wait_for(ended.wait(),1)
        assert isinstance(session.touchpad_error,ValueError)
        await session.touchpad_on(on_event=lambda e:pytest.fail('event after disconnect'))
        session.client.is_connected=False;session._drop_local_streams()
        await session._wait_touchpad_cleanup()
        assert not session.touchpad_active and session.touchpad is None
    asyncio.run(run())


def test_overflow_is_bounded_and_does_not_spill_into_gestures(tmp_path,fake_inference):
    async def run():
        session,commands=make_session(tmp_path)
        await session.touchpad_on(on_event=lambda e:None)
        stream=session.touchpad
        for seq in range(200):session._demux(None,packet(seq))
        assert stream.queue.qsize()<=64 and stream.overflows>0
        await session.touchpad_off()
    asyncio.run(run())


def test_missing_mnn_start_failure_leaves_no_control_writes(tmp_path,monkeypatch):
    from ring_python_sdk.touchpad import stream
    def fail(_):raise RuntimeError('install MNN')
    monkeypatch.setattr(stream,'TouchpadProcessor',fail)
    async def run():
        session,commands=make_session(tmp_path)
        with pytest.raises(RuntimeError,match='MNN'):await session.touchpad_on(on_event=lambda e:None)
        assert not commands and not session.touchpad_active and session.touchpad is None
    asyncio.run(run())


def test_app_source_reuses_session_without_host_gesture_model(tmp_path,fake_inference):
    states=[];source=RingAudioSource(data_root=tmp_path,audio_enabled=False,
        touchpad_observer=lambda e:None,touchpad_state_observer=states.append)
    async def run():
        session,_=make_session(tmp_path)
        await source._start_touchpad(session)
        assert source.touchpad_active
        await source._shutdown_session(session)
        assert not source.touchpad_active and states==[True,False]
    asyncio.run(run())
    with pytest.raises(ValueError,match='share'):
        RingAudioSource(touchpad_observer=lambda e:None,imu_observer=lambda e:None)


def test_audio_gesture_and_touchpad_notifications_share_session(tmp_path,fake_inference):
    async def run():
        session,commands=make_session(tmp_path);audio=[];gestures=[];moves=[]
        await session.mic_on(on_pcm=lambda seq,pcm:audio.append(pcm))
        await session.swipe_on(on_trigger=gestures.append,print_events=False,print_triggers=False,print_profile=False)
        await session.touchpad_on(on_event=moves.append)
        for seq in range(24):session._demux(None,packet(seq))
        session._demux(None,b'\x26\x07'+struct.pack('<HBIf',1,5,1000,.9))
        block=struct.pack('<hBBH',100,0,0,4)+b'\0\0'
        session._demux(None,b'\x20\x03'+struct.pack('<HHHI',1,0,1,1000)+block)
        for _ in range(100):
            if moves and gestures and audio:break
            await asyncio.sleep(.01)
        assert moves and gestures and audio
        assert commands[0]==bytes.fromhex('20 00 01 80 80')
        await session.stop_all()
        assert not any((session.touchpad_active,session.mic_active,session.swipe_active))
    asyncio.run(run())


def test_cancel_during_model_prepare_cleans_executor(tmp_path,monkeypatch):
    from ring_python_sdk.touchpad import stream
    async def prepare(self):await asyncio.sleep(10)
    monkeypatch.setattr(stream.TouchpadStream,'prepare',prepare)
    async def run():
        session,commands=make_session(tmp_path)
        task=asyncio.create_task(session.touchpad_on(on_event=lambda e:None))
        await asyncio.sleep(.005);worker=session.touchpad
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert worker.closed and not commands and session.touchpad is None
    asyncio.run(run())


def test_modes_remain_reserved_until_stop_completes(tmp_path,fake_inference):
    async def run():
        session,commands=make_session(tmp_path);entered=asyncio.Event();release=asyncio.Event()
        await session.touchpad_on(on_event=lambda e:None)
        async def write(uuid,data,**kw):
            commands.append(data)
            if data==STOP:entered.set();await release.wait()
        session.client.write_gatt_char=write
        stop=asyncio.create_task(session.touchpad_off());await entered.wait()
        with pytest.raises(RuntimeError,match='touchpad'):await session.quaternion_on(on_frame=lambda e:None)
        with pytest.raises(RuntimeError,match='touchpad'):await session.imu_on()
        release.set();await stop
        await session.quaternion_on(on_frame=lambda e:None)
        entered.clear();release.clear()
        stop=asyncio.create_task(session.quaternion_off());await entered.wait()
        with pytest.raises(RuntimeError,match='stream'):await session.touchpad_on(on_event=lambda e:None)
        release.set();await stop
    asyncio.run(run())
