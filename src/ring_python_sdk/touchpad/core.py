"""Public 1.4.0 token/trajectory/click adapter. No desktop event injection.

Ported from supplied AAR b.f, e.m/n/d/h/g/i contracts. Timing uses monotonic
seconds; model step is fixed at 200 Hz. Cross-Android numeric parity is pending.
"""
from collections import deque
import math
import struct
import numpy as np

START = bytes.fromhex('21 00 c8 00 c8 00 d0 07 10 0a 01')
STOP = b'\x21\x01'
WRITE = '6e400002-b5a3-f393-e0a9-e50e24dcca9e'
NOTIFY = '6e400003-b5a3-f393-e0a9-e50e24dcca9e'
CD_SPEED = [0,.05,.1,.2,.325,.5,.75,1.1,1.55,2.15,3,4]
CD_GAIN = [390,390,405,440,495,565,655,820,1025,1335,1750,2220]


def parse_tokens(packet):
    if packet[:2] != b'\x21\x05': return None
    if len(packet)<10: raise ValueError('token 数据头不完整')
    seq,count,uptime,version=struct.unpack_from('<HBIB',packet,2)
    if not 1<=count<=20 or version!=1 or len(packet)!=10+count*12:
        raise ValueError('不支持的 token 数据格式')
    values=np.asarray(struct.unpack_from('<'+'e'*(count*6),packet,10),np.float32).reshape(count,6)
    if not np.isfinite(values).all(): raise ValueError('token 含无效数值')
    return seq,uptime,values


class ClickDetector:
    def __init__(self): self.reset()
    def reset(self):
        self.window=deque(maxlen=15); self.raw={}; self.pending=deque()
        self.start=self.transition=self.last_prob=self.last_move=None
    def probability(self,index,p):
        if self.last_prob is not None and index!=self.last_prob+1: self.reset()
        self.last_prob=index; self.window.append((index,p))
        if len(self.window)<15:return []
        sorted_p=sorted(v for _,v in self.window); low,high=sorted_p[2],sorted_p[12]
        if high-low<=.5+1e-12:return []
        hi=[i for i,v in self.window if v>=high][-3:]
        lo=[i for i,v in self.window if v<=low][-3:]
        previous,current=(lo,hi) if self.start is None else (hi,lo)
        if previous[-1]>=current[0] or current[-1]!=index:return []
        if self.transition is not None and current[0]<=self.transition:return []
        self.transition=index
        if self.start is None:self.start=current[0]
        else:
            self.pending.append((self.start,current[0]));self.start=None
        return self.ready()
    def movement(self,index,velocity):
        self.raw[index]=velocity;self.last_move=index
        for k in list(self.raw):
            if k<index-800:del self.raw[k]
        return self.ready()
    def ready(self):
        result=[]
        while self.pending and self.last_move is not None and self.last_move>=self.pending[0][1]-1:
            start,end=self.pending.popleft()
            if not 0<end-start<40 or any(k not in self.raw for k in range(start,end)):continue
            dx=sum(self.raw[k][0] for k in range(start,end))*.005
            dy=sum(self.raw[k][1] for k in range(start,end))*.005
            if math.hypot(dx,dy)<=.02:result.append({'kind':'click','step':end})
        return result


class Postprocessor:
    def __init__(self):self.reset()
    def reset(self):
        self.n=0;self.vel={};self.prob={};self.times={};self.last=0
        self.next_step=self.next_time=self.last_time=None;self.catchup=False
        self.click=ClickDetector();self.events=[];self.contact=0.
    def add(self,output,arrival,frame_time):
        output=np.asarray(output)
        if output.shape!=(5,3) or not np.isfinite(output).all():raise ValueError('模型输出无效')
        self.n+=1; n=self.n;self.times[n]=(frame_time,arrival)
        if n>200:
            for j,(vx,vy,logit) in enumerate(output):
                k=n+j-2
                if k>200:
                    if k>self.last:self.vel.setdefault(k,[]).append((float(vx),float(vy)))
                    p=1/(1+math.exp(-max(-80,min(80,float(logit)))))
                    self.prob.setdefault(k,[]).append(p)
            for k in sorted(k for k in self.prob if k<=n-2):
                samples=self.prob.pop(k);self.contact=sum(samples)/len(samples)
                self.events.extend(self.click.probability(k,self.contact))
        for k in list(self.times):
            if k<n-66 and k not in self.vel:del self.times[k]
    def drain(self,now):
        available=sorted(self.vel.keys() & self.times.keys())
        for k in available:
            if now-self.times[k][0]<=.1:break
            del self.vel[k];del self.times[k];self.last=max(self.last,k)
            self.click.reset()  # Never allow a click to span discarded movement.
            if self.next_step is not None and k>=self.next_step:self.next_step=k+1
        available=sorted(self.vel.keys() & self.times.keys())
        if self.next_time is None:
            if not available:return self.take_events()
            self.next_step=available[0];self.next_time=max(now,(self.last_time or now-.005)+.005)
        if now<self.next_time:return self.take_events()
        k=self.next_step
        if k not in available:
            self.next_time=self.next_step=None
            return self.take_events()
        v=np.mean(self.vel.pop(k),axis=0)
        x=float(v[0])*1.595;y=float(v[1])
        cd=float(np.interp(math.hypot(x,y),CD_SPEED,CD_GAIN));gain=cd*.7*.005
        dx,dy=x*gain*.75,y*gain*1.10
        if math.hypot(dx,dy)<.1:dx=dy=0.
        self.events.append({'kind':'move','step':k,'dx':dx,'dy':dy,'contact':self.contact})
        self.events.extend(self.click.movement(k,(float(v[0]),float(v[1]))))
        del self.times[k];self.last=k;self.last_time=now;self.next_step=k+1
        available=sorted(self.vel.keys() & self.times.keys())
        if not available:self.next_step=self.next_time=None;self.catchup=False
        else:
            age=now-self.times[available[0]][1]
            if self.catchup and age<.015:self.catchup=False
            elif not self.catchup and age>.025:self.catchup=True
            interval=.00475 if self.catchup else .005
            self.next_time+=interval
            if self.next_time<=now:self.next_time+=(int((now-self.next_time)/interval)+1)*interval
        return self.take_events()
    def take_events(self):
        events,self.events=self.events,[]
        return events
