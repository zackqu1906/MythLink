"""Bounded evidence at the encoded-block / decoded-PCM boundary."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import struct
import time
from collections import deque

from ring_python_sdk.public_protocol import AudioFrame


_LOG = logging.getLogger(__name__)


class ADPCMAudioDiagnostics:
    """Report exact digital silence, without treating quiet speech as a fault.

    Log onset after 2 seconds of received PCM, then at most once per 30 seconds
    of wall time. Keep at most one block in memory and include at most three
    anomalous encoded blocks per capture, so a later decoder can replay them.
    Normal audio gets scalar statistics and hashes only. A bounded notification
    window is copied before assembly, so anomaly evidence can audit the SDK's
    framing separately from its decoder. This is the Python BLE receive boundary,
    not an over-the-air Bluetooth trace.
    """

    def __init__(self, capture: str) -> None:
        self.capture = capture
        self.blocks = 0
        self.zero_samples = 0
        self.first_zero_sequence: int | None = None
        self.first_zero_uptime_ms: int | None = None
        self.zero_since = 0.0
        self.last_report = 0.0
        self.reported = False
        self.raw_blocks_left = 3
        self.last: tuple[AudioFrame, bytes] | None = None
        self.previous_sequence: int | None = None
        self.missing_blocks = 0
        self._raw_notifications: deque[bytes] = deque(maxlen=32)

    def record_notification(self, packet: bytes) -> None:
        """Observe untouched input before MicBlockAssembler; never repair it."""
        # A valid public block is at most 806 payload bytes + 12 header bytes.
        if 12 < len(packet) <= 818 and packet[:2] == b"\x20\x03":
            self._raw_notifications.append(bytes(packet))

    def observe(self, frame: AudioFrame, block: bytes) -> None:
        now = time.monotonic()
        self.blocks += 1
        if self.previous_sequence is not None:
            self.missing_blocks += max(0, ((frame.sequence - self.previous_sequence) & 65535) - 1)
        self.previous_sequence = frame.sequence
        self.last = (frame, block)
        zero = not any(frame.pcm)
        if self.blocks == 1:
            self._report("first_block", frame, block, now)
        if zero:
            if not self.zero_samples:
                self.first_zero_sequence = frame.sequence
                self.first_zero_uptime_ms = frame.uptime_ms
                self.zero_since = now
            self.zero_samples += frame.sample_count
            if self.zero_samples >= frame.sample_rate * 2:
                if not self.reported:
                    self._report("zero_pcm_started", frame, block, now)
                    self.reported = True
                    self.last_report = now
                elif now - self.last_report >= 30:
                    self._report("zero_pcm_continues", frame, block, now)
                    self.last_report = now
        else:
            if self.reported:
                self._report("zero_pcm_recovered", frame, block, now)
            self.zero_samples = 0
            self.first_zero_sequence = None
            self.first_zero_uptime_ms = None
            self.reported = False

    def close(self) -> None:
        if self.reported and self.last is not None:
            self._report("zero_pcm_at_close", *self.last, time.monotonic())
        self.last = None
        self.reported = False
        self._raw_notifications.clear()

    def _report(self, event: str, frame: AudioFrame, block: bytes, now: float) -> None:
        samples = frame.samples
        predictor, index, reserved, count = struct.unpack_from("<hBBH", block)
        histogram = [0] * 16
        # The final high nibble may be padding; count only decoded nibbles.
        for i in range(count - 1):
            histogram[(block[6 + i // 2] >> (4 * (i % 2))) & 15] += 1
        zero = not any(frame.pcm)
        row = {
            "event": event, "capture": self.capture, "encoding": "adpcm",
            "sequence": frame.sequence, "uptime_ms": frame.uptime_ms,
            "blocks": self.blocks, "missing_blocks": self.missing_blocks,
            "zero_start_sequence": self.first_zero_sequence,
            "zero_start_uptime_ms": self.first_zero_uptime_ms,
            "zero_audio_s": round(self.zero_samples / frame.sample_rate, 3),
            "zero_wall_s": round(max(0, now - self.zero_since), 3) if self.zero_samples else 0,
            "pcm_samples": len(samples), "pcm_all_zero": zero,
            "pcm_peak": max(map(abs, samples), default=0),
            "pcm_rms": round(math.sqrt(sum(s * s for s in samples) / max(1, len(samples))), 3),
            "encoded_bytes": len(block), "predictor": predictor,
            "step_index": index, "reserved": reserved, "declared_samples": count,
            "nibble_histogram": histogram,
            "encoded_sha256": hashlib.sha256(block).hexdigest(),
            "pcm_sha256": hashlib.sha256(frame.pcm).hexdigest(),
        }
        if zero and event != "first_block" and self.raw_blocks_left:
            row["encoded_hex"] = block.hex()
            # Select by wire metadata, retaining arrival order and duplicate
            # packets. An offline verifier must independently validate fragment
            # count, conflicts and completeness; this trace can be truncated.
            row["raw_notifications_hex"] = [
                packet.hex() for packet in self._raw_notifications
                if int.from_bytes(packet[2:4], "little") == frame.sequence
                and int.from_bytes(packet[8:12], "little") == frame.uptime_ms
            ]
            row["raw_notification_boundary"] = "before_MicBlockAssembler"
            row["raw_notification_window_limit"] = self._raw_notifications.maxlen
            self.raw_blocks_left -= 1
        # Diagnostic output must never interrupt the BLE audio callback.
        try:
            _LOG.info("[RING AUDIO HEALTH] %s", json.dumps(row, separators=(",", ":")))
        except Exception:
            pass
