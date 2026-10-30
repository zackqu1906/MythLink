"""Local, microphone-only speech candidates on the recorded device timeline.

Never infer the speaker's identity or use IMU activity as a speech label.
The VAD is optional: absence is reported instead of silently calling energy VAD.
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np


def group_speech_frames(frames, min_voiced=.18, join_gap=.20):
    """Group (start, end, voiced) frames, keeping missing data as hard breaks."""
    segments, current = [], None
    previous_end = None

    def finish():
        nonlocal current
        if current and current['voiced_seconds'] + 1e-9 >= min_voiced:
            segments.append(current)
        current = None

    for start, end, voiced in frames:
        if previous_end is not None and abs(start - previous_end) > .003:
            finish()
        if current and start - current['end'] > join_gap + 1e-9:
            finish()
        if voiced:
            if current is None:
                current = {'start': start, 'end': end, 'voiced_seconds': end - start}
            else:
                current['end'] = end
                current['voiced_seconds'] += end - start
        previous_end = end
    finish()
    return segments


def detect_speech(path: Path, audio: np.ndarray, source='ring'):
    result = {'method': 'webrtcvad', 'version': 1, 'status': 'unavailable',
              'source': source, 'segments': [], 'frame_ms': 20, 'mode': 2,
              'min_voiced_s': .18, 'join_gap_s': .20,
              'note': '只根据录音检测疑似语音，不判断说话人；动作声也可能误检。'}
    if source not in ('ring', 'synthetic'):
        result['reason'] = '这条记录没有戒指录音，未自动标注语音。'
        return result
    if not len(audio):
        result['reason'] = '这条记录没有有效录音。'
        return result
    try:
        import webrtcvad
    except ImportError:
        result['reason'] = '未安装本地语音检测依赖 webrtcvad-wheels；录音和手动回看仍可使用。'
        return result
    try:
        with wave.open(str(path / 'audio.wav'), 'rb') as wav:
            rate = wav.getframerate()
            if rate not in (8000, 16000, 32000, 48000) or wav.getnchannels() != 1 or wav.getsampwidth() != 2:
                raise ValueError('语音检测需要单声道 PCM16 录音。')
            pcm = wav.readframes(wav.getnframes())
    except (OSError, ValueError, wave.Error) as exc:
        result['reason'] = f'无法检测这条录音：{exc}'
        return result

    # Ignore digital zero/invalid blocks when estimating a conservative noise gate.
    valid = audio[(audio[:, 6] == 0) & np.isfinite(audio[:, 4]) & (audio[:, 4] > 0)]
    noise = float(np.percentile(20 * np.log10(valid[:, 4]), 20)) if len(valid) else -90.
    gate = min(-32., max(-65., noise + 6.))
    vad = webrtcvad.Vad(2)
    frames, previous_end = [], None
    frame_samples = rate // 50
    skipped = 0
    for row in audio:
        center, _, wav_frame, samples, rms, _, flag = row
        count, offset = int(samples), int(wav_frame)
        start = float(center - count / rate / 2)
        # Reset VAD adaptation at a gap; never bridge a missing/invalid block.
        if previous_end is not None and abs(start - previous_end) > .003:
            vad = webrtcvad.Vad(2)
        previous_end = start + count / rate
        if flag or count != frame_samples or offset < 0 or 2 * (offset + count) > len(pcm) or not np.isfinite(rms):
            skipped += 1
            vad = webrtcvad.Vad(2)
            continue
        voiced = vad.is_speech(pcm[offset * 2:(offset + count) * 2], rate)
        voiced = voiced and rms > 0 and 20 * np.log10(rms) >= gate
        frames.append((start, previous_end, bool(voiced)))
    result.update(status='ready', segments=group_speech_frames(frames),
                  noise_dbfs=noise, gate_dbfs=gate, analyzed_frames=len(frames), skipped_frames=skipped)
    return result
