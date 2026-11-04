import asyncio
import copy
import json
from pathlib import Path
import struct
import sys
import time
from types import SimpleNamespace
import wave

import pytest

from ring_raise.audio import AudioCapture
from ring_raise.capture import Capture
from ring_raise.common import read_json
from ring_raise.multimodal import audio_audit, validate_multimodal
from ring_raise.protocol import IMU_START


def block(value=800, count=1600):
    return struct.pack('<hBBH', value, 0, 0, count)+b'\0'*(count//2)


def fragments(seq=0, uptime=1000, value=800):
    raw = block(value)
    chunks = [raw[:400], raw[400:]]
    return [b'\x20\x03'+struct.pack('<HHHI',seq,i,2,uptime)+c for i,c in enumerate(chunks)]


def imu(seq, uptime):
    return b'\x21\x02'+struct.pack('<HBI',seq,10,uptime)+struct.pack('<60h',*((0,0,2048,0,0,0)*10))


def wait_for(predicate, timeout=3):
    deadline=time.monotonic()+timeout
    while not predicate():
        if time.monotonic()>deadline:
            raise AssertionError('timed out')
        time.sleep(.01)


def test_audio_reassembly_order_wrap_and_wav_index(tmp_path):
    a=AudioCapture(tmp_path)
    f=fragments(65535,2**32-50)
    a.feed(f[1],100)
    a.feed(f[1],101)  # equal duplicate, no duplicate PCM
    a.feed(f[0],102)
    for p in fragments(0,50): a.feed(p,200)
    a.close(201)
    rows=[json.loads(x) for x in (tmp_path/'audio_blocks.jsonl').read_text().splitlines()]
    assert [r['wav_start_sample'] for r in rows]==[0,1600]
    assert rows[1]['device_packet_s']-rows[0]['device_packet_s']==pytest.approx(.1)
    with wave.open(str(tmp_path/'audio.wav')) as w:
        assert w.getnframes()==3200 and w.getframerate()==16000
        assert struct.unpack('<h',w.readframes(1))[0]==800


def test_audio_missing_fragment_and_missing_block_are_not_silence(tmp_path):
    a=AudioCapture(tmp_path)
    a.feed(fragments(0,1000)[0],100)
    for p in fragments(2,1200): a.feed(p,200)
    a.close(300)
    assert a.stats['incomplete_blocks']==1 and a.stats['missing_blocks']==1
    assert a.stats['samples']==1600  # only received valid audio, no silent padding
    row=json.loads((tmp_path/'audio_blocks.jsonl').read_text())
    assert row['segment']>0 and row['seq']==2


def test_audio_late_blocks_conflicts_codec_and_zero_warning(tmp_path):
    a=AudioCapture(tmp_path)
    for seq in range(21):
        for p in fragments(seq,1000+100*seq,0): a.feed(p,seq*100000000)
    assert a.snapshot()['warning'] and a.stats['max_zero_run_s']>=2
    for p in fragments(0): a.feed(p,3000000000)
    assert a.stats['late_fragments']==2 and a.stats['samples']==33600
    with pytest.raises(ValueError,match='Opus'): a.feed(b'\x20\x04',0)
    f=fragments(21,3100)
    a.feed(f[0],3100000000)
    with pytest.raises(ValueError,match='冲突'): a.feed(f[0][:-1]+b'\x01',3100000001)
    a.close(3200000000)


def test_audio_out_of_order_complete_blocks_and_reboot(tmp_path):
    a=AudioCapture(tmp_path)
    f=fragments(0,1000)
    a.feed(f[0],1)
    for p in fragments(1,1100): a.feed(p,2)
    assert a.stats['blocks']==0
    a.feed(f[1],3)
    assert a.stats['blocks']==2
    f=fragments(2,100)
    a.feed(f[0],4)
    with pytest.raises(ValueError,match='倒退'): a.feed(f[1],5)
    with pytest.raises(ValueError): a.close(6)
    assert a.closed


@pytest.fixture
def fake_ble(monkeypatch):
    control=SimpleNamespace(commands=[],clients=[],mode='normal')
    class Scanner:
        @staticmethod
        async def discover(timeout):
            return [SimpleNamespace(name='RingoTest',address='TEST')]
    class Client:
        def __init__(self, device):
            self.is_connected=False
            self.mic=False
            self.imu=False
            self.task=None
            self.i=self.a=0
            control.clients.append(self)
        async def connect(self): self.is_connected=True
        async def start_notify(self,uuid,callback): self.callback=callback
        async def write_gatt_char(self,uuid,data,response):
            control.commands.append(data)
            if data==b'\x2a\x01': self.callback(None,bytes.fromhex('2a0201010200020000'))
            elif data==b'\x20\x00': self.mic=True; self.a=0
            elif data==b'\x20\x01': self.mic=False
            elif data==IMU_START: self.imu=True; self.i=0
            elif data==b'\x21\x01': self.imu=False
            if (self.mic or self.imu) and (not self.task or self.task.done()):
                self.task=asyncio.create_task(self.stream())
        async def stream(self):
            while self.is_connected and (self.mic or self.imu):
                if self.mic and control.mode!='missing_audio':
                    for p in fragments(self.a,1000+self.a*100): self.callback(None,p)
                    self.a+=1
                if self.imu:
                    for _ in range(2):
                        self.callback(None,imu(self.i,1000+self.i*50));self.i+=1
                await asyncio.sleep(.015)
        async def stop_notify(self,uuid): pass
        async def disconnect(self):
            self.is_connected=False
            if self.task:
                await self.task
    monkeypatch.setitem(sys.modules,'bleak',SimpleNamespace(BleakClient=Client,BleakScanner=Scanner))
    return control


def config(**extra):
    return dict(participant='p1',visit='v1',hand='right',finger='index',mount_note='mock',posture='seated',
                scenario='sanity',streams='imu_audio',seconds=10,environment='mock; no real device',**extra)


def test_explicit_connection_stop_keeps_connection_and_repeat_recording(tmp_path,fake_ble):
    c=Capture(tmp_path)
    with pytest.raises(ValueError,match='连接戒指'): c.start(config())
    c.connect('TEST')
    wait_for(lambda:c.snapshot()['connection']=='connected')
    assert b'\x20\x00' not in fake_ble.commands and IMU_START not in fake_ble.commands
    try:
        for _ in range(2):
            session=c.start(config())
            wait_for(lambda:c.snapshot()['state']=='recording')
            c.stop()
            c.record_future.result(4)
            assert c.snapshot()['connection']=='connected' and c.snapshot()['state']=='complete'
            meta=read_json(tmp_path/session/'metadata.json')
            assert meta['complete'] and meta['audio']['samples']>0 and meta['stats']['samples']>0
            assert audio_audit(tmp_path/session)['usable']
            ann=read_json(tmp_path/session/'multimodal_annotations.json')
            assert not ann['reviewed'] and ann['speech_intervals']==[] and not ann['alignment']['reviewed']
        assert len(fake_ble.clients)==1
    finally:
        c.close()
    assert c.snapshot()['connection']=='disconnected' and not c.thread.is_alive()
    assert fake_ble.commands[-2:]==[b'\x20\x01',b'\x21\x01']


@pytest.mark.parametrize('drop',[False,True])
def test_disconnect_mid_record_preserves_partial_data(tmp_path,fake_ble,drop):
    c=Capture(tmp_path);c.connect('TEST')
    wait_for(lambda:c.snapshot()['connection']=='connected')
    session=c.start(config())
    wait_for(lambda:c.snapshot()['state']=='recording')
    if drop:
        fake_ble.clients[-1].is_connected=False
    else:
        c.disconnect()
    c.thread.join(5)
    assert not c.thread.is_alive()
    meta=read_json(tmp_path/session/'metadata.json')
    assert not meta['complete'] and meta['error']
    assert (tmp_path/session/'audio.wav').is_file() and (tmp_path/session/'annotations.json').is_file()
    assert not audio_audit(tmp_path/session)['usable']


def test_missing_modality_is_error_not_silent_success(tmp_path,fake_ble):
    fake_ble.mode='missing_audio'
    c=Capture(tmp_path);c.stream_timeout_s=.12;c.connect('TEST')
    wait_for(lambda:c.snapshot()['connection']=='connected')
    try:
        session=c.start(config())
        c.record_future.result(4)
        meta=read_json(tmp_path/session/'metadata.json')
        assert not meta['complete'] and '音频' in meta['error']
        assert c.snapshot()['connection']=='connected'
    finally:
        c.close()


def test_multimodal_annotation_separates_voice_and_intent(tmp_path,fake_ble):
    c=Capture(tmp_path);c.connect('TEST')
    wait_for(lambda:c.snapshot()['connection']=='connected')
    session=c.start(config())
    wait_for(lambda:c.snapshot()['t']>.5)
    c.stop();c.record_future.result(4);c.close()
    p=tmp_path/session
    ann=read_json(p/'multimodal_annotations.json')
    with pytest.raises(ValueError,match='复核者'): validate_multimodal(p,ann)
    ann.update(reviewed=True,reviewer='tester',evidence='listened+observed',
               speech_intervals=[dict(start_s=0,end_s=.1,label='other_speech')],
               intent_intervals=[dict(start_s=0,end_s=.1,label='not_directed')])
    valid=validate_multimodal(p,ann)
    assert not valid['alignment']['reviewed'] and 'audio_blocks.jsonl' in valid['source_hashes']
    bad=copy.deepcopy(ann);bad['speech_intervals'][0]['end_s']=999
    with pytest.raises(ValueError,match='越界'): validate_multimodal(p,bad)
    bad=copy.deepcopy(ann);bad['alignment']['reviewed']=True
    with pytest.raises(ValueError,match='时间戳'): validate_multimodal(p,bad)
    with (p/'audio.wav').open('ab') as f: f.write(b'changed')
    with pytest.raises(ValueError,match='哈希'): validate_multimodal(p,ann)


def test_original_decoupled_cnn_frontend_contract_and_no_old_weights():
    import numpy as np
    import torch
    from ring_raise.audio_model import SpeechFrontend, make_speech_model, verify_original, SPEECH_LABELS
    from vendor.decoupled_original.model import CnnNet8
    assert verify_original()['weights_copied'] is False
    frontend=SpeechFrontend()
    samples=(np.sin(np.arange(16000)*.02)*8000).astype(np.int16)
    features=frontend.extract_pcm16(samples)
    assert features.shape==(20,201) and np.isfinite(features).all()
    model=make_speech_model().eval()
    assert type(model) is CnnNet8 and SPEECH_LABELS==('speech','non_speech')
    with torch.inference_mode():
        assert model(torch.from_numpy(features)[None]).shape==(1,2)
    with pytest.raises(ValueError,match='1 秒'): frontend.extract_pcm16(samples[:1600])
    with pytest.raises(ValueError): frontend.extract_pcm16(samples.astype(np.float32))


def test_old_and_new_gesture_label_semantics_cannot_mix(tmp_path):
    from ring_raise.demo import generate
    from ring_raise.data import prepare
    from ring_raise.common import write_json
    root=tmp_path/'sessions';generate(root)
    plan=read_json(root/'splits.json')
    path=root/plan['train'][0]/'metadata.json'
    meta=read_json(path);meta['gesture_label_schema']='mouth_raise_phases_v2_intent_independent';write_json(path,meta)
    with pytest.raises(ValueError,match='不能混合旧版'):
        prepare(root,root/'splits.json',tmp_path/'dataset',True)
