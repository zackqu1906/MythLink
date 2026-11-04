from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .common import CHANNELS, RAW_CHANNELS, FIELDS, LABELS, TASK, read_json, sha256, write_json

DEFAULT_WINDOW = dict(hz=200, seconds=1., hop_s=.1, max_gap_s=.015, boundary_margin_s=.15)


def load_samples(path):
    a = np.genfromtxt(Path(path) / 'imu.csv', delimiter=',', names=True, dtype=np.float64, ndmin=1)
    if list(a.dtype.names or ()) != FIELDS or len(a) == 0:
        raise ValueError('imu.csv 表头错误或为空')
    matrix = np.column_stack([a[k] for k in FIELDS])
    if not np.isfinite(matrix).all() or np.any(np.diff(a['t']) <= 0):
        raise ValueError('IMU 含无效数值或非递增时间戳')
    raw = np.column_stack([a[k] for k in RAW_CHANNELS])
    if np.any(raw != np.round(raw)) or np.any(np.abs(raw) > 32768) or np.any(raw > 32767):
        raise ValueError('原始数据不是合法 int16')
    scale = np.array([9.80665 / 2048] * 3 + [np.pi / 180 / 16.4] * 3)
    physical = np.column_stack([a[k] for k in CHANNELS])
    if not np.allclose(raw * scale, physical, rtol=1e-7, atol=1e-8):
        raise ValueError('物理单位与原始 LSB 不一致，拒绝训练')
    return a, physical.astype(np.float32), raw


def audit(path):
    path = Path(path)
    meta = read_json(path / 'metadata.json')
    a, x, raw = load_samples(path)
    blockers = []
    if not meta.get('complete') or meta.get('error'):
        blockers.append('录制没有正常完成')
    if meta.get('task') != TASK:
        blockers.append('任务定义不一致')
    for filename, key in [('imu.csv', 'imu_sha256'), ('wire.jsonl', 'wire_sha256')]:
        if meta.get(key) != sha256(path / filename):
            blockers.append(f'{filename} 原始文件哈希不匹配')
    if not meta.get('firmware_version'):
        blockers.append('缺少固件版本')
    if meta.get('imu_start_hex') != '2100c800c800d007100a0000':
        blockers.append('采集命令并非指定 SDK 的 200 Hz / 16 g / 2000 dps raw 模式')
    ends = np.r_[np.flatnonzero(np.diff(a['packet_seq']) != 0), len(a) - 1]
    counts = np.diff(np.r_[-1, ends])
    if np.any(counts != 10):
        blockers.append('CSV 中出现不完整的 10 帧包')
    seq_step = np.diff(a['packet_seq'][ends]) % 65536
    endpoint_dt = np.diff(a['device_s'][ends])
    valid = (seq_step == 1) & (endpoint_dt > 0)
    rate = float(10 / np.median(endpoint_dt[valid])) if np.any(valid) else 0.
    if not 190 <= rate <= 210:
        blockers.append('包末时间推算的采样率不在 190–210 Hz；不能用回填时间证明 ODR')
    duration = float(a['t'][-1] - a['t'][0])
    if duration < 10:
        blockers.append('录制不足 10 秒')
    gaps = (np.diff(a['t']) > DEFAULT_WINDOW['max_gap_s']) | (np.diff(a['segment']) != 0)
    saturated = np.any(np.abs(raw) >= 32760, axis=1)
    return dict(session=path.name, usable=not blockers, blockers=blockers, samples=len(a),
                duration_s=duration, endpoint_rate_hz=rate, gaps=int(gaps.sum()),
                saturated_samples=int(saturated.sum()),
                acceleration_norm_p50=float(np.median(np.linalg.norm(x[:, :3], axis=1))),
                gyro_norm_p50=float(np.median(np.linalg.norm(x[:, 3:], axis=1))),
                synthetic=bool(meta.get('synthetic')), firmware=meta.get('firmware_version'),
                note='饱和或缺口所跨窗口将被排除；静止重力和安装朝向仍需实物检查')


