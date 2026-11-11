"""Immutable SDK events. Timestamps use the host monotonic clock, in seconds."""
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class TouchpadMove:
    dx: float
    dy: float
    contact_probability: float
    step: int
    timestamp: float
    kind: Literal['move'] = 'move'


@dataclass(frozen=True)
class TouchpadClick:
    step: int
    timestamp: float
    button: Literal['left', 'right'] = 'left'
    kind: Literal['click'] = 'click'


@dataclass(frozen=True)
class TouchpadContact:
    state: Literal['down', 'up', 'reset']
    step: int
    timestamp: float
    confirmed_step: int | None = None
    kind: Literal['contact'] = 'contact'


@dataclass(frozen=True)
class TouchpadClickVerdict:
    start_step: int
    step: int
    is_click: bool
    timestamp: float
    kind: Literal['click_verdict'] = 'click_verdict'


TouchpadEvent = TouchpadMove | TouchpadClick | TouchpadContact | TouchpadClickVerdict


@dataclass(frozen=True)
class Raw2PointerMove(TouchpadMove):
    mode: str = 'SINGLE'
    session_id: str = ''
    generation: int = 0
    sequence: int = 0
    episode_id: int | None = None


@dataclass(frozen=True)
class Raw2PointerClick(TouchpadClick):
    count: int = 1
    click_id: int = 0
    session_id: str = ''
    generation: int = 0
    sequence: int = 0
    source_timestamp: float | None = None
    is_preview: bool = False


@dataclass(frozen=True)
class Raw2PointerContact:
    phase: str
    mode: str
    episode_id: int
    step: int
    timestamp: float
    start_timestamp: float
    end_timestamp: float | None = None
    session_id: str = ''
    generation: int = 0
    sequence: int = 0
    kind: Literal['pointer_contact'] = 'pointer_contact'


@dataclass(frozen=True)
class Raw2Reset(TouchpadContact):
    session_id: str = ''
    generation: int = 0


@dataclass(frozen=True)
class TouchpadTokens:
    """Model inputs of one packet: T1–T3 accel, T4–T6 gyro, in token units.

    steps[i] is the frame index used by move/contact events of the same epoch;
    epoch counts processor resets, after which steps restart.
    """
    epoch: int
    steps: tuple[int, ...]
    frame_times: tuple[float, ...]
    values: tuple[tuple[float, ...], ...]
    timestamp: float
    kind: Literal['tokens'] = 'tokens'

@dataclass(frozen=True)
class TouchpadStats:
    packets: int = 0
    tokens: int = 0
    warmup_frames: int = 0
    resets: int = 0
    invalid_packets: int = 0
    duplicate_packets: int = 0
    stale_packets: int = 0
    queue_overflows: int = 0
    sequence_gaps: int = 0
    missing_packets: int = 0
    arrival_gaps: int = 0
    idle_resets: int = 0
    reboots: int = 0
    contact_probability: float = 0.
    last_data_age_s: float | None = None
    inference_batch_ms: float = 0.
    packet_queue_age_ms: float = 0.
    inference_thread_cpu_ms: float = 0.
    inference_batch_packets: int = 0
    ready: bool | None = None
