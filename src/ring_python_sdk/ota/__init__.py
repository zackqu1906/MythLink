"""Ringo BLE OTA public API."""

from ring_python_sdk.ota.types import (
    OtaError,
    OtaImageState,
    OtaPhase,
    OtaProgress,
    OtaResult,
)

__all__ = [
    "OtaClient",
    "OtaError",
    "OtaImageState",
    "OtaPhase",
    "OtaProgress",
    "OtaResult",
]


def __getattr__(name):
    if name == "OtaClient":
        from .client import OtaClient
        return OtaClient
    raise AttributeError(name)