def validate_annotations(path, annotation=None, require_reviewed=True):
    path = Path(path)
    ann = annotation if annotation is not None else read_json(path / 'annotations.json')
    if ann.get('schema') != 1 or ann.get('imu_sha256') != sha256(path / 'imu.csv'):
        raise ValueError('标注版本或原始数据哈希不匹配')
    evidence_types = ['manual-observation', 'video']
    if read_json(path / 'metadata.json').get('synthetic'):
        evidence_types.append('synthetic-ground-truth')
    if require_reviewed and (not ann.get('reviewed') or not ann.get('reviewer', '').strip()
                             or ann.get('evidence') not in evidence_types):
        raise ValueError('必须按实际动作人工复核；提示时间不能直接用作训练真值')
    a, _, _ = load_samples(path)
    previous = float(a['t'][0]) - 1e-6
    trials = {}
    for item in ann.get('intervals', []):
        start, end = float(item['start_s']), float(item['end_s'])
        if not np.isfinite([start, end]).all() or start < previous - 1e-6 or end <= start or end > a['t'][-1] + .01:
            raise ValueError('标注必须按时间排序、不重叠，并落在录制范围内')
        previous = end
        label = item['label']
        if label not in [*LABELS, 'unknown']:
            raise ValueError(f'未知标签：{label}')
        if label != 'unknown' and not str(item.get('activity', '')).strip():
            raise ValueError('每段有效标注都需要具体活动名称')
        if label in LABELS[1:]:
            if not item.get('trial_id'):
                raise ValueError('每个目标动作阶段都需要 trial_id')
            trials.setdefault(item['trial_id'], []).append(item)
    if require_reviewed:
        for trial, phases in trials.items():
            if [p['label'] for p in phases] != LABELS[1:]:
                raise ValueError(f'{trial} 必须依次包含 raising / near_mouth / lowering；未完成动作请标为 unknown')
            if any(abs(a['end_s'] - b['start_s']) > .011 for a, b in zip(phases, phases[1:])):
                raise ValueError(f'{trial} 的动作阶段必须连续')
    return ann


def make_window(times, values, segments, end, config=DEFAULT_WINDOW, saturated=None):
    """Only samples <= end are used, including interpolation support points."""
    frames = round(config['seconds'] * config['hz'])
    if frames < 2:
        raise ValueError('窗口长度过小')
    grid = end - np.arange(frames - 1, -1, -1) / config['hz']
    last = np.searchsorted(times, end + 1e-9, side='right')
    first = max(0, np.searchsorted(times, grid[0], side='right') - 1)
    t = times[first:last]
    if len(t) < 2 or t[0] > grid[0] + 1e-9 or end - t[-1] > 1 / config['hz'] + .001:
        return None
    if np.any(np.diff(t) > config['max_gap_s']) or np.any(segments[first:last] != segments[first]):
        return None
    if saturated is not None and np.any(saturated[first:last]):
        return None
    # Rightmost grid point may hold the latest past sample for at most one frame.
    return np.stack([np.interp(grid, t, values[first:last, c]) for c in range(6)]).astype(np.float32)


def session_windows(path, config=DEFAULT_WINDOW):
    a, values, raw = load_samples(path)
    ann = validate_annotations(path)
    windows, labels, times, clean, activities = [], [], [], [], []
    saturated = np.any(np.abs(raw) >= 32760, axis=1)
    boundaries = np.array([v for item in ann['intervals'] for v in (item['start_s'], item['end_s'])])
    for end in np.arange(a['t'][0] + config['seconds'], a['t'][-1] + 1e-9, config['hop_s']):
        interval = next((i for i in ann['intervals'] if i['start_s'] <= end < i['end_s']), None)
        if interval is None or interval['label'] == 'unknown':
            continue
        x = make_window(a['t'], values, a['segment'], end, config, saturated)
        if x is None:
            continue
        windows.append(x)
        labels.append(LABELS.index(interval['label']))
        times.append(end)
        clean.append(bool(np.min(abs(boundaries - end)) >= config['boundary_margin_s']))
        activities.append(interval['activity'])
    if not windows:
        raise ValueError(f'{Path(path).name} 没有有效且已复核的窗口')
    return dict(x=np.stack(windows), y=np.array(labels), t=np.array(times),
                train_mask=np.array(clean), activity=np.array(activities)), ann


