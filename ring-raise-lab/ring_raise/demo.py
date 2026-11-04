"""Deliberately artificial fixtures for pipeline checks, never training evidence."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import struct

import numpy as np

from .common import FIELDS, TASK, sha256, write_json
from .protocol import RawDecoder, IMU_START, verify_vendor


def generate(root):
    root = Path(root)
    if root.exists():
        raise ValueError('合成自检目录已存在，请换一个目录')
    root.mkdir(parents=True)
    plan = dict(mode='personal', train=[], val=[], test=[])
    for visit, split in enumerate(('train','val','test'),1):
        for kind in ('target','negative'):
            name = f'synthetic_v{visit}_{kind}'
            path = root/name
            path.mkdir()
            intervals = [dict(start_s=0.,end_s=5.,label='unknown',activity='settle',trial_id='')]
            if kind == 'target':
                t = 5.
                for trial in range(1,3):
                    for label,duration in [('background',3),('raising',2),('near_mouth',3),('lowering',2)]:
                        intervals.append(dict(start_s=t,end_s=t+duration,label=label,
                                              activity='rest' if label=='background' else 'target',
                                              trial_id='' if label=='background' else f't{trial}'))
                        t += duration
                intervals.append(dict(start_s=t,end_s=30.,label='background',activity='rest',trial_id=''))
            else:
                intervals.append(dict(start_s=5.,end_s=30.,label='background',activity='synthetic_background',trial_id=''))
            decoder = RawDecoder()
            rng = np.random.default_rng(visit*10 + int(kind=='target'))
            scale = np.array([9.80665/2048]*3+[np.pi/180/16.4]*3)
            with (path/'imu.csv').open('w',newline='') as f, (path/'wire.jsonl').open('w') as wire:
                writer=csv.DictWriter(f,fieldnames=FIELDS)
                writer.writeheader()
                for packet in range(600):
                    values=[]
                    for i in range(10):
                        t=(packet*10+i)/200
                        label=next(x['label'] for x in intervals if x['start_s']<=t<x['end_s'])
                        v=np.array([0.,0.,9.80665,0.,0.,0.])
                        if label=='near_mouth': v=np.array([9.80665,0.,0.,0.,0.,0.])
                        elif label=='raising': v=np.array([5.,0.,7.,0.,1.5,0.])
                        elif label=='lowering': v=np.array([5.,0.,7.,0.,-1.5,0.])
                        v += rng.normal(0,[.03,.03,.03,.01,.01,.01])
                        values.extend(np.clip(np.rint(v/scale),-32768,32767).astype(int).tolist())
                    payload=b'\x21\x02'+struct.pack('<HBI',packet,10,1045+packet*50)+struct.pack('<60h',*values)
                    wire.write(json.dumps(dict(synthetic=True,direction='rx',host_ns=packet*50_000_000,hex=payload.hex()))+'\n')
                    writer.writerows(decoder.feed(payload,packet*50_000_000))
            meta=dict(schema=1,task=TASK,session=name,synthetic=True,complete=True,error='',
                      config=dict(participant='synthetic_p01',visit=f'v{visit}',kind=kind,hand='right',
                                  finger='index',posture='synthetic',mount_note='synthetic fixture only'),
                      group=f'synthetic_p01/v{visit}',firmware_version='SYNTHETIC-NOT-HARDWARE',
                      sdk=verify_vendor(),imu_start_hex=IMU_START.hex(),stats=decoder.stats,
                      imu_sha256=sha256(path/'imu.csv'),wire_sha256=sha256(path/'wire.jsonl'))
            intervals[-1]['end_s']=29.995
            write_json(path/'metadata.json',meta)
            write_json(path/'annotations.json',dict(schema=1,reviewed=True,reviewer='synthetic-generator',
                       evidence='synthetic-ground-truth',imu_sha256=meta['imu_sha256'],intervals=intervals))
            plan[split].append(name)
    write_json(root/'splits.json',plan)
    return dict(root=str(root),plan=str(root/'splits.json'),warning='仅合成流程自检，不证明真实戒指效果')
