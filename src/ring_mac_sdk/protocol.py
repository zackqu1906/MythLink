"""Wire contract pinned to Android 579694e: R1 NUS + Raw2 MLP8 v2."""
import math
import struct
from copy import copy
from dataclasses import dataclass

from .errors import ProtocolError
from .types import DeviceInfo, GestureEvent, GestureMode, GestureResponse, InfoComponent, SwipeDirection, WearingHand

SERVICE = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
SMP_SERVICE = "8d53dc1d-1db7-4cd3-868b-8a527460aa84"
REQUEST = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NOTIFY = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
INFO, BATTERY = b"\x2a\x01", b"\x29\x01"
START, STOP, STATUS = b"\x3c\x00\x02\x01\x00\x00", b"\x3c\x01", b"\x3c\x03"
MODEL_ID = 0x77762A23
MODEL_SHA256 = "c16d77015ad37ec09a7b3b9d86055aab910096ce4595142c91238724eed3c8ab"
WIDTHS = (256, 256, 256, 256, 64, 64, 64)
SCALES = (1/32, 1/64, 1/32, 1/32, 1/32, 1/64, 1/64, 1/32)
MASK = 0xFFFFFFFF


def info(data: bytes) -> DeviceInfo:
    if len(data) < 9 or data[:3] != b"\x2a\x02\x01" or data[3] not in (1, 2):
        raise ProtocolError("Not a supported Ringo/R1 INFO identity")
    end = 9 + 5 * data[8]
    if len(data) < end or len({data[i] for i in range(9, end, 5)}) != data[8]:
        raise ProtocolError("Invalid INFO capability table")
    if len(data) == end:
        version = ".".join(str(v) for v in data[4:8])
    else:
        if not 1 <= data[end] <= 31 or len(data) != end + 1 + data[end]:
            raise ProtocolError("Invalid INFO version length")
        if any(not 1 <= v <= 127 for v in data[end+1:]):
            raise ProtocolError("Invalid INFO version encoding")
        version = data[end+1:].decode("ascii")
    components = tuple(InfoComponent(*data[i:i+5]) for i in range(9, end, 5))
    return DeviceInfo(version, data[3], any(c.id == 8 and c.present != 0 for c in components),
                      components, tuple(data[4:8]))


def gesture_reply(data: bytes) -> GestureResponse:
    if len(data) < 2 or data[:2] not in (b"\x26\x08", b"\x38\x80", b"\x39\x80"):
        raise ProtocolError("Invalid gesture ACK")
    command = data[0]
    if len(data) != (5 if command == 0x26 else 6):
        raise ProtocolError("Invalid gesture ACK length")
    operation, result = data[2], struct.unpack_from("<h", data, 3)[0]
    try:
        if command == 0x26:
            value = GestureMode(operation)
        else:
            if operation not in (0, 1):
                raise ValueError()
            value = (WearingHand if command == 0x38 else SwipeDirection)(data[5])
    except ValueError as exc:
        raise ProtocolError("Invalid gesture ACK value") from exc
    return GestureResponse(command, operation, result, value)


def gesture(data: bytes) -> GestureEvent:
    if len(data) != 65 or data[:2] != b"\x26\x06":
        raise ProtocolError("Invalid gesture event length")
    class_id = data[4]
    probabilities = struct.unpack_from("<12f", data, 5)
    mass = struct.unpack_from("<f", data, 61)[0]
    if any(not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities) or not math.isfinite(mass) or mass < 0:
        raise ProtocolError("Invalid gesture probabilities")
    if all(p == 0 for p in probabilities):
        if class_id not in (1, 2, 3, 4, 6, 7, 8, 9, 12, 13, 0x88, 0x89) or mass != 0:
            raise ProtocolError("Invalid final gesture")
        confidence = 0.0
    else:
        ids = (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 12, 13)
        if class_id == 0 or class_id not in ids or abs(sum(probabilities)-1) > .0001:
            raise ProtocolError("Invalid legacy gesture")
        confidence = probabilities[ids.index(class_id)]
    return GestureEvent(struct.unpack_from("<H", data, 2)[0], class_id,
                        struct.unpack_from("<I", data, 57)[0], confidence)


