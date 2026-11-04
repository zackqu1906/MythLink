"""Shared NUS IMU codec; SI units, wire axes, device uptime seconds."""
import math
import struct

SERVICE = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
# Explicitly configure the scaling used by decode_imu: 200 Hz, 2000 dps, 16 g.
START = b"\x21\x00" + struct.pack("<HHHBB", 200, 200, 2000, 16, 10)


def decode_imu(data):
    if len(data) < 9 or data[0] != 0x21 or data[1] not in (2, 4):
        return ()
    _, count, uptime = struct.unpack_from("<HBI", data, 2)
    payload = data[9:]
    if not count:
        raise ValueError("empty IMU packet")
    if data[1] == 2:
        if len(payload) != count * 12:
            raise ValueError("invalid raw IMU length")
        frames = struct.iter_unpack("<6h", payload)
    else:
        if len(payload) < 15:
            raise ValueError("invalid delta IMU header")
        widths = [((payload[12+i//2] >> (i%2*4)) & 15)+1 for i in range(6)]
        bits = (count-1)*sum(widths)
        if len(payload) != 15+(bits+7)//8:
            raise ValueError("invalid delta IMU length")
        frames = [struct.unpack_from("<6h", payload)]
        packed = int.from_bytes(payload[15:], "little")
        offset = 0
        for _ in range(count-1):
            frame = []
            for axis, width in enumerate(widths):
                value = (packed >> offset) & ((1 << width)-1)
                offset += width
                if value & (1 << (width-1)):
                    value -= 1 << width
                frame.append((frames[-1][axis]+value+32768)%65536-32768)
            frames.append(tuple(frame))
    scale = (9.80665/2048,)*3 + (math.pi/180/16.4,)*3
    return tuple((uptime/1000-(count-1-i)/200,
                  tuple(v*s for v, s in zip(frame, scale))) for i, frame in enumerate(frames))
