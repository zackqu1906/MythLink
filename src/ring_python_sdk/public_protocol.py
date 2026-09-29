"""Wire-compatible port of the supplied Android public SDK 20260924.

All wire integers and floats are little endian. Device uptime is not wall time.
No Android runtime, model, NumPy, Opus, or audioop dependency is needed.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import struct
import time

SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
WRITE_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NOTIFY_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
GESTURE_IDS = (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 12, 13)
GESTURE_NAMES = {1: "swipe_up", 2: "swipe_down", 3: "swipe_left",
                 4: "swipe_right", 5: "tap", 6: "snap", 7: "fist",
                 8: "index_pinch", 9: "middle_pinch", 12: "circle_clockwise",
                 13: "circle_counterclockwise"}
BUTTON_NAMES = ("press", "release", "click", "long_press", "double_click")


@dataclass(frozen=True)
class GestureEvent:
    sequence: int
    class_id: int
    center_uptime_ms: int
    confidence: float

    @property
    def name(self) -> str:
        return GESTURE_NAMES.get(self.class_id, f"unknown_{self.class_id}")


@dataclass(frozen=True)
class ButtonEvent:
    sequence: int
    event_code: int
    uptime_ms: int

    @property
    def name(self) -> str:
        return BUTTON_NAMES[self.event_code]


@dataclass(frozen=True)
class BatteryEvent:
    percent: int


@dataclass(frozen=True)
class DeviceInfo:
    hardware_revision: int
    firmware_major: int
    firmware_minor: int
    firmware_patch: int
    firmware_tweak: int
    components: tuple[tuple[int, int, int, int, int], ...]
    version_text: str | None = None

    @property
    def firmware_version(self) -> str:
        base = f"{self.firmware_major}.{self.firmware_minor}.{self.firmware_patch}"
        if self.firmware_tweak:
            base += f".{self.firmware_tweak}"
        return self.version_text or base


def parse_device_info(packet: bytes) -> DeviceInfo | None:
    """Optional INFO diagnostic from the existing Ringo Python SDK, format 1."""
    if len(packet) < 9 or packet[:3] != b"\x2a\x02\x01" or packet[3] not in (1, 2):
        return None
    count = packet[8]
    end = 9 + count * 5
    if len(packet) < end:
        return None
    version = None
    if len(packet) > end:
        size = packet[end]
        if not 1 <= size <= 31 or len(packet) != end + 1 + size:
            return None
        text = packet[end + 1:]
        if any(c < 0x21 or c > 0x7e for c in text):
            return None
        version = text.decode("ascii")
    components = tuple(tuple(packet[i:i + 5]) for i in range(9, end, 5))
    return DeviceInfo(*packet[3:8], components, version)


@dataclass(frozen=True)
class QuaternionFrame:
    qw: float
    qx: float
    qy: float
    qz: float
    sequence: int
    frame_index: int
    frame_count: int
    packet_uptime_ms: int
    uptime_ms: int
    sample_rate_hz: int


@dataclass(frozen=True)
class AudioFrame:
    sequence: int
    uptime_ms: int
    pcm: bytes  # signed PCM16 little endian, mono, 16000 Hz
    sample_rate: int = 16000

    @property
    def samples(self) -> tuple[int, ...]:
        return struct.unpack(f"<{len(self.pcm) // 2}h", self.pcm)

    @property
    def sample_count(self) -> int:
        return len(self.pcm) // 2


def mic_start_command(hardware_gain_db: float | None = None,
                      software_gain_db: float | None = None) -> bytes:
    def gain(value: float | None, minimum: float, maximum: float) -> int:
        if value is None:
            return 0x80
        if not math.isfinite(value) or not minimum <= value <= maximum or value * 2 != int(value * 2):
            raise ValueError(f"Gain must be {minimum}..{maximum} dB in 0.5 dB steps")
        return int(value * 2) & 255
    return bytes((0x20, 0, 1, gain(hardware_gain_db, -20, 20), gain(software_gain_db, -24, 24)))


def quaternion_start_command(sample_rate_hz: int = 200, frames_per_packet: int = 10) -> bytes:
    if sample_rate_hz not in (25, 50, 100, 200):
        raise ValueError("Quaternion rate must be 25, 50, 100 or 200 Hz")
    if not isinstance(frames_per_packet, int) or not 1 <= frames_per_packet <= 20:
        raise ValueError("frames_per_packet must be an integer in 1..20")
    return b"\x21\x00" + struct.pack("<HHHBBB", sample_rate_hz, sample_rate_hz, 2000, 16, frames_per_packet, 3)


def parse_gesture(packet: bytes) -> GestureEvent | None:
    if packet[:2] == b"\x26\x07" and len(packet) == 13:
        seq, class_id, center, confidence = struct.unpack_from("<HBIf", packet, 2)
        if math.isfinite(confidence) and 0 <= confidence <= 1:
            # Match Android: retain unknown compact class IDs for future firmware.
            return GestureEvent(seq, class_id, center, confidence)
    elif packet[:2] == b"\x26\x06" and len(packet) == 65:
        seq, class_id = struct.unpack_from("<HB", packet, 2)
        probs = struct.unpack_from("<12f", packet, 5)
        center, mass = struct.unpack_from("<If", packet, 57)
        if (class_id not in GESTURE_IDS[1:] or
                any(not math.isfinite(p) or not 0 <= p <= 1 for p in probs) or
                abs(sum(probs) - 1) > 1e-4 or not math.isfinite(mass) or mass < 0):
            return None
        return GestureEvent(seq, class_id, center, probs[GESTURE_IDS.index(class_id)])
    # EVENT_V2 26 05 is not a confirmed gesture.
    return None


def parse_button(packet: bytes) -> ButtonEvent | None:
    if len(packet) == 9 and packet[:2] == b"\x27\x02" and packet[4] < 5:
        return ButtonEvent(*struct.unpack_from("<HBI", packet, 2))
    return None


def parse_battery(packet: bytes) -> BatteryEvent | None:
    if len(packet) >= 5 and packet[:2] == b"\x29\x02" and packet[4] <= 100:
        return BatteryEvent(packet[4])
    return None


def parse_quaternions(packet: bytes, sample_rate_hz: int = 200) -> list[QuaternionFrame]:
    if (sample_rate_hz not in (25, 50, 100, 200) or len(packet) < 10 or
            packet[:2] != b"\x21\x0a" or packet[9] != 1):
        return []
    seq, count, uptime = struct.unpack_from("<HBI", packet, 2)
    if not 1 <= count <= 20 or len(packet) != 10 + count * 16:
        return []
    result = []
    for i in range(count):
        q = struct.unpack_from("<4f", packet, 10 + i * 16)
        norm = sum(v * v for v in q)
        if not math.isfinite(norm) or not 0.81 <= norm <= 1.21:
            return []
        frame_time = (uptime - (count - 1 - i) * (1000 // sample_rate_hz)) & 0xffffffff
        result.append(QuaternionFrame(*q, seq, i, count, uptime, frame_time, sample_rate_hz))
    return result


_STEPS = (
    7,8,9,10,11,12,13,14,16,17,19,21,23,25,28,31,34,37,41,45,50,55,60,66,
    73,80,88,97,107,118,130,143,157,173,190,209,230,253,279,307,337,371,408,
    449,494,544,598,658,724,796,876,963,1060,1166,1282,1411,1552,1707,1878,
    2066,2272,2499,2749,3024,3327,3660,4026,4428,4871,5358,5894,6484,7132,
    7845,8630,9493,10442,11487,12635,13899,15289,16818,18500,20350,22385,
    24623,27086,29794,32767)
_INDICES = (-1,-1,-1,-1,2,4,6,8)


def decode_adpcm(block: bytes) -> bytes | None:
    """Decode the firmware's independent IMA block, including initial sample."""
    if len(block) < 6:
        return None
    predictor, index, reserved, count = struct.unpack_from("<hBBH", block)
    if index > 88 or reserved != 0 or not 1 <= count <= 1600 or len(block) != 6 + count // 2:
        return None
    samples = [predictor]
    for i in range(1, count):
        packed = block[6 + (i - 1) // 2]
        code = packed & 15 if i % 2 == 1 else packed >> 4
        step = _STEPS[index]
        delta = step >> 3
        if code & 4:
            delta += step
        if code & 2:
            delta += step >> 1
        if code & 1:
            delta += step >> 2
        predictor = max(-32768, min(32767, predictor + (-delta if code & 8 else delta)))
        index = max(0, min(88, index + _INDICES[code & 7]))
        samples.append(predictor)
    return struct.pack(f"<{count}h", *samples)


class MicBlockAssembler:
    """One bounded block; retire on conflict/timeout/new sequence. No partial PCM."""
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.sequence: int | None = None
        self.uptime = 0
        self.started_at = 0.0
        self.parts: list[bytes | None] = []
        self.byte_count = 0
        self.retired = False

    def _retire(self) -> None:
        self.retired = True
        self.parts = []

    def accept(self, packet: bytes, now_ms: float | None = None) -> AudioFrame | None:
        if len(packet) <= 12 or packet[:2] != b"\x20\x03":
            return None
        seq, fragment, count, uptime = struct.unpack_from("<HHHI", packet, 2)
        if not 1 <= count <= 806 or fragment >= count or len(packet) - 12 > 806:
            return None
        now = time.monotonic() * 1000 if now_ms is None else now_ms
        if seq != self.sequence:
            if self.sequence is not None and not 1 <= ((seq - self.sequence) & 65535) <= 32767:
                return None
            self.sequence, self.uptime, self.started_at = seq, uptime, now
            self.parts, self.byte_count, self.retired = [None] * count, 0, False
        if self.retired:
            return None
        if self.uptime != uptime or len(self.parts) != count or now - self.started_at > 500:
            self._retire()
            return None
        payload = bytes(packet[12:])
        if self.parts[fragment] is not None:
            if self.parts[fragment] != payload:
                self._retire()
            return None
        self.byte_count += len(payload)
        if self.byte_count > 806:
            self._retire()
            return None
        self.parts[fragment] = payload
        if any(part is None for part in self.parts):
            return None
        block = b"".join(self.parts)
        self._retire()
        pcm = decode_adpcm(block)
        return AudioFrame(seq, uptime, pcm) if pcm is not None else None
