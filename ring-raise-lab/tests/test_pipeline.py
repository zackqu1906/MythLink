import copy
import csv
import struct

import numpy as np
import pytest
import torch

from ring_raise.common import CHANNELS, read_json, write_json
from ring_raise.data import (DEFAULT_WINDOW, audit, load_samples, make_window, prepare,
                             session_windows, validate_annotations, validate_plan)
from ring_raise.demo import generate
from ring_raise.inference import OnlinePredictor
from ring_raise.metrics import Trigger, event_metrics
from ring_raise.protocol import RawDecoder, IMU_START, verify_vendor
from ring_raise.train import evaluate, load_npz, load_run, predict, train


def packet(seq=0, uptime=1000, raw=(0, 0, 2048, 164, 0, 0)):
    return b'\x21\x02'+struct.pack('<HBI',seq,10,uptime)+struct.pack('<60h',*(raw*10))


def test_supplied_sdk_provenance_and_units():
    assert verify_vendor()['archive'] == 'ring-studio-ringo-lab-firmware-20261014.zip'
    assert IMU_START.hex() == '2100c800c800d007100a0000'
    rows = RawDecoder().feed(packet(),1)
    assert len(rows) == 10
    assert rows[-1]['az_ms2'] == pytest.approx(9.80665)
    assert rows[-1]['gx_rads'] == pytest.approx(np.pi/18)
    assert rows[-1]['raw_gx'] == 164
    assert rows[-1]['t'] == pytest.approx(.045)


@pytest.mark.parametrize('sub',[4,5,10])
def test_reject_other_imu_encodings(sub):
    with pytest.raises(ValueError):
        RawDecoder().feed(bytes([0x21,sub])+b'\0'*140,1)


@pytest.mark.parametrize('data',[b'\x21\x02',packet()[:-1],packet()+b'\0'])
def test_reject_malformed_packets(data):
    with pytest.raises(ValueError):
        RawDecoder().feed(data,1)


def test_sequence_wrap_duplicates_and_gap():
    d=RawDecoder()
    d.feed(packet(65535,1000),1)
    assert len(d.feed(packet(0,1050),2))==10
    assert d.feed(packet(0,1050),3)==[]
    rows=d.feed(packet(2,1150),4)
    assert rows[-1]['segment']==1
    assert d.stats['missing_packets']==1


def test_device_clock_wrap_is_monotonic():
    d=RawDecoder()
    first=d.feed(packet(0,2**32-5),1)
    second=d.feed(packet(1,45),2)
    assert second[0]['t']>first[-1]['t']
    assert second[-1]['t'] == pytest.approx(.095,abs=1e-6)


def test_conflicting_retransmission_rejected():
    d=RawDecoder();d.feed(packet(),1)
    with pytest.raises(ValueError,match='冲突'):
        d.feed(packet(raw=(0,0,2047,164,0,0)),2)


def test_restart_and_wrong_rate_fail_closed():
    d=RawDecoder();d.feed(packet(20,20000),1)
    with pytest.raises(ValueError,match='重启'):
        d.feed(packet(0,1000),2)
    d=RawDecoder();d.feed(packet(0,1000),1)
    with pytest.raises(ValueError,match='重叠'):
        d.feed(packet(1,1010),2)


def test_window_never_reads_future_and_rejects_gaps():
    t=np.arange(800)/200
    values=np.tile(t[:,None],(1,6)).astype(np.float32)
    segments=np.zeros(len(t))
    x=make_window(t,values,segments,2.)
    assert x.shape == (6,200)
    np.testing.assert_allclose(x[0],t[201:401])
    values[t>2.]=99999
    np.testing.assert_array_equal(x,make_window(t,values,segments,2.))
    segments[t>1.5]=1
    assert make_window(t,values,segments,2.) is None
    assert make_window(t[::10],values[::10],np.zeros(len(t[::10])),2.) is None


def test_saturated_windows_are_not_filled():
    t=np.arange(800)/200
    saturated=np.zeros(len(t),dtype=bool);saturated[300]=True
    assert make_window(t,np.zeros((len(t),6)),np.zeros(len(t)),2.,saturated=saturated) is None


@pytest.fixture(scope='module')
def synthetic(tmp_path_factory):
    root=tmp_path_factory.mktemp('raise_lab')/'sessions'
    generate(root)
    return root


def test_raw_audit_and_review_gate(synthetic):
    path=synthetic/'synthetic_v1_target'
    result=audit(path)
    assert result['usable'] and result['endpoint_rate_hz']==pytest.approx(200)
    ann=read_json(path/'annotations.json')
    validate_annotations(path)
    ann['evidence']='prompt-only'
    with pytest.raises(ValueError,match='复核'):
        validate_annotations(path,ann)


def test_invalid_annotation_order_and_incomplete_trial(synthetic):
    path=synthetic/'synthetic_v1_target'
    ann=read_json(path/'annotations.json')
    ann['intervals'][1]['start_s']=1
    with pytest.raises(ValueError,match='标注'):
        validate_annotations(path,ann)
    ann=read_json(path/'annotations.json')
    ann['intervals'][3]['label']='unknown'
    with pytest.raises(ValueError,match='必须依次'):
        validate_annotations(path,ann)


def test_group_and_subject_leakage_rejected(synthetic):
    plan=read_json(synthetic/'splits.json')
    validate_plan(synthetic,plan)
    plan['val'].append(plan['train'].pop())
    with pytest.raises(ValueError,match='批次'):
        validate_plan(synthetic,plan)
    plan=read_json(synthetic/'splits.json');plan['mode']='cross_subject'
    with pytest.raises(ValueError,match='用户'):
        validate_plan(synthetic,plan)


