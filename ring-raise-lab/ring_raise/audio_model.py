"""User-selected original decoupled CNN, with a new speech/no-speech task.

This is a training building block, not a trained VAD. Never loads the old
near-speech checkpoint. The acquisition path does not gate on this model.
"""
from __future__ import annotations

import numpy as np

from vendor.decoupled_original.features import LegacyFeatureExtractor
from vendor.decoupled_original.resample import LegacyDownsampler16kTo8k
from vendor.decoupled_original.model import CnnNet8
from .common import ROOT, read_json, sha256

SPEECH_LABELS = ('speech', 'non_speech')
AUDIO_WINDOW_S = 1.
AUDIO_HOP_S = .1


def verify_original():
    root = ROOT/'vendor/decoupled_original'
    provenance = read_json(root/'PROVENANCE.json')
    for name, item in provenance['files'].items():
        if sha256(root/name) != item['sha256']:
            raise ValueError(f'decoupled 原始代码/前端系数不匹配：{name}')
    return provenance


def make_speech_model():
    verify_original()
    return CnnNet8(20, 2)  # Fresh weights; original topology unchanged.


class SpeechFrontend:
    def __init__(self):
        verify_original()
        self.downsample = LegacyDownsampler16kTo8k()
        self.features = LegacyFeatureExtractor()

    def extract_pcm16(self, samples):
        x = np.asarray(samples)
        if x.shape != (16000,) or x.dtype.kind != 'i' or np.any(x < -32768) or np.any(x > 32767):
            raise ValueError('需要恰好 1 秒、16 kHz 的有符号 PCM16；不可跨缺口补零')
        waveform = x.astype(np.float32)/np.float32(32768.)
        return self.features.extract(self.downsample(waveform))