def ringo_gesture(data: bytes) -> GestureEvent:
    """Ringo public 26 07 density trigger; preserve its historical class IDs."""
    if len(data) != 13 or data[:2] != b"\x26\x07":
        raise ProtocolError("Invalid Ringo public gesture length")
    confidence = struct.unpack_from("<f", data, 9)[0]
    if data[4] not in (1, 2, 3, 4, 5, 6, 7, 8, 9, 12, 13) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ProtocolError("Invalid Ringo public gesture")
    return GestureEvent(struct.unpack_from("<H", data, 2)[0], data[4],
                        struct.unpack_from("<I", data, 5)[0], confidence)


def battery(data: bytes) -> int:
    if len(data) < 5 or data[:2] != b"\x29\x02" or data[4] > 100:
        raise ProtocolError("Invalid battery response")
    return data[4]


@dataclass(frozen=True)
class Token:
    session: int
    epoch: int
    sequence: int
    uptime: int
    reset: bool
    features: tuple[float, ...]


def token(data: bytes) -> Token:
    if len(data) != 28 or data[:3] != b"\x3c\x02\x02" or data[3] & ~1:
        raise ProtocolError("Invalid Raw2 token")
    values = struct.unpack_from("8b", data, 20)
    if -128 in values:
        raise ProtocolError("Reserved Raw2 feature -128")
    return Token(*struct.unpack_from("<4I", data, 4), bool(data[3]),
                 tuple(v*s for v, s in zip(values, SCALES)))


def tokens(data: bytes) -> list[Token]:
    if data[:2] == b"\x3c\x02":
        return [token(data)]
    if len(data) < 4 or data[:3] != b"\x3c\x13\x02" or not 2 <= data[3] <= 15 or len(data) != 4+28*data[3]:
        raise ProtocolError("Invalid Raw2 batch")
    batch = [token(data[i:i+28]) for i in range(4, len(data), 28)]
    if any(t.session != batch[0].session for t in batch):
        raise ProtocolError("Batch crosses sessions")
    return batch


@dataclass(frozen=True)
class StreamStatus:
    state: int
    result: int
    session: int
    epoch: int
    frames: int
    tokens: int
    sent: int
    dropped: int
    resets: int
    max_us: int
    last_us: int
    total_us: int
    deadline: int

    @property
    def stopped(self):
        return self.result == 0 and self.state in (0, 2)


def status(data: bytes) -> StreamStatus:
    if len(data) != 64 or data[:3] != b"\x3c\x11\x02" or data[3] > 2:
        raise ProtocolError("Invalid Raw2 status")
    if struct.unpack_from("<I", data, 44)[0] != MODEL_ID:
        raise ProtocolError("Frontend model ID must be 0x77762a23")
    if struct.unpack_from("<HH4B", data, 48) != (200, 100, 8, 255, 1, 1):
        raise ProtocolError("Incompatible Raw2 rates/quantization/coordinates")
    return StreamStatus(data[3], struct.unpack_from("<i", data, 4)[0],
                        *struct.unpack_from("<9I", data, 8), *struct.unpack_from("<2I", data, 56))


class Tracker:
    def __init__(self, state: StreamStatus):
        self.session, self.epoch = state.session, state.epoch
        self.sequence = self.uptime = 0
        self.first = True

    def accept(self, t: Token) -> bool:
        if t.session != self.session:
            raise ProtocolError("Session changed; STOP/START required")
        if self.first:
            if t.sequence != 0 or t.epoch != self.epoch or not t.reset:
                raise ProtocolError("First token must reset at sequence zero")
        else:
            if t.sequence != (self.sequence+1) & MASK:
                raise ProtocolError("Token sequence gap; STOP/START required")
            if t.epoch != self.epoch and (not t.reset or t.epoch != (self.epoch+1) & MASK):
                raise ProtocolError("Invalid epoch transition")
            if t.epoch == self.epoch and t.reset:
                raise ProtocolError("Repeated reset")
            if not 0 < ((t.uptime-self.uptime) & MASK) <= 3000:
                raise ProtocolError("Invalid device time progression")
        reset = self.first or t.epoch != self.epoch
        self.first = False
        self.epoch, self.sequence, self.uptime = t.epoch, t.sequence, t.uptime
        return reset

    def accept_all(self, batch: list[Token]) -> list[bool]:
        if not batch:
            raise ProtocolError("Empty token batch")
        trial = copy(self)
        resets = [trial.accept(t) for t in batch]
        self.__dict__.update(trial.__dict__)
        return resets