def test_synthetic_cannot_enter_real_dataset(synthetic,tmp_path):
    with pytest.raises(ValueError,match='合成数据'):
        prepare(synthetic,synthetic/'splits.json',tmp_path/'dataset')


def test_trigger_holds_rearms_and_resets_after_gap():
    trigger=Trigger(.8)
    bg=np.array([1.,0,0,0]);near=np.array([0,0,1.,0])
    assert not trigger.step(0,near)
    assert not trigger.step(.1,near)
    assert trigger.step(.2,near)
    assert not trigger.step(.3,near)
    for t in np.arange(.4,1.5,.1): trigger.step(float(t),bg)
    assert not trigger.step(1.5,near)
    assert not trigger.step(1.6,near)
    assert trigger.step(1.7,near)
    assert not trigger.step(3.,near)  # a gap clears the debounce count


def test_events_use_negative_exposure_not_total_windows():
    ts=np.arange(0,10,.1)
    package=dict(session=np.array(['s']*len(ts)),t=ts,y=np.zeros(len(ts),dtype=int),
                 activity=np.array(['drink']*len(ts)))
    p=np.tile([1.,0,0,0],(len(ts),1));p[10:15]=[0,0,1,0]
    sessions={'s':{'annotations':{'intervals':[dict(start_s=0,end_s=10,label='background',activity='drink',trial_id='')]}}}
    r=event_metrics(package,p,sessions,.8,DEFAULT_WINDOW)
    assert r['negative_seconds']==pytest.approx(10)
    assert r['false_triggers']==1 and r['false_triggers_per_hour']==pytest.approx(360)
    assert r['event_recall'] is None
    assert r['poisson_upper95_per_hour']>r['false_triggers_per_hour']


def test_full_prepare_train_evaluate_and_online_parity(synthetic,tmp_path):
    dataset,run=tmp_path/'dataset',tmp_path/'run'
    prepare(synthetic,synthetic/'splits.json',dataset,True)
    train(dataset,run,kind='stats',epochs=2)
    model,mean,std,config=load_run(run)
    train_data=load_npz(dataset/'train.npz')
    expected=train_data['x'][train_data['train_mask']].mean(axis=(0,2),keepdims=True)
    np.testing.assert_allclose(mean,expected)
    result=evaluate(run)
    assert result['synthetic'] is True and result['target_events']==2
    assert (run/'test.html').is_file()
    with pytest.raises(ValueError,match='已出过'):
        evaluate(run)
    with pytest.raises(ValueError,match='合成'):
        OnlinePredictor(run)
    path=synthetic/'synthetic_v3_target'
    package,_=session_windows(path)
    offline=predict(model,package['x'],mean,std)
    predictor=OnlinePredictor(run,allow_synthetic=True)
    a,_,_=load_samples(path)
    results=[]
    for sample in a:
        results.extend(predictor.feed({k:float(sample[k]) for k in a.dtype.names}))
    indexed={round(r['t'],5):r['probabilities'] for r in results}
    online=np.array([indexed[round(float(t),5)] for t in package['t']])
    np.testing.assert_allclose(online,offline,rtol=1e-5,atol=1e-6)


@pytest.mark.parametrize('bad_stream',[False,True])
def test_capture_handshake_storage_and_cleanup_without_hardware(tmp_path,monkeypatch,bad_stream):
    import sys
    from types import SimpleNamespace
    from ring_raise.capture import Capture
    commands=[]
    class Scanner:
        @staticmethod
        async def discover(timeout):
            return [SimpleNamespace(name='RingoTest',address='TEST-ADDRESS')]
    class Client:
        def __init__(self,device):
            self.is_connected=False
        async def connect(self): self.is_connected=True
        async def start_notify(self,uuid,callback): self.callback=callback
        async def write_gatt_char(self,uuid,data,response):
            commands.append(data)
            if data==b'\x2a\x01':
                self.callback(None,bytes.fromhex('2a0201010200020000'))
            if data==IMU_START:
                if bad_stream:
                    self.callback(None,b'\x21\x05'+b'\0'*128)
                else:
                    for seq in range(400): self.callback(None,packet(seq,1000+50*seq))
        async def stop_notify(self,uuid): pass
        async def disconnect(self): self.is_connected=False
    monkeypatch.setitem(sys.modules,'bleak',SimpleNamespace(BleakClient=Client,BleakScanner=Scanner))
    capture=Capture(tmp_path)
    import time
    capture.connect('TEST-ADDRESS')
    deadline=time.monotonic()+3
    while capture.snapshot()['connection']=='connecting' and time.monotonic()<deadline:
        time.sleep(.01)
    assert capture.snapshot()['connection']=='connected'
    session=capture.start(dict(device='TEST-ADDRESS',participant='p01',visit='v01',hand='right',
                               finger='index',mount_note='mock-only',posture='mock',kind='sanity',streams='imu',seconds=10))
    capture.record_future.result(timeout=5)
    assert capture.snapshot()['connection']=='connected'
    capture.close()
    assert not capture.thread.is_alive()
    meta=read_json(tmp_path/session/'metadata.json')
    assert meta['firmware_version']=='2.0.2'
    assert meta['complete']==(not bad_stream)
    assert commands==[b'\x2a\x01',b'\x20\x01',b'\x21\x01',b'\x2c\x00\x00',IMU_START,b'\x21\x01',b'\x20\x01',b'\x21\x01']
    if not bad_stream:
        assert audit(tmp_path/session)['usable']
    else:
        assert 'raw' in meta['error']
