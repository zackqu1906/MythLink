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
    button: Literal['left'] = 'left'
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
