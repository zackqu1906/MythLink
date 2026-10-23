"""Descriptive signal checks; never a wearer-VAD classifier.

IMU columns: aligned host seconds, receipt seconds, unwrapped device seconds,
sample index, packet sequence, ax/ay/az (m/s²), gx/gy/gz (deg/s), six raw LSBs.
Audio columns: ADC midpoint seconds, receipt seconds, WAV frame offset, frames,
linear RMS (full scale=1), peak, status flag. Timing is approximate across devices.
"""
from __future__ import annotations

import numpy as np

LABELS = {
    "unlabeled": "未标记", "silence": "静默", "self_speech": "自己说话",
    "vowel": "自己长音啊", "other_speech": "旁人说话", "playback": "音箱播放",
    "movement": "只动手", "both": "同时说话",
}
BIN_S = 0.2


def packet_rate(imu: np.ndarray) -> float | None:
    """Use *last* samples of complete adjacent packets, not SDK-filled spacing.

    The SDK backfills intrapacket timestamps using the requested rate; those
    spacings cannot independently verify firmware ODR. Drop the in-flight packet.
    """
    if len(imu) < 3:
        return None
    ends = np.flatnonzero(np.diff(imu[:, 4]) != 0)
    if len(ends) < 3:
        return None
    a, b = imu[ends[:-1]], imu[ends[1:]]
    dt = b[:, 2] - a[:, 2]
    good = (np.mod(b[:, 4] - a[:, 4], 65536) == 1) & (dt > 0)
    rates = (b[:, 3] - a[:, 3])[good] / dt[good]
    return float(np.median(rates)) if len(rates) else None


def quality(imu: np.ndarray, requested_hz: float) -> dict:
    rate = packet_rate(imu)
    if len(imu) < 2:
        return {"packet_hz": rate, "delivered_hz": None, "gaps": 0, "clipped": 0,
                "timing_ok": False}
    dt = np.diff(imu[:, 2])
    duration = imu[-1, 2] - imu[0, 2]
    return {
        "packet_hz": rate,
        "delivered_hz": float((len(imu) - 1) / duration) if duration > 0 else None,
        "gaps": int(np.count_nonzero((dt > 1.8 / requested_hz) | (dt <= 0))),
        "clipped": int(np.count_nonzero(np.any(np.abs(imu[:, 11:17]) >= 32760, axis=1))),
        "timing_ok": rate is not None and abs(rate / requested_hz - 1) < .05,
    }


def features(imu: np.ndarray, audio: np.ndarray, hz: float) -> np.ndarray:
    """Non-overlapping 200 ms windows, per-axis DC removal then vector RMS.

    Skip missing/short IMU windows; never interpolate across a BLE dropout.
    Audio bins without >=80% time coverage remain NaN (JSON becomes null).
    """
    if len(imu) < 2:
        return np.empty((0, 4))
    ib = np.floor(imu[:, 0] / BIN_S).astype(int)
    ab = np.floor(audio[:, 0] / BIN_S).astype(int) if len(audio) else np.array([])
    result = []
    # Group once: avoids O(number_of_bins * capture_length) in offline reports.
    boundaries = np.r_[0, np.flatnonzero(np.diff(ib)) + 1, len(imu)]
    audio_bins = {}
    if len(audio):
        for lo, hi in zip(np.r_[0, np.flatnonzero(np.diff(ab)) + 1],
                          np.r_[np.flatnonzero(np.diff(ab)) + 1, len(audio)]):
            audio_bins[int(ab[lo])] = audio[lo:hi]
    for lo, hi in zip(boundaries[:-1], boundaries[1:]):
        v = imu[lo:hi]
        if len(v) < .8 * hz * BIN_S or np.any(np.diff(v[:, 2]) > 1.8 / hz):
            continue
        if np.any(np.diff(v[:, 2]) <= 0):
            continue
        centered = v[:, 5:11] - v[:, 5:11].mean(axis=0)
        rms = np.sqrt(np.mean(centered ** 2, axis=0))
        a = audio_bins.get(int(ib[lo]), np.empty((0, 7)))
        # The frame count is independent of the recording rate. Infer block
        # duration from midpoint spacing (20 ms by the capture contract).
        valid_audio = (len(a) >= 8 and not np.any(a[:, 6]) and
                       np.all(np.diff(a[:, 0]) > 0) and np.all(np.diff(a[:, 0]) < .031))
        ar = float(np.sqrt(np.average(a[:, 4] ** 2, weights=a[:, 3]))) if valid_audio else np.nan
        result.append([(ib[lo] + .5) * BIN_S, np.linalg.norm(rms[:3]),
                       np.linalg.norm(rms[3:]), ar])
    return np.asarray(result, dtype=float).reshape(-1, 4)


