#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description='原始六轴抬到嘴边实验：采集、复核、训练、事件评估')
    sub = parser.add_subparsers(dest='command', required=True)
    p=sub.add_parser('serve',help='打开本地采集与标注界面')
    p.add_argument('--root',type=Path,default=ROOT/'data/sessions')
    p.add_argument('--port',type=int,default=8767)
    p.add_argument('--no-browser',action='store_true')
    p=sub.add_parser('audit',help='检查一段原始采集')
    p.add_argument('session',type=Path)
    p=sub.add_parser('prepare',help='按冻结的批次划分构建窗口')
    p.add_argument('--root',type=Path,default=ROOT/'data/sessions')
    p.add_argument('--plan',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--allow-synthetic',action='store_true')
    p=sub.add_parser('train',help='只使用 train/val 训练并选择阈值')
    p.add_argument('--dataset',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--model',choices=['stats','cnn'],default='cnn')
    p.add_argument('--epochs',type=int,default=40)
    p.add_argument('--seed',type=int,default=42)
    p.add_argument('--false-triggers-per-hour',type=float,default=1.)
    p=sub.add_parser('evaluate',help='一次冻结测试集评估')
    p.add_argument('--run',type=Path,required=True)
    p=sub.add_parser('replay',help='按逐样本实时逻辑回放一段数据')
    p.add_argument('--run',type=Path,required=True)
    p.add_argument('--session',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p=sub.add_parser('live',help='真实模型实时观测，仅打印候选，不触发其他应用')
    p.add_argument('--run',type=Path,required=True)
    p.add_argument('--device',required=True)
    p.add_argument('--participant',required=True)
    p.add_argument('--visit',required=True)
    p.add_argument('--hand',choices=['left','right'],required=True)
    p.add_argument('--finger',required=True)
    p.add_argument('--mount-note',required=True)
    p.add_argument('--seconds',type=float,default=120)
    p.add_argument('--root',type=Path,default=ROOT/'data/live')
    p=sub.add_parser('demo',help='生成有显式标记的合成流程自检数据')
    p.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    if args.command=='serve':
        from ring_raise.server import serve
        serve(args.root,args.port,not args.no_browser)
        return
    elif args.command=='audit':
        from ring_raise.data import audit
        result=audit(args.session)
    elif args.command=='prepare':
        from ring_raise.data import prepare
        result=prepare(args.root,args.plan,args.out,args.allow_synthetic)
    elif args.command=='train':
        from ring_raise.train import train
        result=train(args.dataset,args.out,args.model,args.epochs,args.seed,args.false_triggers_per_hour)
    elif args.command=='evaluate':
        from ring_raise.train import evaluate
        result=evaluate(args.run)
    elif args.command=='replay':
        from ring_raise.inference import replay
        result=replay(args.run,args.session,args.out)
    elif args.command=='demo':
        from ring_raise.demo import generate
        result=generate(args.out)
    elif args.command=='live':
        from ring_raise.capture import Capture
        from ring_raise.inference import OnlinePredictor
        capture=Capture(args.root,OnlinePredictor(args.run))
        capture.connect(args.device)
        while capture.snapshot()['connection']=='connecting':
            time.sleep(.1)
        if capture.snapshot()['connection']!='connected':
            raise RuntimeError(capture.snapshot()['connection_error'] or '连接失败')
        capture.start(dict(device=args.device,participant=args.participant,visit=args.visit,
                           hand=args.hand,finger=args.finger,mount_note=args.mount_note,
                           posture='live-observation',kind='sanity',streams='imu',seconds=args.seconds,activity='unknown'))
        last=None
        try:
            while capture.is_recording():
                status=capture.snapshot()
                pred=status.get('prediction')
                if pred and pred['t']!=last:
                    print(json.dumps(pred,ensure_ascii=False),flush=True)
                    last=pred['t']
                time.sleep(.1)
        except KeyboardInterrupt:
            capture.stop()
        finally:
            if capture.record_future:
                capture.record_future.result(timeout=15)
            capture.close()
        result={k:v for k,v in capture.snapshot().items() if k!='plot'}
    print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    if args.command=='audit' and not result['usable']:
        return 2
    if args.command=='live' and result.get('error'):
        return 2
    return 0


if __name__=='__main__':
    try:
        sys.exit(main() or 0)
    except (ValueError,RuntimeError,FileNotFoundError,KeyError) as exc:
        print(f'错误：{exc}',file=sys.stderr)
        sys.exit(2)
