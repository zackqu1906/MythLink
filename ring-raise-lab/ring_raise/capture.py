from __future__ import annotations

import asyncio
from collections import deque
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import threading
import time
import uuid

from .audio import AudioCapture
from .common import CHANNELS, FIELDS, TASK, identifier, sha256, write_json
from .protocol import RawDecoder, RX, TX, IMU_START, firmware_version, verify_vendor

SCENARIOS = {
    'sanity': ('sanity', '检查静止、旋转与真实声音；先静置，再说一句话'),
    'raise_speak': ('target', '自然抬到嘴边，对戒指说一句不同的指令，再放下'),
    'raise_silent': ('target', '做相同抬手动作，到嘴边保持，全程不说话'),
    'raise_conversation': ('target', '抬到嘴边，但对别人说话，不向戒指发指令'),
    'speak_no_raise': ('negative', '保持手在桌面或自然下垂，对别人说话，不抬向嘴边'),
    'background_speech': ('negative', '自己不说话；旁人说话或播放语音，记录声源'),
    'daily': ('negative', '自然完成所选日常活动，不向戒指发指令'),
}


def make_schedule(kind, trials=5, seconds=120, seed=42, activity='target'):
    intervals = [dict(start_s=0., end_s=5., label='unknown', activity='settle', trial_id='')]
    rng = random.Random(seed)
    t = 5.
    if kind == 'target':
        for trial in range(1, trials + 1):
            for label, duration in [('background', rng.uniform(3, 5)), ('raising', rng.uniform(1.8, 2.6)),
                                    ('near_mouth', rng.uniform(3, 5)), ('lowering', 2.)]:
                end = t + duration
                intervals.append(dict(start_s=t, end_s=end, label=label,
                                      activity=activity if label != 'background' else 'rest',
                                      trial_id=f't{trial}' if label != 'background' else ''))
                t = end
        intervals.append(dict(start_s=t, end_s=t + 4, label='background', activity='rest', trial_id=''))
    else:
        intervals.append(dict(start_s=t, end_s=t + seconds,
                              label='background' if kind == 'negative' else 'unknown',
                              activity=activity, trial_id=''))
    return intervals


async def scan():
    from bleak import BleakScanner
    devices = await BleakScanner.discover(timeout=5)
    return [dict(name=d.name or '', address=d.address) for d in devices if 'ring' in (d.name or '').lower()]


