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


TouchpadEvent = TouchpadMove | TouchpadClick

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
    contact_probability: float = 0.
    last_data_age_s: float | None = None
