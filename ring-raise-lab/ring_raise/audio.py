"""Loss-aware ADPCM capture. WAV offsets are NOT a continuous device clock."""
from __future__ import annotations

from collections import deque
import json
import math
import struct
import wave

from vendor.ring_studio.adpcm import AdpcmDecoder
from vendor.ring_studio.ringo_protocol import decode_packet

SAMPLE_RATE = 16000


class AudioCapture:
    def __init__(self, path):
        self.wav = wave.open(str(path / 'audio.wav'), 'wb')
        self.wav.setparams((1, 2, SAMPLE_RATE, 0, 'NONE', 'not compressed'))
        self.index = (path / 'audio_blocks.jsonl').open('w', encoding='utf-8')
        self.events = (path / 'audio_events.jsonl').open('w', encoding='utf-8')
        self.decoder = AdpcmDecoder()
        self.pending = {}
        self.next_seq = None
        self.last_uptime = self.last_device = None
        self.epoch_ms = 0
        self.segment = 0
        self.zero_run = 0
        self.recent = deque(maxlen=180)
        self.last_good_host_ns = None
        self.closed = False
        self.stats = dict(blocks=0, samples=0, missing_blocks=0, incomplete_blocks=0,
                          late_fragments=0, timing_gaps=0, clipped_samples=0,
                          zero_blocks=0, max_zero_run_s=0., rms=0., peak=0.)

    def event(self, kind, **fields):
        self.events.write(json.dumps(dict(kind=kind, **fields)) + '\n')

    def feed(self, raw, host_ns):
        if raw[:2] == b'\x20\x04':
            raise ValueError('当前采集器要求随包 dev 固件的 ADPCM；收到 Opus，原包已保留，请核对固件')
        if raw[:2] != b'\x20\x03':
            return
        e = decode_packet(raw)
        seq = e['seq']
        if self.next_seq is None:
            self.next_seq = seq
            self.event('first_sequence', seq=seq, note='启动前或首包前的丢失不可观测')
        if (seq - self.next_seq) % 65536 >= 32768:
            self.stats['late_fragments'] += 1
            return
        block = self.pending.setdefault(seq, dict(uptime=e['uptime_ms'], count=e['frag_count'],
                                                  first_ns=host_ns, last_ns=host_ns, parts={}))
        if block['uptime'] != e['uptime_ms'] or block['count'] != e['frag_count']:
            raise ValueError('相同音频序号的时间或分片数冲突')
        part = bytes.fromhex(e['payload'])
        old = block['parts'].get(e['frag_idx'])
        if old is not None and old != part:
            raise ValueError('同一音频分片内容冲突')
        block['parts'][e['frag_idx']] = part
        block['last_ns'] = host_ns
        if sum(map(len, block['parts'].values())) > 65536:
            raise ValueError('音频块过大')
        self.drain(host_ns)

    def drain(self, now_ns, force=False):
        while self.pending:
            block = self.pending.get(self.next_seq)
            expired = force or len(self.pending) >= 16 or now_ns - min(b['first_ns'] for b in self.pending.values()) >= 2_000_000_000
            if block is None:
                if not expired:
                    break
                seq = min(self.pending, key=lambda s: (s-self.next_seq) % 65536)
                missing = (seq-self.next_seq) % 65536
                self.stats['missing_blocks'] += missing
                self.segment += 1
                self.event('missing_blocks', from_seq=self.next_seq, count=missing)
                self.next_seq = seq
                continue
            if len(block['parts']) != block['count']:
                if not expired:
                    break
                self.stats['incomplete_blocks'] += 1
                self.segment += 1
                self.event('incomplete_block', seq=self.next_seq, received=sorted(block['parts']), expected=block['count'])
            else:
                data = b''.join(block['parts'][i] for i in range(block['count']))
                samples = self.decoder.decode(data)
                if len(samples) > SAMPLE_RATE:
                    raise ValueError('单个音频块超过 1 秒，核对协议')
                self._write(self.next_seq, block, samples)
            del self.pending[self.next_seq]
            self.next_seq = (self.next_seq+1) % 65536

    def _write(self, seq, block, samples):
        uptime = block['uptime']
        if self.last_uptime is not None and uptime < self.last_uptime:
            if self.last_uptime-uptime > 2**31:
                self.epoch_ms += 2**32
            else:
                raise ValueError('音频设备时间倒退/重启')
        device_s = (uptime+self.epoch_ms)/1000
        if self.last_device is not None:
            dt = device_s-self.last_device
            if dt <= 0:
                raise ValueError('音频时间未递增')
            if abs(dt-len(samples)/SAMPLE_RATE) > .03:
                self.stats['timing_gaps'] += 1
                self.segment += 1
                self.event('timestamp_gap', seq=seq, interval_s=dt)
        self.last_uptime, self.last_device = uptime, device_s
        zero = not any(samples)
        self.zero_run = self.zero_run+len(samples)/SAMPLE_RATE if zero else 0.
        rms = math.sqrt(sum(v*v for v in samples)/len(samples))/32768
        peak = max(map(abs, samples))/32768
        row = dict(seq=seq, uptime_ms=uptime, device_packet_s=device_s,
                   host_first_ns=block['first_ns'], host_last_ns=block['last_ns'],
                   wav_start_sample=self.stats['samples'], sample_count=len(samples),
                   sample_rate=SAMPLE_RATE, segment=self.segment, rms=rms, peak=peak,
                   all_zero=zero, timestamp_reference='unknown: packet time, not confirmed sample start/end')
        self.index.write(json.dumps(row) + '\n')
        self.wav.writeframesraw(struct.pack('<'+'h'*len(samples), *samples))
        self.recent.append([self.stats['samples']/SAMPLE_RATE, rms, peak])
        self.stats['blocks'] += 1
        self.stats['samples'] += len(samples)
        self.stats['zero_blocks'] += int(zero)
        self.stats['clipped_samples'] += sum(abs(s) >= 32760 for s in samples)
        self.stats.update(max_zero_run_s=max(self.stats['max_zero_run_s'], self.zero_run), rms=rms, peak=peak)
        self.last_good_host_ns = block['last_ns']

    def snapshot(self):
        return {**self.stats, 'duration_s': self.stats['samples']/SAMPLE_RATE,
                'pending_blocks': len(self.pending), 'plot': list(self.recent),
                'warning': '连续全零音频：请说话检查；不能把缺失信号标成静默' if self.zero_run >= 2 else ''}

    def close(self, now_ns):
        if self.closed:
            return
        try:
            self.drain(now_ns, force=True)
        finally:
            self.wav.close()
            self.index.close()
            self.events.close()
            self.closed = True
