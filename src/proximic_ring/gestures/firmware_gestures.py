"""Deliver firmware-confirmed gestures without host classification or weights."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
import queue
import threading
import time

from ring_python_sdk.swipe import DoubleTapDetector, DoublePinchDetector


# A 200 ms device-time pair can arrive slightly later over BLE. Wait 50 ms
# beyond the pairing window before committing a single tap.
SINGLE_TAP_WAIT_S = 0.250
SINGLE_MIDDLE_WAIT_S = 0.350
SINGLE_INDEX_WAIT_S = 0.650  # SDK default pair window 600ms + BLE delivery margin.

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
    """Bounded callback dispatcher with exclusive single/double tap routing.

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
        self._double_tap = DoubleTapDetector()
        self._double_middle = DoublePinchDetector(pinch_name='middle-pinch', max_interval_ms=300)
        self._double_index = DoublePinchDetector(pinch_name='index-pinch', max_interval_ms=600)

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
            self._queue.put_nowait((event, self._last_received))

    def _deliver(self, event):
        self.on_gesture(event)
        with self._lock:
            self.processed_events += 1
            self.gesture_counts[event.name] += 1

    def _run(self):
        pending = None
        detectors = {'tap':self._double_tap, 'middle-pinch':self._double_middle, 'index-pinch':self._double_index}
        waits = {'tap':SINGLE_TAP_WAIT_S, 'middle-pinch':SINGLE_MIDDLE_WAIT_S, 'index-pinch':SINGLE_INDEX_WAIT_S}
        def reset():
            for detector in detectors.values(): detector.reset()
        try:
            while not self._closing.is_set():
                timeout = (max(0.0, pending[1] + waits[pending[0].name] - time.monotonic())
                           if pending is not None else .05)
                try:
                    event, received_at = self._queue.get(timeout=timeout)
                except queue.Empty:
                    if pending is not None and time.monotonic() >= pending[1] + waits[pending[0].name]:
                        self._deliver(pending[0])
                        pending = None
                        reset()
                    continue
                if self._closing.is_set():
                    break
                if pending is not None and received_at > pending[1] + waits[pending[0].name]:
                    self._deliver(pending[0])
                    pending = None
                    reset()
                pairs = [detector.feed(event.raw) for detector in detectors.values()]
                pair = next((item for item in pairs if item is not None), None)
                if pair is not None:
                    pending = None
                    confidence = [value for value in (pair.first.confidence, pair.second.confidence)
                                  if value is not None]
                    self._deliver(replace(event, name=pair.name, raw=pair,
                                          confidence=min(confidence) if confidence else None))
                elif event.name in detectors:
                    detector = detectors[event.name]
                    if pending is not None and detector.pending_first is pending[0].raw:
                        continue  # Repeated/too-fast tap cannot commit a single.
                    if pending is not None:
                        self._deliver(pending[0])
                    pending = (event, received_at) if detector.pending_first is event.raw else None
                else:
                    if pending is not None and detectors[pending[0].name].pending_first is None:
                        self._deliver(pending[0])
                        pending = None
                    self._deliver(event)
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