def correlation(f: np.ndarray, channel: int = 1) -> dict | None:
    """corr(audio(t), IMU(t+lag)); positive lag means IMU follows audio."""
    if len(f) < 20:
        return None
    # Centers are (bin + .5) * BIN_S: round(center/BIN_S) merges adjacent
    # windows through Python's round-to-even, corrupting the lag calculation.
    lookup = {round(row[0] / BIN_S - .5): row for row in f}
    candidates = []
    for shift in range(-5, 6):
        pairs = [(r[3], lookup[k + shift][channel]) for k, r in lookup.items()
                 if k + shift in lookup and np.isfinite(r[3])]
        if len(pairs) < 20:
            continue
        p = np.asarray(pairs)
        if np.std(p[:, 0]) < 1e-9 or np.std(p[:, 1]) < 1e-9:
            continue
        r = float(np.corrcoef(p.T)[0, 1])
        candidates.append({"r": r, "lag_s": shift * BIN_S, "windows": len(pairs)})
    return max(candidates, key=lambda c: abs(c["r"])) if candidates else None


def spectrum(imu: np.ndarray, hz: float) -> dict:
    """Hann-window periodogram of the last contiguous <=4 s; one-sided PSD."""
    if len(imu) < hz * 2:
        return {}
    breaks = np.flatnonzero((np.diff(imu[:, 2]) > 1.8 / hz) | (np.diff(imu[:, 2]) <= 0))
    start = max(int(breaks[-1] + 1) if len(breaks) else 0, len(imu) - int(hz * 4))
    v = imu[start:, 5:11]
    if len(v) < hz * 2:
        return {}
    n = len(v)
    w = np.hanning(n)
    fft = np.fft.rfft((v - v.mean(axis=0)) * w[:, None], axis=0)
    psd = np.abs(fft) ** 2 / (hz * np.sum(w ** 2))
    psd[1:-1 if n % 2 == 0 else None] *= 2
    freq = np.fft.rfftfreq(n, 1 / hz)
    acc, gyro = psd[:, :3].sum(axis=1), psd[:, 3:].sum(axis=1)
    return {"hz": freq.tolist(), "acc_db": (10 * np.log10(acc + 1e-20)).tolist(),
            "gyro_db": (10 * np.log10(gyro + 1e-20)).tolist(), "seconds": n / hz,
            "acc_peak_hz": float(freq[1 + np.argmax(acc[1:])]),
            "gyro_peak_hz": float(freq[1 + np.argmax(gyro[1:])])}


def summarize(imu: np.ndarray, audio: np.ndarray, markers: list[dict], hz: float) -> dict:
    q = quality(imu, hz)
    f = features(imu, audio, hz) if q["timing_ok"] else np.empty((0, 4))
    groups = {}
    segments = []
    end = float(imu[-1, 0]) if len(imu) else 0
    for i, m in enumerate(markers):
        stop = markers[i + 1]["t"] if i + 1 < len(markers) else end
        # Ignore keyboard/mouse motion for a full second on BOTH boundaries.
        part = f[(f[:, 0] - BIN_S / 2 >= m["t"] + 1) &
                 (f[:, 0] + BIN_S / 2 <= stop - 1)]
        segments.append({**m, "end": stop, "usable_windows": len(part)})
        if len(part) and m["label"] != "unlabeled":
            groups.setdefault(m["label"], []).extend(part.tolist())
    stats = []
    for label, values in groups.items():
        v = np.asarray(values)
        ar = v[np.isfinite(v[:, 3]), 3]
        stats.append({"label": label, "seconds": len(v) * BIN_S, "windows": len(v),
                      "acc_rms": float(np.median(v[:, 1])),
                      "gyro_rms": float(np.median(v[:, 2])),
                      "audio_dbfs": float(20 * np.log10(max(np.median(ar), 1e-9))) if len(ar) else None})
    baseline = next((s for s in stats if s["label"] == "silence"), None)
    for s in stats:
        for channel in ("acc", "gyro"):
            denom = baseline[channel + "_rms"] if baseline else 0
            s[channel + "_ratio"] = s[channel + "_rms"] / denom if denom > 1e-12 else None
    return {"quality": q, "conditions": stats, "segments": segments,
            "acc_correlation": correlation(f), "gyro_correlation": correlation(f, 2),
            "interpretation": "描述性探索；相关或能量升高不能证明身体传导或佩戴者识别。重复实验并比较旁人/音箱/动作。"}


def json_safe(value):
    """Strict JSON: missing measurements are null, never zero or NaN."""
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, np.integer):
        return int(value)
    return value
