"""Bridge the supplied Ring Studio driver/codecs to the experiment recorder.

The vendored SDK files remain byte-identical to the supplied archive. This
adapter only assembles packets, retains int16 values, and converts rad/s to
the lab's existing deg/s convention. It never filters the captured IMU.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import struct
import sys
import time

SDK_ROOT = Path(__file__).with_name('vendor') / 'ring_studio'
sys.path.insert(0, str(SDK_ROOT))
from bleak import BleakScanner
from ring.drivers.ringo import Ringo
from ring.drivers.ringo_protocol import IMU_START, MIC_START, decode_packet, firmware_version


def _codec(name):
    # Avoid importing the optional Qt Ringo Lab application package.
    spec = importlib.util.spec_from_file_location(
        '_imu_lab_sdk_' + name, SDK_ROOT / 'ring/apps/ringo_lab' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AdpcmDecoder = _codec('adpcm').AdpcmDecoder
OrderedAudio = _codec('audio').OrderedAudio


@dataclass(frozen=True)
class ImuSample:
    uptime_ms: float
    sample_index: int
    packet_seq: int
    accel_ms2: tuple
    gyro_dps: tuple
    raw: tuple


@dataclass(frozen=True)
class AudioFrame:
    sequence: int
    uptime_ms: int
    pcm: bytes
    sample_rate: int = 16000


class RawSession:
    def __init__(self, on_imu, on_audio, on_wire):
        self.on_imu, self.on_audio, self.on_wire = on_imu, on_audio, on_wire
        self.ring = None
        self.error = ''
        self.previous_seq = None
        self.sample_index = 0
        self.stats = dict(sample_count=0, packet_count=0, estimated_lost_samples=0,
                          dropped_packet_count=0, invalid_packet_count=0)
        self.audio_stats = dict(decoded_blocks=0, incomplete_blocks=0, invalid_packets=0, gap_events=0)
        self.packet_types = Counter()
        self.fragments = {}
        self.audio = OrderedAudio(AdpcmDecoder())
        self.mic_enabled = False
        provenance = json.loads((SDK_ROOT / 'PROVENANCE.json').read_text())
        # Fail closed if a different installed `ring` package shadows this SDK.
        driver_path = Path(sys.modules[Ringo.__module__].__file__).resolve()
        if not driver_path.is_relative_to(SDK_ROOT.resolve()):
            raise RuntimeError('加载到了其他 ring SDK，请重新启动独立实验程序。')
        for name, digest in provenance['files_sha256'].items():
            if hashlib.sha256((SDK_ROOT / name).read_bytes()).hexdigest() != digest:
                raise RuntimeError(f'新 SDK 文件校验失败：{name}')
        self.hardware = dict(sdk_path=str(driver_path), sdk_release=provenance['archive'],
                             sdk_provenance=provenance, stream='raw',
                             imu_start_hex=IMU_START.hex(), mic_start_hex=MIC_START.hex(),
                             sdk_gyro_unit='rad/s', saved_gyro_unit='deg/s',
                             info='正在读取固件版本')

    @property
    def connected(self):
        return bool(self.ring and self.ring.connected)

    async def connect(self, name):
        devices = await BleakScanner.discover(timeout=5)
        exact = [d for d in devices if (d.name or '').lower() == name.lower() or d.address.lower() == name.lower()]
        matches = exact or [d for d in devices if name.lower() in (d.name or '').lower()]
        if not matches:
            raise RuntimeError('未找到戒指。请在 iPhone / Mythlink 等占用它的程序中断开，然后重试。')
        if len(matches) != 1:
            raise RuntimeError('发现多枚戒指，请填写完整名称：' + '、'.join(d.name or d.address for d in matches))
        device = matches[0]
        async def no_reconnect(_):
            pass
        self.ring = Ringo(device, device.name, lambda *_: None, lambda: None, no_reconnect)
        self.ring.packet_callback = self.accept
        self.ring.command_callback = lambda data: self.on_wire('tx', data)
        await asyncio.wait_for(self.ring.connect(), 20)
        self.ring.set_auto_reconnect(False)
        self.hardware.update(name=device.name, address=device.address)

    async def start(self, microphone=True):
        await self.ring.open_imu()  # New SDK's INFO / NORMAL / full raw START.
        if self.error:
            raise RuntimeError(self.error)
        self.mic_enabled = microphone
        if microphone:
            await self.ring.write(MIC_START)

    def accept(self, data):
        data = bytes(data)
        self.on_wire('rx', data)
        self.packet_types[data[:2].hex()] += 1
        try:
            if data[:2] == b'\x2a\x02':
                version = firmware_version(data)
                self.hardware.update(firmware_version=version,
                                     info=f"{self.hardware.get('name', 'Ringo')} · 固件 {version} · raw 六轴",
                                     device_info_hex=data.hex())
            elif data[:2] == b'\x21\x02':
                event = decode_packet(data)
                seq, count, _ = struct.unpack_from('<HBI', data, 2)
                if count != 10:
                    raise ValueError('raw 包帧数与新 SDK 的每包 10 帧配置不一致')
                if self.previous_seq is not None:
                    step = (seq - self.previous_seq) % 65536
                    if not 0 < step < 32768:
                        self.stats['dropped_packet_count'] += 1
                        return
                    lost = (step - 1) * 10
                    self.stats['estimated_lost_samples'] += lost
                    self.sample_index += lost
                self.previous_seq = seq
                self.stats['packet_count'] += 1
                for frame, raw in zip(event['frames'], struct.iter_unpack('<6h', data[9:])):
                    v = frame['values']
                    self.on_imu(ImuSample(frame['timestamp'] * 1000, self.sample_index, seq,
                        tuple(v[:3]), tuple(math.degrees(x) for x in v[3:]), raw))
                    self.sample_index += 1
                    self.stats['sample_count'] += 1
            elif data[:2] in (b'\x21\x04', b'\x21\x05', b'\x21\x0a'):
                raise ValueError('请求 raw 后收到其他 IMU 编码；已停止，避免把 token 当作物理六轴。')
            elif data[:2] == b'\x21\x09' and len(data) >= 3 and data[2]:
                raise ValueError('raw 启动被固件拒绝：' + data.hex(' '))
            elif data[:2] in (b'\x20\x03', b'\x20\x04') and self.mic_enabled:
                event = decode_packet(data)
                if event['encoding'] != 'adpcm':
                    raise ValueError('当前固件输出 Opus；本实验要求 public_dev 的 ADPCM 音频。')
                self._fragment(event)
            elif data[:2] == b'\x2c\x04':
                self.hardware['runtime'] = decode_packet(data)
            elif data[:2] == b'\x29\x02':
                self.hardware['battery'] = decode_packet(data)
        except (ValueError, IndexError, struct.error) as exc:
            if data[:1] == b'\x20':
                self.audio_stats['invalid_packets'] += 1
            else:
                self.stats['invalid_packet_count'] += 1
            self.error = f'新 SDK 数据校验失败：{exc}'

    def _fragment(self, event):
        seq, uptime = event['seq'], event['uptime_ms']
        if (seq - self.audio.next_seq) % 65536 >= 32768 or seq in self.audio.pending:
            return
        now = time.monotonic()
        key = (seq, uptime)
        self._expire(now)
        if key not in self.fragments and len(self.fragments) >= 16:
            del self.fragments[next(iter(self.fragments))]
            self.audio_stats['incomplete_blocks'] += 1
        block = self.fragments.setdefault(key, dict(time=now, count=event['frag_count'], chunks={}))
        index, chunk = event['frag_idx'], bytes.fromhex(event['payload'])
        if block['count'] != event['frag_count'] or (index in block['chunks'] and block['chunks'][index] != chunk):
            raise ValueError('戒指音频分片冲突')
        block['chunks'][index] = chunk
        if len(block['chunks']) != block['count']:
            return
        payload = b''.join(block['chunks'][i] for i in range(block['count']))
        del self.fragments[key]
        if len(payload) < 6 or not 1 <= struct.unpack_from('<H', payload, 4)[0] <= 1600:
            raise ValueError('ADPCM 块的样本数不在 1–1600 范围内')
        self._audio_results(self.audio.push(seq, uptime, payload, now))

    def _expire(self, now):
        for key, block in list(self.fragments.items()):
            if now - block['time'] > 2:
                del self.fragments[key]
                self.audio_stats['incomplete_blocks'] += 1

    def _audio_results(self, results):
        for seq, uptime, samples, error in results:
            if samples is None:
                self.audio_stats['gap_events'] += 1
                self.on_wire('audio_gap', error.encode())
                continue
            self.audio_stats['decoded_blocks'] += 1
            self.on_audio(AudioFrame(seq, uptime, struct.pack('<' + 'h' * len(samples), *samples)))

    def drain(self, force=False):
        self._expire(time.monotonic())
        self._audio_results(self.audio.drain(time.monotonic(), force=force))

    async def disconnect(self):
        try:
            if self.ring:
                await asyncio.wait_for(self.ring.disconnect(), 8)
        finally:
            self.drain(force=True)
            self.audio_stats['incomplete_blocks'] += len(self.fragments)
            self.fragments.clear()
            self.audio.close()
