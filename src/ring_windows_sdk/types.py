from dataclasses import dataclass
from enum import IntEnum


class GestureMode(IntEnum):
    NORMAL = 0
    OFF = 1
    LOW_POWER = 7


class WearingHand(IntEnum):
    RIGHT = 0
    LEFT = 1


class SwipeDirection(IntEnum):
    FORWARD = 0
    REVERSED = 1


@dataclass(frozen=True)
class InfoComponent:
    id: int
    present: int
    count: int
    model: int
    flags: int


@dataclass(frozen=True)
class DeviceInfo:
    firmware_version: str
    hardware_revision: int
    swipe_available: bool
    components: tuple[InfoComponent, ...] = ()
    firmware_numeric_version: tuple[int, ...] = ()

    @property
    def product(self) -> str:
        return "Ringo" if self.hardware_revision == 1 else "R1"

    @property
    def gesture_control(self) -> str:
        if not self.swipe_available:
            return "unsupported"
        return "write_only" if self.hardware_revision == 1 else "acknowledged"

    @property
    def gesture_settings_available(self) -> bool:
        return self.hardware_revision == 2 and self.swipe_available


@dataclass(frozen=True)
class GestureResponse:
    command: int
    operation: int
    status: int | None
    value: GestureMode | WearingHand | SwipeDirection
    acknowledged: bool = True


@dataclass(frozen=True)
class GestureEvent:
    sequence: int
    class_id: int
    center_uptime_ms: int
    confidence: float


@dataclass(frozen=True)
class EventMeta:
    session_id: str
    generation: int
    sequence: int
    source_time_ns: int


@dataclass(frozen=True)
class ContactRange:
    start_frame: int
    start_time_ns: int
    end_frame_exclusive: int | None = None
    end_time_ns: int | None = None


@dataclass(frozen=True)
class Motion:
    meta: EventMeta
    frame_id: int
    dx: float
    dy: float
    mode: str
    episode_id: int | None


@dataclass(frozen=True)
class ContactUpdate:
    meta: EventMeta
    episode_id: int
    phase: str
    mode: str
    range: ContactRange


@dataclass(frozen=True)
class Click:
    meta: EventMeta
    click_id: int
    button: str
    count: int
    is_preview: bool


TouchpadEvent = Motion | ContactUpdate | Click


@dataclass(frozen=True)
class TouchpadStatus:
    stage: str = "STOPPED"
    message: str = ""
    session_id: str | None = None
    generation: int = 0
    backend: str = "CPUExecutionProvider"
