"""Public value types for Ringo MCUboot OTA."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class OtaPhase(StrEnum):
    PREFLIGHT = "preflight"
    CONNECTING = "connecting"
    UPLOADING = "uploading"
    MARKING = "marking"
    REBOOTING = "rebooting"
    VERIFYING = "verifying"
    RECONNECTING = "reconnecting"
    COMPLETE = "complete"


class OtaError(RuntimeError):
    """OTA failure annotated with the phase that failed."""

    def __init__(self, phase: OtaPhase, message: str) -> None:
        self.phase = phase
        super().__init__(f"{phase.value}: {message}")


@dataclass(frozen=True)
class OtaProgress:
    phase: OtaPhase
    bytes_sent: int = 0
    bytes_total: int = 0
    message: str = ""

    @property
    def percent(self) -> float:
        if self.bytes_total <= 0:
            return 0.0
        return min(100.0, max(0.0, self.bytes_sent * 100.0 / self.bytes_total))


@dataclass(frozen=True)
class OtaImageState:
    slot: int
    version: str
    hash_hex: str
    bootable: bool
    pending: bool
    confirmed: bool
    active: bool
    permanent: bool


@dataclass(frozen=True)
class OtaResult:
    previous_version: str
    version: str
    image_hash_hex: str
    image_size: int
    elapsed_s: float
    images: tuple[OtaImageState, ...]
    final_state: OtaImageState


__all__ = [
    "OtaError",
    "OtaImageState",
    "OtaPhase",
    "OtaProgress",
    "OtaResult",
]
