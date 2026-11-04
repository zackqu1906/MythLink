"""Independent speech/intent labels; no inferred ground truth from prompts or RMS."""
from __future__ import annotations

import json
import math
from pathlib import Path
import wave

from .common import read_json, sha256

SPEECH = ('wearer_speech', 'other_speech', 'mixed_speech', 'non_speech', 'unknown')
INTENT = ('directed', 'not_directed', 'unknown')


def audio_audit(path):
    path = Path(path)
    meta = read_json(path/'metadata.json')
    if not meta.get('audio_files'):
        return dict(available=False, usable=False, blockers=['未采集音频'])
    blockers = []
    for name, digest in meta['audio_files'].items():
        if sha256(path/name) != digest:
            blockers.append(f'{name} 哈希不匹配')
    with wave.open(str(path/'audio.wav')) as w:
        count, rate = w.getnframes(), w.getframerate()
        if (w.getnchannels(), w.getsampwidth(), rate) != (1, 2, 16000):
            blockers.append('音频格式不是 16 kHz / mono / int16')
    blocks = [json.loads(line) for line in (path/'audio_blocks.jsonl').read_text().splitlines()]
    position = 0
    for block in blocks:
        if block['wav_start_sample'] != position or block['sample_count'] <= 0:
            blockers.append('音频块索引不连续')
        position += block['sample_count']
    if count != position or count != meta['audio']['samples']:
        blockers.append('音频索引、WAV 和元数据长度不同')
    if not count:
        blockers.append('没有音频样本')
    if not meta.get('complete'):
        blockers.append('录制未正常完成')
    warnings = []
    stats = meta['audio']
    if stats.get('max_zero_run_s', 0) >= 2:
        warnings.append('存在连续全零音频，需试听确认传感器有效，不能自动标成静默')
    if any(stats.get(k, 0) for k in ('missing_blocks','incomplete_blocks','timing_gaps')):
        warnings.append('存在缺块/时间缺口；WAV 未补零，融合时必须按块索引排除缺口')
    return dict(available=True, usable=not blockers, blockers=blockers, warnings=warnings,
                duration_s=count/rate, blocks=len(blocks), stats=stats)


def validate_multimodal(path, annotation):
    path = Path(path)
    meta = read_json(path/'metadata.json')
    quality = audio_audit(path)
    if not quality['usable']:
        raise ValueError('音频不能复核：'+'；'.join(quality['blockers']))
    if meta.get('synthetic'):
        raise ValueError('合成数据不能保存为真实观察标注')
    ann = dict(annotation)
    if ann.get('schema') != 1 or ann.get('audio_sha256') != sha256(path/'audio.wav'):
        raise ValueError('音频标注与原始文件不一致')
    if ann.get('reviewed') is not True or not str(ann.get('reviewer','')).strip():
        raise ValueError('需要试听/观察并填写复核者')
    if ann.get('evidence') not in ('listened+observed', 'video+listened'):
        raise ValueError('需要实际试听及动作/意图观察依据')
    if ann.get('speech_timebase') != 'WAV seconds (gaps not padded)' or ann.get('intent_timebase') != 'IMU relative seconds':
        raise ValueError('标注时间基准被修改')
    for key, labels, limit in [('speech_intervals', SPEECH, quality['duration_s']),
                                ('intent_intervals', INTENT, meta['duration_s'])]:
        intervals = ann.get(key)
        if not isinstance(intervals, list) or not intervals:
            raise ValueError(f'{key} 至少添加一段；未知部分显式标 unknown')
        previous = 0.
        for item in intervals:
            a, b = float(item['start_s']), float(item['end_s'])
            if not math.isfinite(a+b) or a < previous or not a < b <= limit+.001 or item['label'] not in labels:
                raise ValueError(f'{key} 越界、重叠或类别无效')
            item['start_s'], item['end_s'] = a, b
            previous = b
    align = ann.get('alignment', {})
    if align.get('reviewed'):
        if align.get('reference') not in ('start','end') or not str(align.get('notes','')).strip():
            raise ValueError('对齐复核需要确认音频时间戳含义，并记录测量/固件依据')
        offset = float(align.get('offset_s', float('nan')))
        if not math.isfinite(offset) or abs(offset) > 10:
            raise ValueError('对齐偏移必须为 -10～10 秒的有限数')
    # Bind labels to every relevant timing source, not only WAV bytes.
    ann['source_hashes'] = {n:sha256(path/n) for n in ('imu.csv','audio.wav','audio_blocks.jsonl','metadata.json')}
    return ann