def validate_plan(root, plan):
    mode = plan.get('mode')
    if mode not in ('personal', 'cross_subject'):
        raise ValueError('split mode 必须为 personal 或 cross_subject')
    seen_sessions, seen_groups, seen_people = {}, {}, {}
    for split in ('train', 'val', 'test'):
        if not plan.get(split):
            raise ValueError(f'需要独立的 {split} 数据')
        for session in plan[split]:
            if Path(session).name != session:
                raise ValueError('split 只允许 session 目录名')
            meta = read_json(Path(root) / session / 'metadata.json')
            if session in seen_sessions:
                raise ValueError('同一 session 出现在多个 split 或重复出现')
            seen_sessions[session] = split
            group = meta['group']
            if group != f"{meta['config']['participant']}/{meta['config']['visit']}":
                raise ValueError('批次标识与 participant/visit 不一致')
            if group in seen_groups and seen_groups[group] != split:
                raise ValueError('同一佩戴/采集批次不能跨 split')
            seen_groups[group] = split
            person = meta['config']['participant']
            if mode == 'cross_subject' and person in seen_people and seen_people[person] != split:
                raise ValueError('跨用户评估禁止同一用户跨 split')
            seen_people[person] = split
    if mode == 'personal' and len(seen_people) != 1:
        raise ValueError('personal 只评估同一人的独立批次；多人请用 cross_subject')


def prepare(root, plan_path, output, allow_synthetic=False):
    root, output = Path(root), Path(output)
    plan = read_json(plan_path)
    validate_plan(root, plan)
    if output.exists():
        raise ValueError('输出目录已存在；使用新版本名，避免覆盖冻结数据')
    config = {**DEFAULT_WINDOW, **plan.get('window', {})}
    if config['hz'] != 200 or config['hop_s'] != .1 or not .5 <= config['seconds'] <= 3:
        raise ValueError('首版固定 200 Hz、100 ms 步长，窗口支持 0.5–3 秒')
    if config['max_gap_s'] != .015 or not 0 <= config['boundary_margin_s'] <= .5:
        raise ValueError('缺口阈值固定 15 ms；边界排除范围需在 0–0.5 秒')
    packages, sources, sessions = {}, {}, {}
    synthetic_flags, firmware_versions, label_schemas = set(), set(), set()
    for split in ('train', 'val', 'test'):
        chunks = []
        for session in plan[split]:
            path = root / session
            quality = audit(path)
            if not quality['usable']:
                raise ValueError(f'{session}: ' + '; '.join(quality['blockers']))
            meta = read_json(path / 'metadata.json')
            label_schemas.add(meta.get('gesture_label_schema', 'target_intent_phases_v1'))
            synthetic_flags.add(bool(meta.get('synthetic')))
            firmware_versions.add(meta['firmware_version'])
            if meta.get('synthetic') and not allow_synthetic:
                raise ValueError('合成数据只能通过显式 --allow-synthetic 做流程自检')
            chunk, ann = session_windows(path, config)
            chunk['session'] = np.full(len(chunk['y']), session)
            chunks.append(chunk)
            sources[session] = {f: sha256(path / f) for f in ('metadata.json', 'imu.csv', 'wire.jsonl', 'annotations.json')}
            sessions[session] = dict(split=split, metadata=meta, annotations=ann, audit=quality)
        packages[split] = {key: np.concatenate([c[key] for c in chunks]) for key in chunks[0]}
        if set(packages[split]['y']) != set(range(4)):
            raise ValueError(f'{split} 缺少四阶段中的某些类别；每个 split 都需要目标动作和背景')
        if not any(sessions[s]['metadata']['config']['kind'] == 'negative' for s in plan[split]):
            raise ValueError(f'{split} 必须包含独立的连续负例录制，不能只用动作间休息')
    if len(synthetic_flags) != 1:
        raise ValueError('禁止混合真实和合成数据')
    if len(firmware_versions) != 1:
        raise ValueError('首版禁止混合固件版本；先完成单位、时间和分布一致性验证')
    if len(label_schemas) != 1:
        raise ValueError('不能混合旧版意图相关动作标签与新版独立运动标签；请重新统一复核')
    output.mkdir(parents=True)
    for split, package in packages.items():
        np.savez_compressed(output / (split + '.npz'), **package)
    manifest = dict(schema=1, task=TASK, objective='gesture_only_not_multimodal_intent',
                    gesture_label_schema=next(iter(label_schemas)), labels=LABELS, window=config, plan=plan, sources=sources,
                    sessions=sessions, synthetic=synthetic_flags == {True},
                    files={s + '.npz': sha256(output / (s + '.npz')) for s in packages},
                    counts={s: np.bincount(p['y'], minlength=4).tolist() for s, p in packages.items()})
    write_json(output / 'manifest.json', manifest)
    return dict(output=str(output), counts=manifest['counts'], synthetic=manifest['synthetic'])


def load_dataset(path):
    path = Path(path)
    manifest = read_json(path / 'manifest.json')
    for filename, digest in manifest['files'].items():
        if sha256(path / filename) != digest:
            raise ValueError('冻结数据集哈希不匹配')
    return manifest
