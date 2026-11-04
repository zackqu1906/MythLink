"""Ringo wire codecs, aligned with app_ble_packet.h (little endian)."""
import struct
from .nus_protocol import SERVICE, RX, TX, START, decode_imu

# Full START explicitly selects raw encoding and LN six-axis mode.
IMU_START = START + b'\x00\x00'
# Omit the codec selector: dev uses ADPCM; Opus builds choose Opus.
MIC_START = b'\x20\x00'

BUTTONS = ('按下', '松开', '单击', '长按', '双击')


def decode_packet(data):
    data = bytes(data)
    if len(data) < 2:
        raise ValueError('truncated header')
    cmd, sub = data[:2]
    event = dict(kind='unknown', cmd=cmd, subcmd=sub)
    def unpack(fmt, size):
        if len(data) != size:
            raise ValueError('invalid packet length')
        return struct.unpack_from(fmt, data, 2)
    if (cmd, sub) == (0x21, 2) or (cmd, sub) == (0x21, 4):
        seq = struct.unpack_from('<H', data, 2)[0]
        event.update(kind='imu', seq=seq, frames=[dict(timestamp=t, values=list(v)) for t, v in decode_imu(data)])
    elif (cmd, sub) == (0x2c, 4):
        if len(data) != 29 or data[2] != 1:
            raise ValueError('invalid runtime status')
        gyro_hz, accel_hz, gyro_fs = struct.unpack_from('<HHH', data, 10)
        event.update(kind='runtime', tx_mode=data[3], active=int.from_bytes(data[4:6], 'little'),
                     gyro_hz=gyro_hz, accel_hz=accel_hz, gyro_fs=gyro_fs,
                     accel_fs=data[16], frames_per_packet=data[17], encode_mode=data[18])
    elif (cmd, sub) == (0x29, 2):
        if len(data) not in (6, 11):
            raise ValueError('invalid battery status')
        event.update(kind='battery', battery_mv=int.from_bytes(data[2:4], 'little'),
                     battery_pct=data[4], charge=data[5] & 0x7f)
    elif (cmd, sub) == (0x27, 2):
        seq, code, uptime = unpack('<HBI', 9)
        event.update(kind='button', seq=seq, event=code, label=BUTTONS[code] if code < 5 else f'未知 {code}', uptime_ms=uptime)
    elif (cmd, sub) == (0x24, 2):
        if len(data) not in (13, 17):
            raise ValueError('invalid PPG vitals length')
        seq, hr, spo2, wear, uptime = struct.unpack_from('<HHBHI', data, 2)
        event.update(kind='ppg', seq=seq, hr=hr, spo2=spo2, wear=wear, uptime_ms=uptime)
        if len(data) == 17:
            rri, stress, count = struct.unpack_from('<HBB', data, 13)
            event.update(rri_ms=rri, stress=stress, rri_count=count)
    elif (cmd, sub) == (0x24, 8):
        if len(data) < 12:
            raise ValueError('truncated PPG raw')
        seq, mode, _, count, mask, uptime = struct.unpack_from('<HBBBBI', data, 2)
        channels = [name for bit, name in enumerate(('green', 'red', 'ir')) if mask & (1 << bit)]
        if not channels or mask & ~7 or len(data) != 12 + count * len(channels) * 4:
            raise ValueError('invalid PPG raw length/mask')
        values = struct.unpack_from('<' + 'i' * (count * len(channels)), data, 12)
        event.update(kind='ppg_raw', seq=seq, mode=mode, uptime_ms=uptime,
                     channels={name: list(values[i::len(channels)]) for i, name in enumerate(channels)})
    elif (cmd, sub) == (0x24, 4):
        if len(data) < 20:
            raise ValueError('truncated wear packet')
        seq, wear, flags, count, _, lp, bl, diff, uptime = struct.unpack_from('<HBBBBhhiI', data, 2)
        if len(data) != 20 + count * 4:
            raise ValueError('invalid wear length')
        event.update(kind='ppg_raw', seq=seq, wear=wear, flags=flags, sar_lp=lp,
                     sar_bl=bl, sar_diff=diff, uptime_ms=uptime,
                     channels={'ir': list(struct.unpack_from('<' + 'i' * count, data, 20))})
    elif cmd == 0x20 and sub in (3, 4):
        if len(data) <= 12:
            raise ValueError('truncated microphone fragment')
        seq, index, count, uptime = struct.unpack_from('<HHHI', data, 2)
        if not 0 <= index < count <= 256:
            raise ValueError('invalid fragment index/count')
        event.update(kind='mic', encoding='adpcm' if sub == 3 else 'opus', seq=seq, frag_idx=index, frag_count=count,
                     uptime_ms=uptime, payload=data[12:].hex())
    return event


def opus_frames(block):
    if len(block) < 3:
        raise ValueError('truncated Opus block')
    samples, count = struct.unpack_from('<HB', block)
    if samples != 1600 or count != 5:
        raise ValueError('invalid Opus block: expected 1600 samples / 5 frames')
    offset, frames = 3, []
    for _ in range(count):
        if offset + 2 > len(block):
            raise ValueError('truncated Opus frame header')
        size = struct.unpack_from('<H', block, offset)[0]
        offset += 2
        if not 1 <= size <= 256 or offset + size > len(block):
            raise ValueError('truncated Opus frame')
        frames.append(block[offset:offset+size])
        offset += size
    if offset != len(block) or not count or not samples:
        raise ValueError('invalid Opus block')
    return samples, frames


def firmware_version(data):
    """INFO version, including optional build flavor, as in ring Python SDK."""
    if len(data) < 9 or data[:2] != b'\x2a\x02' or data[3] not in (1, 2):
        raise ValueError('invalid device INFO')
    offset = 9 + 5 * data[8]
    if len(data) < offset:
        raise ValueError('truncated INFO components')
    version = tuple(data[4:8])
    text = '.'.join(map(str, version if version[3] else version[:3]))
    if len(data) > offset:
        size = data[offset]
        raw = data[offset + 1:]
        if not 1 <= size <= 31 or len(raw) != size or not all(0x21 <= c <= 0x7e for c in raw):
            raise ValueError('invalid INFO version text')
        text = raw.decode('ascii')
    return text


def imu_start_error(status, battery):
    reasons = []
    if battery.get('charge') in (1, 2):
        reasons.append('设备在充电或已充满，请取下充电座后重试')
    flags = status.get('active', 0)
    if flags & (1 << 5):
        reasons.append('HEALTH 正在占用 IMU')
    if flags & (1 << 9):
        reasons.append('IMU 标定正在占用传感器')
    if status:
        reasons.append(f"TX mode={status['tx_mode']}，IMU BLE 流={'开' if flags & 4 else '关'}，"
                       f"gyro/accel={status['gyro_hz']}/{status['accel_hz']} Hz，encode={status['encode_mode']}")
    else:
        reasons.append('未收到运行态应答，请检查固件版本与通知链路')
    return 'Ringo IMU 启动失败：未收到有效六轴数据。' + '；'.join(reasons)
