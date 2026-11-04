from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABELS = ['background', 'raising', 'near_mouth', 'lowering']
CHANNELS = ['ax_ms2', 'ay_ms2', 'az_ms2', 'gx_rads', 'gy_rads', 'gz_rads']
RAW_CHANNELS = ['raw_ax', 'raw_ay', 'raw_az', 'raw_gx', 'raw_gy', 'raw_gz']
FIELDS = ['t', 'device_s', 'host_ns', 'packet_seq', 'segment', *CHANNELS, *RAW_CHANNELS]
TASK = 'raise_to_mouth_prepare_to_speak_v1'


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value):
        raise ValueError('编号只能包含 1–64 个英文字母、数字、下划线或横线')
    return value


def session_path(root, name):
    return Path(root) / identifier(name)
