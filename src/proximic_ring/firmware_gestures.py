"""Deliver firmware-confirmed gestures without host classification or weights."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import queue
import threading
import time

# Preserve saved UI action names, including clench and distinct pinch classes.
GESTURE_NAMES = {
    1: "swipe-up", 2: "swipe-down", 3: "swipe-left", 4: "swipe-right",
    5: "tap", 6: "snap", 7: "clench", 8: "index-pinch", 9: "middle-pinch",
    12: "circle-clockwise", 13: "circle-counterclockwise",
}


@dataclass(frozen=True)
class FirmwareGestureEvent:
    class_id: int
    name: str
    confidence: float
    timestamp_ms: int
    sequence: int
    raw: object


class FirmwareGestureWorker:
    """Bounded callback dispatcher. No IMU, inference, thresholds or debounce.

    Callbacks may perform app routing and must not block CoreBluetooth's loop.
    Idle time without gestures is normal; readiness comes from START/connection.
    """
    def __init__(self, *, on_gesture, queue_capacity: int = 64):
        if queue_capacity < 1:
            raise ValueError("queue_capacity must be positive")
        self.on_gesture = on_gesture
        self._queue = queue.Queue(queue_capacity)
        self._closing = threading.Event()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="RingFirmwareGestures", daemon=True)
        self._started = False
        self.error = None
        self.received_events = self.processed_events = self.dropped_events = 0
        self.gesture_counts = Counter()
        self._last_received = None

    def start(self):
        if self._started or self._closing.is_set():
            raise RuntimeError("FirmwareGestureWorker is single-use")
        self._started = True
        self._thread.start()

    def submit(self, result):
        if (getattr(result, "kind", None) != "trigger" or
                getattr(result, "protocol_version", None) != 2 or
                result.class_id not in GESTURE_NAMES):
            return
        # EVENT_V2/legacy logits never drive app actions. Confidence comes
        # directly from the confirmed packet, without host probability synthesis.
        event = FirmwareGestureEvent(result.class_id, GESTURE_NAMES[result.class_id],
            result.confidence, result.center_uptime_ms if result.center_uptime_ms is not None
            else result.uptime_ms, result.seq, result)
        with self._lock:
            if not self._started or self._closing.is_set() or self.error is not None:
                return
            self.received_events += 1
            self._last_received = time.monotonic()
            if self._queue.full():
                try:
                    self._queue.get_nowait()
                    self.dropped_events += 1
                except queue.Empty:
                    pass  # The consumer may have drained it since full().
            self._queue.put_nowait(event)

    def _run(self):
        try:
            while not self._closing.is_set():
                try:
                    event = self._queue.get(timeout=.05)
                except queue.Empty:
                    continue
                if self._closing.is_set():
                    break
                self.on_gesture(event)
                with self._lock:
                    self.processed_events += 1
                    self.gesture_counts[event.name] += 1
        except Exception as exc:
            self.error = exc

    def snapshot(self):
        with self._lock:
            return {
                "source": "firmware", "received_events": self.received_events,
                "processed_events": self.processed_events, "dropped_events": self.dropped_events,
                "gesture_counts": dict(self.gesture_counts), "queue_depth": self._queue.qsize(),
                "last_event_age_ms": ((time.monotonic() - self._last_received) * 1000
                                      if self._last_received is not None else None),
                "error": str(self.error) if self.error else None,
            }

    def close(self, *, timeout_s=5):
        with self._lock:
            self._closing.set()
        if self._started and threading.current_thread() is not self._thread:
            self._thread.join(timeout_s)
            if self._thread.is_alive():
                raise TimeoutError("Firmware gesture callback did not stop")
