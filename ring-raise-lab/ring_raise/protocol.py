"""Strict raw-only wrapper; original supplied SI conversion remains unchanged."""
from __future__ import annotations

import struct

from .common import ROOT, read_json, sha256, CHANNELS, RAW_CHANNELS
from vendor.ring_studio.ringo_protocol import IMU_START, firmware_version
from vendor.ring_studio.nus_protocol import RX, TX, decode_imu


def verify_vendor():
    provenance = read_json(ROOT / 'vendor/PROVENANCE.json')
    for path, item in provenance['files'].items():
        if sha256(ROOT / path) != item['sha256']:
            raise ValueError(f'SDK 源文件校验失败：{path}')
    return provenance


class RawDecoder:
    """Keep device time, unwrap counters; reject restarts and incorrect modes.

    Packet endpoint time is supplied by the device. Within-packet times come
    from the SDK's requested 200 Hz, not independently observed hardware times.
    """
    def __init__(self):
        self.last_seq = self.last_uptime = self.last_end = self.origin = None
        self.epoch_ms = 0
        self.last_packet = None
        self.segment = 0
        self.stats = dict(packets=0, samples=0, duplicates_or_old=0, missing_packets=0,
                          gaps=0, clipped_samples=0)

    def feed(self, packet, host_ns):
        packet = bytes(packet)
        if packet[:2] in (b'\x21\x04', b'\x21\x05', b'\x21\x0a'):
            raise ValueError('请求 raw 却收到 delta/token/其他编码；停止，避免混淆数据语义')
        if packet[:2] != b'\x21\x02':
            return []
        if len(packet) < 9:
            raise ValueError('raw IMU 包头不完整')
        seq, count, uptime = struct.unpack_from('<HBI', packet, 2)
        if count != 10 or len(packet) != 9 + count * 12:
            raise ValueError('原始包必须与请求一致：10 帧，每帧 6×int16')
        step = 1 if self.last_seq is None else (seq - self.last_seq) % 65536
        if self.last_seq is not None and not 0 < step < 32768:
            if seq == self.last_seq and uptime == self.last_uptime and packet != self.last_packet:
                raise ValueError('相同序号/时间戳出现冲突的 IMU 数据')
            # Device reboot is not a packet reordering event.
            if uptime < self.last_uptime and self.last_uptime - uptime < 2**31:
                raise ValueError('设备时间倒退/重启，请开始新录制')
            self.stats['duplicates_or_old'] += 1
            return []
        if self.last_uptime is not None and uptime < self.last_uptime:
            if self.last_uptime - uptime > 2**31:
                self.epoch_ms += 2**32
            else:
                raise ValueError('设备时间倒退/重启，请开始新录制')
        end = (uptime + self.epoch_ms) / 1000
        start = end - (count - 1) / 200
        if self.last_end is not None:
            if start <= self.last_end:
                raise ValueError('包内回填时间重叠：实际采样率可能与 200 Hz 请求不一致')
            if step > 1 or start - self.last_end > .015:
                self.segment += 1
                self.stats['gaps'] += 1
        self.stats['missing_packets'] += step - 1
        self.last_seq, self.last_uptime, self.last_end = seq, uptime, end
        self.last_packet = packet
        if self.origin is None:
            self.origin = start
        decoded = decode_imu(packet)
        rows = []
        for (device_s, values), raw in zip(decoded, struct.iter_unpack('<6h', packet[9:])):
            unwrapped = device_s + self.epoch_ms / 1000
            row = dict(t=unwrapped - self.origin, device_s=unwrapped, host_ns=host_ns,
                       packet_seq=seq, segment=self.segment)
            row.update(zip(CHANNELS, values))
            row.update(zip(RAW_CHANNELS, raw))
            self.stats['clipped_samples'] += int(any(abs(v) >= 32760 for v in raw))
            rows.append(row)
        self.stats['packets'] += 1
        self.stats['samples'] += len(rows)
        return rows
