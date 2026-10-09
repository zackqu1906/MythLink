"""Compose two confirmed firmware pinch actions into one double pinch."""

from __future__ import annotations

from dataclasses import dataclass
import math

from ring_python_sdk.swipe.events import SwipeResult


@dataclass(frozen=True)
class DoublePinchEvent:
    first: SwipeResult
    second: SwipeResult
    interval_ms: int

    @property
    def name(self) -> str:
        return "double-" + self.second.name.removeprefix("swipe-")


class DoublePinchDetector:
    """Feed ALL SwipeResult triggers from one ring, serially.

    Only two consecutive actions of the selected kind can form a pair. EVENT
    classifications are ignored. V2 uses peak/center timestamps; V1 uses uptime.
    Bounds are inclusive. Too-fast repeats do not replace the first pinch;
    a late pinch starts a new pair. Pairs never overlap (three pinches => one).

    Duplicate/older sequence numbers are ignored, and a missing trigger,
    protocol/clock change or another gesture breaks the pending pair. Call
    reset() on disconnect or when starting a new capture. This composes existing
    detections; it cannot recover actions missed by the firmware model.
    """

    def __init__(self, *, pinch_name: str = "index-pinch",
                 min_interval_ms: float = 120, max_interval_ms: float = 600):
        if pinch_name not in ("index-pinch", "middle-pinch", "tap"):
            raise ValueError("pinch_name must be index-pinch, middle-pinch or tap")
        if not (math.isfinite(min_interval_ms) and math.isfinite(max_interval_ms)
                and 0 < min_interval_ms <= max_interval_ms < 0x80000000):
            raise ValueError("intervals must be finite and 0 < min <= max < 2**31 ms")
        self.pinch_name = pinch_name
        self.min_interval_ms = min_interval_ms
        self.max_interval_ms = max_interval_ms
        self.reset()

    def reset(self) -> None:
        self._first: SwipeResult | None = None
        self._last: SwipeResult | None = None

    @staticmethod
    def _time(event: SwipeResult) -> int:
        return (event.center_uptime_ms if event.center_uptime_ms is not None
                else event.uptime_ms)

    def feed(self, event: SwipeResult) -> DoublePinchEvent | None:
        if event.kind != "trigger" or event.class_id == 0:
            return None
        if self._last is not None:
            last = self._last
            if event.protocol_version != last.protocol_version:
                self.reset()
            else:
                seq_delta = (event.seq - last.seq) & 0xFFFF
                if seq_delta == 0 or seq_delta >= 0x8000:
                    return None
                clock_delta = (self._time(event) - self._time(last)) & 0xFFFFFFFF
                clock_changed = ((event.center_uptime_ms is None)
                                 != (last.center_uptime_ms is None))
                if seq_delta != 1 or clock_delta >= 0x80000000 or clock_changed:
                    self._first = None
        self._last = event
        if event.name.removeprefix("swipe-") != self.pinch_name:
            self._first = None
            return None
        if self._first is not None:
            interval = (self._time(event) - self._time(self._first)) & 0xFFFFFFFF
            if interval < self.min_interval_ms:
                return None
            if interval <= self.max_interval_ms:
                result = DoublePinchEvent(self._first, event, interval)
                self._first = None
                return result
        self._first = event
        return None
