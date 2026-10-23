"""Latest-choice mailbox for idle-only audio changes on one BLE connection."""
from dataclasses import dataclass, replace
import threading


@dataclass(frozen=True)
class AudioConfiguration:
    audio_source: str = "ring"
    microphone_device: str = ""
    speech_control_mode: str = "gesture"

    def __post_init__(self):
        if self.audio_source not in {"ring", "microphone"}:
            raise ValueError("未知音频来源")
        if self.speech_control_mode not in {"gesture", "proximity"}:
            raise ValueError("未知语音启停方式")


@dataclass(frozen=True)
class AudioChange:
    revision: int
    configuration: AudioConfiguration


class LiveAudioSettings:
    def __init__(self, initial: AudioConfiguration):
        self._lock = threading.Lock()
        self._desired = self._applied = initial
        self._revision = 0
        self._inflight = None
        self.allowed = threading.Event()
        self._closed = False

    @property
    def desired(self):
        with self._lock:
            return self._desired

    @property
    def applied(self):
        with self._lock:
            return self._applied

    @property
    def applying(self):
        with self._lock:
            return self._inflight is not None

    @property
    def pending(self):
        with self._lock:
            return self._desired != self._applied or self._inflight is not None

    def select(self, **changes):
        with self._lock:
            if self._closed:
                return
            candidate = replace(self._desired, **changes)
            if candidate != self._desired:
                self._desired = candidate
                self._revision += 1

    def claim(self):
        with self._lock:
            if (self._closed or not self.allowed.is_set() or self._inflight is not None
                    or self._desired == self._applied):
                return None
            self._inflight = AudioChange(self._revision, self._desired)
            return self._inflight

    def complete(self, request, *, success):
        with self._lock:
            if self._closed or request is not self._inflight:
                return False
            if success:
                self._applied = request.configuration
            elif request.revision == self._revision:
                self._desired = self._applied
            # Keep the input gate closed until the GUI commits settings and
            # dataset metadata. A newer user choice remains queued separately.
            return True

    def acknowledge(self, request):
        with self._lock:
            if request is self._inflight:
                self._inflight = None

    def close(self):
        with self._lock:
            self._closed = True
            self.allowed.clear()