class Capture:
    """One persistent BLE event loop. Connect/disconnect are independent of recording."""
    stream_timeout_s = 5.
    def __init__(self, root, predictor=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.thread = self.loop = self.client = self.record_future = None
        self.receiver = None
        self.disconnect_event = threading.Event()
        self.stop_event = threading.Event()
        self.recent = deque(maxlen=1000)
        self.status = dict(state='idle', connection='disconnected', error='', connection_error='',
                           samples=0, t=0, session='', phase='', device='', firmware='')
        self.predictor = predictor
        self.device_info = {}

    def update(self, **fields):
        with self.lock:
            self.status.update(fields)

    def snapshot(self):
        with self.lock:
            return {**self.status, 'plot': list(self.recent)[::4],
                    'application': 'ring-raise-lab-v2', 'data_root': str(self.root.resolve())}

    def is_recording(self):
        return self.snapshot()['state'] in ('arming', 'recording', 'stopping')

    def connect(self, device):
        if not isinstance(device, str) or not device.strip():
            raise ValueError('请扫描并选择戒指')
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise ValueError('已有连接或正在连接，请先断开')
            self.disconnect_event.clear()
            self.status.update(connection='connecting', connection_error='', device=device, firmware='')
            self.thread = threading.Thread(target=self._run_connection, args=(device.strip(),), daemon=True)
            self.thread.start()

    def disconnect(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                self.status['connection'] = 'disconnecting'
                self.disconnect_event.set()
                self.stop_event.set()

    def stop(self):
        with self.lock:
            if self.is_recording():
                self.status['state'] = 'stopping'
                self.stop_event.set()

    def close(self):
        self.disconnect()
        if self.thread:
            self.thread.join(timeout=25)

    def _run_connection(self, device):
        try:
            asyncio.run(self._connection(device))
        except Exception as exc:
            self.update(connection='disconnected', connection_error=str(exc))

    async def _connection(self, name):
        from bleak import BleakClient, BleakScanner
        self.loop = asyncio.get_running_loop()
        info = asyncio.Event()
        self.client = None
        error = ''
        def receive(_, raw):
            if raw[:2] == b'\x2a\x02':
                try:
                    self.device_info.update(firmware_version=firmware_version(raw), device_info_hex=bytes(raw).hex())
                    info.set()
                except ValueError as exc:
                    self.update(connection_error=str(exc))
            if self.receiver:
                self.receiver(bytes(raw))
        try:
            devices = await BleakScanner.discover(timeout=5)
            matches = [d for d in devices if name.lower() in (d.address.lower(), (d.name or '').lower())]
            if len(matches) != 1:
                raise ValueError('设备必须精确匹配且唯一；请关闭占用戒指的其他程序')
            if self.disconnect_event.is_set():
                return
            d = matches[0]
            self.device_info = dict(device_name=d.name, device_address=d.address)
            self.client = BleakClient(d)
            await asyncio.wait_for(self.client.connect(), 20)
            await self.client.start_notify(TX, receive)
            await self.client.write_gatt_char(RX, b'\x2a\x01', response=False)
            await asyncio.wait_for(info.wait(), 4)
            for command in (b'\x20\x01', b'\x21\x01'):
                await self.client.write_gatt_char(RX, command, response=False)
            if not self.disconnect_event.is_set():
                self.update(connection='connected', device=d.address, device_name=d.name,
                            firmware=self.device_info['firmware_version'])
            while not self.disconnect_event.is_set():
                if not self.client.is_connected:
                    raise ConnectionError('戒指连接已丢失；请重新连接')
                await asyncio.sleep(.05)
        except Exception as exc:
            error = str(exc)
        finally:
            self.disconnect_event.set()
            self.stop_event.set()
            future = self.record_future
            if future and not future.done():
                try:
                    await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(future)), 15)
                except Exception as exc:
                    error = error or str(exc)
            if self.client and self.client.is_connected:
                for command in (b'\x20\x01', b'\x21\x01'):
                    try:
                        await asyncio.wait_for(self.client.write_gatt_char(RX, command, response=False), 2)
                    except Exception as exc:
                        error = error or str(exc)
                try:
                    await asyncio.wait_for(self.client.stop_notify(TX), 2)
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(self.client.disconnect(), 5)
                except Exception as exc:
                    error = error or str(exc)
            self.receiver = None
            self.update(connection='disconnected', connection_error=error)

    def start(self, config):
        with self.lock:
            if self.status['connection'] != 'connected' or not self.client or not self.client.is_connected:
                raise ValueError('请先点击“连接戒指”，等待连接成功')
            if self.is_recording() or (self.record_future and not self.record_future.done()):
                raise ValueError('已有采集正在运行或保存')
            cfg = dict(config)
            for field in ('participant', 'visit'):
                cfg[field] = identifier(cfg.get(field, ''))
            cfg['scenario'] = cfg.get('scenario', {'target':'raise_speak', 'negative':'daily', 'sanity':'sanity'}.get(cfg.get('kind'), 'sanity'))
            if cfg['scenario'] not in SCENARIOS:
                raise ValueError('未知场景')
            cfg['kind'] = SCENARIOS[cfg['scenario']][0]
            cfg['streams'] = cfg.get('streams', 'imu_audio')
            if cfg['streams'] not in ('imu', 'audio', 'imu_audio'):
                raise ValueError('未知数据模式')
            if cfg['scenario'] != 'sanity' and cfg['streams'] != 'imu_audio':
                raise ValueError('正式场景必须同时采集 IMU 与音频；单路模式仅用于信号检查')
            if self.predictor and cfg['streams'] != 'imu':
                raise ValueError('现有在线模型是 IMU 基线，请使用 IMU 检查模式')
            if cfg.get('hand') not in ('left', 'right') or not cfg.get('finger'):
                raise ValueError('必须记录佩戴手与手指')
            if not str(cfg.get('mount_note', '')).strip() or not cfg.get('posture'):
                raise ValueError('必须记录佩戴朝向和身体姿势')
            if cfg['kind'] == 'negative' and not str(cfg.get('activity', '')).strip():
                raise ValueError('负例必须记录具体活动')
            if cfg['streams'] != 'imu' and not str(cfg.get('environment', '')).strip():
                raise ValueError('请记录声学环境、旁人/播放设备及大致距离')
            if cfg.get('device') and cfg['device'] not in (self.device_info['device_address'], self.device_info['device_name']):
                raise ValueError('选择的设备与当前连接不同，请先断开再连接')
            cfg['device'] = self.device_info['device_address']
            cfg['trials'], cfg['seconds'] = int(cfg.get('trials', 5)), float(cfg.get('seconds', 120))
            if not 1 <= cfg['trials'] <= 50 or not 10 <= cfg['seconds'] <= 3600:
                raise ValueError('次数范围 1–50，时长范围 10–3600 秒')
            session = datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6]
            path = self.root / session
            path.mkdir()
            self.stop_event.clear()
            self.recent.clear()
            self.status.update(state='arming', session=session, t=0, samples=0, error='', phase='启动数据流',
                               audio={}, quality={}, scenario=cfg['scenario'], cue=SCENARIOS[cfg['scenario']][1],
                               recording_config=cfg)
            self.record_future = asyncio.run_coroutine_threadsafe(self._record(cfg, path), self.loop)
            def record_done(future):
                try:
                    future.result()
                except Exception as exc:
                    self.update(state='error', error=f'采集初始化/保存失败：{exc}', phase='采集失败')
            self.record_future.add_done_callback(record_done)
            return session

    async def _record(self, cfg, path):
        decoder = RawDecoder()
        need_imu, need_audio = cfg['streams'] != 'audio', cfg['streams'] != 'imu'
        seed = random.SystemRandom().randrange(2**31)
        schedule = make_schedule(cfg['kind'], cfg['trials'], cfg['seconds'], seed, cfg.get('activity') if cfg['kind']=='negative' else cfg['scenario'])
        metadata = dict(schema=2, task=TASK, session=path.name, created_utc=datetime.now(timezone.utc).isoformat(),
                        gesture_label_schema='mouth_raise_phases_v2_intent_independent',
                        synthetic=False, config=cfg, group=f"{cfg['participant']}/{cfg['visit']}",
                        sdk=verify_vendor(), imu_start_hex=IMU_START.hex() if need_imu else '',
                        mic_start_hex='2000' if need_audio else '', **self.device_info,
                        units=dict(acceleration='m/s^2, gravity included', gyroscope='rad/s'),
                        axes='device wire axes; physical finger-axis mapping unverified',
                        timestamp='IMU endpoint device ms; intrapacket SDK 200 Hz backfill; audio packet reference unverified',
                        complete=False, error='', schedule_seed=seed)
        write_json(path/'metadata.json', metadata)
        write_json(path/'prompts.json', dict(schema=2, status='planned cues, NOT ground truth', intervals=schedule))
        csv_file = (path/'imu.csv').open('w', newline='', encoding='utf-8')
        wire_file = (path/'wire.jsonl').open('w', encoding='utf-8')
        prediction_file = (path/'predictions.jsonl').open('w') if self.predictor else None
        writer = csv.DictWriter(csv_file, fieldnames=FIELDS)
        writer.writeheader()
        audio = AudioCapture(path) if need_audio else None
        error = ''
        final_t = 0.
        last_imu = time.monotonic()
        started = time.monotonic()
        def log(direction, raw):
            wire_file.write(json.dumps(dict(direction=direction, host_ns=time.monotonic_ns(), unix_ns=time.time_ns(), hex=raw.hex()))+'\n')
        def receive(raw):
            nonlocal error, final_t, last_imu
            now = time.monotonic_ns()
            log('rx', raw)
            try:
                if raw[:2] == b'\x21\x09' and len(raw) >= 3 and raw[2]:
                    raise ValueError('固件拒绝 IMU 启动：'+raw.hex())
                rows = decoder.feed(raw, now) if need_imu else []
                if rows:
                    writer.writerows(rows)
                    last_imu = time.monotonic()
                    final_t = rows[-1]['t']
                    with self.lock:
                        self.recent.extend([[r['t'], *[r[c] for c in CHANNELS]] for r in rows])
                    if self.predictor:
                        if metadata['firmware_version'] != self.predictor.config['firmware_version']:
                            raise ValueError('实时固件版本与训练不同')
                        for row in rows:
                            for pred in self.predictor.feed(row):
                                prediction_file.write(json.dumps(pred)+'\n')
                                self.update(prediction=pred)
                if audio:
                    audio.feed(raw, now)
                    self.update(audio=audio.snapshot())
                    if not need_imu:
                        final_t = audio.stats['samples']/16000
                ready = (not need_imu or decoder.stats['samples'] > 0) and (not audio or audio.stats['samples'] > 0)
                phase = next((x for x in schedule if x['start_s'] <= final_t < x['end_s']), schedule[-1])
                self.update(state='stopping' if self.stop_event.is_set() else ('recording' if ready else 'arming'),
                            samples=decoder.stats['samples'], t=final_t, phase=phase['label'], trial=phase['trial_id'],
                            remaining=max(0, schedule[-1]['end_s']-final_t), quality=decoder.stats.copy())
            except Exception as exc:
                error = str(exc)
                self.stop_event.set()
        async def send(raw):
            log('tx', raw)
            await asyncio.wait_for(self.client.write_gatt_char(RX, raw, response=False), 3)
        self.receiver = receive
        try:
            await send(b'\x2c\x00\x00')
            # Continuous audio starts before gesture cues; never gated by predicted raises.
            if need_audio:
                await send(b'\x20\x00')
            if need_imu:
                await send(IMU_START)
            while not self.stop_event.is_set() and final_t < schedule[-1]['end_s']:
                if not self.client.is_connected:
                    raise ConnectionError('BLE 断链，录制不完整')
                now = time.monotonic()
                if need_imu and now-last_imu > self.stream_timeout_s:
                    raise RuntimeError('5 秒未收到有效 raw IMU')
                if audio:
                    audio.drain(time.monotonic_ns())
                    last_audio = audio.last_good_host_ns/1e9 if audio.last_good_host_ns else started
                    if now-last_audio > self.stream_timeout_s:
                        raise RuntimeError('5 秒未收到完整可解码音频；不能以静默替代缺失音频')
                await asyncio.sleep(.05)
            if error:
                raise RuntimeError(error)
            if self.disconnect_event.is_set() or not self.client.is_connected:
                raise ConnectionError('采集中断开连接，已保存部分数据，标记为不完整')
            if (need_imu and not decoder.stats['samples']) or (audio and not audio.stats['samples']):
                raise RuntimeError('缺少请求的数据流')
            metadata['complete'] = True
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'
        finally:
            self.stop_event.set()
            self.update(state='stopping')
            if self.client and self.client.is_connected:
                for command in ([b'\x20\x01'] if need_audio else []) + ([b'\x21\x01'] if need_imu else []):
                    try:
                        await send(command)
                    except Exception as exc:
                        error = error or f'停止数据流失败：{exc}'
                        self.disconnect_event.set()
                # STOP has no ACK. Retain a short delivery tail before closing files.
                await asyncio.sleep(.2)
            self.receiver = None
            if audio:
                try:
                    audio.close(time.monotonic_ns())
                except Exception as exc:
                    error = error or str(exc)
                metadata['audio'] = audio.snapshot()
                metadata['audio'].pop('plot', None)
                metadata['audio_files'] = {n:sha256(path/n) for n in ('audio.wav','audio_blocks.jsonl','audio_events.jsonl')}
                write_json(path/'multimodal_annotations.json', dict(schema=1, reviewed=False, reviewer='', evidence='',
                           audio_sha256=metadata['audio_files']['audio.wav'], speech_intervals=[], intent_intervals=[],
                           speech_timebase='WAV seconds (gaps not padded)', intent_timebase='IMU relative seconds',
                           alignment=dict(reviewed=False, reference='unknown', offset_s=0., notes='')))
            csv_file.close()
            wire_file.close()
            if prediction_file:
                prediction_file.close()
            metadata.update(error=error, complete=metadata['complete'] and not error, duration_s=final_t,
                            stopped_early=final_t < schedule[-1]['end_s'], stats=decoder.stats,
                            imu_origin_device_s=decoder.origin,
                            imu_sha256=sha256(path/'imu.csv'), wire_sha256=sha256(path/'wire.jsonl'))
            write_json(path/'metadata.json', metadata)
            intervals = [{**x, 'end_s':min(x['end_s'],final_t+.001)} for x in schedule if x['start_s'] < final_t] if need_imu else []
            write_json(path/'annotations.json', dict(schema=1, reviewed=False, reviewer='', evidence='prompt-only',
                       imu_sha256=metadata['imu_sha256'], intervals=intervals))
            self.update(state='error' if error else 'complete', error=error,
                        audio=audio.snapshot() if audio else {}, phase='采集失败' if error else '已保存，需复核标注')
