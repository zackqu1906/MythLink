from __future__ import annotations

import numpy as np


def decode_pcm16le(data: bytes) -> np.ndarray:
    """Decode signed 16-bit little-endian PCM to mono float32 samples in [-1, 1)."""
    if len(data) % 2:
        raise ValueError("PCM16 byte payload must have an even number of bytes")
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / np.float32(32768.0)


def encode_pcm16le(audio_16k: np.ndarray) -> bytes:
    x = np.asarray(audio_16k, dtype=np.float32).reshape(-1)
    if not x.size:
        return b""
    if not np.all(np.isfinite(x)):
        raise ValueError("ASR audio contains NaN or infinity")
    # Match VoiceHistoryStore's PCM16 quantization exactly so a saved WAV is a
    # byte-for-byte record of the samples submitted to the cloud ASR.
    return np.rint(np.clip(x, -1.0, 1.0) * 32767.0).astype(
        "<i2", copy=False
    ).tobytes()
