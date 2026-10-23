from __future__ import annotations

import argparse
import asyncio
import csv
import json
import queue
import re
import secrets
import signal
import sys
import threading
import time
import wave
import webbrowser
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

from .analysis import LABELS, correlation, features, json_safe, quality, spectrum, summarize

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
IMU_HEADER = "t,host_t,device_s,sample_index,packet_seq,ax,ay,az,gx,gy,gz,raw_ax,raw_ay,raw_az,raw_gx,raw_gy,raw_gz"
AUDIO_HEADER = "t,host_t,wav_frame,frames,rms,peak,status_flag"
TIMING_NOTE = ("IMU 包内时间由 SDK 按请求速率回填；实测速率来自相邻包末时间戳。"
               "IMU 首包按接收时刻减去包跨度对齐，电脑麦克风使用 ADC 时间映射。"
               "两者不是硬件同步，仍含未知 BLE 延迟与时钟漂移；相关延迟不可当作传播时间。")


def save_json(path, value):
    # Atomic replacement keeps the last valid metadata/report on interruption.
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(json_safe(value), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(path)


@dataclass
class Config:
    demo: bool = False
    name: str = "Ringo"
    chip: str = "auto"
    stream: str = "raw"
    hz: int = 200
    accel_fs: int = 2
    gyro_fs: int = 250
    frames_per_packet: int = 10
    mic: str = "default"
    audio_rate: int = 16000
    note: str = "手放桌面，正常佩戴"

    @classmethod
    def parse(cls, data):
        c = cls(**data)
        if type(c.demo) is not bool or c.chip not in ("auto", "icm42688", "icm45686"):
            raise ValueError("芯片配置无效")
        if c.stream not in ("raw", "token"):
            raise ValueError("IMU 数据模式无效")
        if c.stream == "token" and (c.hz, c.accel_fs, c.gyro_fs) != (200, 16, 2000):
            raise ValueError("项目 token 模式固定使用 200 Hz、16 g、2000 °/s 启动参数；通道单位未知。")
        if c.hz not in (100, 200, 500, 1000) or c.accel_fs not in (2, 4, 8, 16) or c.gyro_fs not in (125, 250, 500, 1000, 2000):
            raise ValueError("采样率或量程无效")
        if c.frames_per_packet != 10 or c.audio_rate not in (16000, 48000):
            raise ValueError("包大小或音频采样率无效")
        if not isinstance(c.name, str) or not c.name.strip() or len(c.name) > 128:
            raise ValueError("请输入戒指名称或地址")
        if not isinstance(c.mic, str) or not isinstance(c.note, str) or len(c.note) > 500:
            raise ValueError("麦克风或备注无效")
        return c


class Recorder:
    """One bounded writer queue; no disk I/O in the PortAudio callback."""
    def __init__(self, path: Path, config: Config, hardware=None):
        self.path, self.config = path, config
        path.mkdir(parents=True, exist_ok=False)
        self.start = time.monotonic()
        self.lock = threading.RLock()
        self.imu = deque(maxlen=config.hz * 35)
        self.audio = deque(maxlen=2000)
        self.markers = [{"t": 0., "label": "unlabeled", "note": config.note}]
        self.count = 0
        self.audio_count = 0
        self.audio_status = 0
        self.last_audio_host = None
        self.error = ""
        self.previous_uptime = None
        self.device_seconds = 0.
        self.anchor = 0.
        self.elapsed = 0.
        self.closed = False
        self.q = queue.Queue(maxsize=10000)
        self.meta = {"schema": 1, "created": datetime.now().astimezone().isoformat(),
                     "config": asdict(config), "hardware": hardware or {},
                     "timing": TIMING_NOTE, "status": "recording", "synthetic": config.demo,
                     "units": ({"t1_t6": "firmware token units; unverified preprocessing", "audio": "full scale = 1"}
                               if config.stream == "token" else {"accel": "m/s²", "gyro": "deg/s", "audio": "full scale = 1"})}
        save_json(path / "metadata.json", self.meta)
        save_json(path / "markers.json", self.markers)
        self.writer = threading.Thread(target=self._write, daemon=True, name="imu-lab-writer")
        self.writer.start()

    def submit(self, kind, data):
        if self.closed or self.error:
            return
        try:
            self.q.put_nowait((kind, data))
        except queue.Full:
            self.error = "磁盘写入队列已满；实验中止，已有数据保留。"

    def on_imu(self, sample):
        host = time.monotonic() - self.start
        if sample.raw is None and self.config.stream == "raw":
            self.error = "收到非原始六轴帧；请使用支持 raw IMU 的固件。"
            return
        current = float(sample.uptime_ms)
        if self.previous_uptime is None:
            self.anchor = host - (self.config.frames_per_packet - 1) / self.config.hz
        else:
            delta = (current - self.previous_uptime + 2**31) % 2**32 - 2**31
            if delta <= 0 or delta > 10000:
                self.error = "设备时间重复、回退或长时间中断；已停止，避免生成错误频谱。"
                return
            self.device_seconds += delta / 1000
        self.previous_uptime = current
        self.submit("imu", [self.anchor + self.device_seconds, host, self.device_seconds,
                            sample.sample_index, sample.packet_seq, *sample.accel_ms2,
                            *sample.gyro_dps, *(sample.raw if sample.raw is not None else [float("nan")] * 6)])

    def on_audio(self, data, frames, timing, status):
        host = time.monotonic() - self.start
        adc_t = host - float(timing.currentTime - timing.inputBufferAdcTime)
        self.submit("audio", (adc_t + frames / (2 * self.config.audio_rate), host,
                              np.array(data[:, 0], dtype=np.float32, copy=True), str(status)))

    def mark(self, label, note=""):
        if label not in LABELS or self.closed:
            raise ValueError("标签无效，或采集已经结束")
        with self.lock:
            self.markers.append({"t": time.monotonic() - self.start, "label": label, "note": note[:500]})
            save_json(self.path / "markers.json", self.markers)

    def _write(self):
        try:
            with (self.path / "imu.csv").open("w", newline="") as fi, \
                 (self.path / "audio.csv").open("w", newline="") as fa, \
                 (self.path / "audio_events.csv").open("w", newline="") as fe, \
                 wave.open(str(self.path / "audio.wav"), "wb") as wav:
                wi, wa, we = csv.writer(fi), csv.writer(fa), csv.writer(fe)
                header = IMU_HEADER
                if self.config.stream == "token":
                    header = header.replace("ax,ay,az,gx,gy,gz,", "t1,t2,t3,t4,t5,t6,", 1)
                wi.writerow(header.split(",")); wa.writerow(AUDIO_HEADER.split(","))
                we.writerow(["host_t", "status"])
                wav.setparams((1, 2, self.config.audio_rate, 0, "NONE", "not compressed"))
                last_flush = time.monotonic()
                while True:
                    item = self.q.get()
                    if item is None:
                        break
                    kind, data = item
                    with self.lock:
                        if kind == "imu":
                            wi.writerow(data)
                            self.imu.append(data)
                            self.count += 1
                        else:
                            t, host, pcm, status = data
                            row = [t, host, self.audio_count, len(pcm),
                                   float(np.sqrt(np.mean(pcm.astype(float) ** 2))), float(np.max(np.abs(pcm))), int(bool(status))]
                            wa.writerow(row)
                            wav.writeframesraw((np.clip(pcm, -1, 1) * 32767).astype("<i2").tobytes())
                            self.audio.append(row)
                            self.audio_count += len(pcm)
                            self.last_audio_host = host
                            if status:
                                self.audio_status += 1
                                we.writerow([host, status])
                    if time.monotonic() - last_flush > 1:
                        fi.flush(); fa.flush(); fe.flush()
                        last_flush = time.monotonic()
        except Exception as exc:
            self.error = f"数据写入失败: {exc}"

    def arrays(self):
        with self.lock:
            return (np.asarray(self.imu, dtype=float).reshape(-1, 17),
                    np.asarray(self.audio, dtype=float).reshape(-1, 7))

    def close(self, error=""):
        self.closed = True
        self.elapsed = time.monotonic() - self.start
        # A failed writer may leave a full queue; never deadlock shutdown.
        while self.writer.is_alive():
            try:
                self.q.put(None, timeout=.2)
                break
            except queue.Full:
                continue
        self.writer.join(timeout=10)
        if self.writer.is_alive():
            self.error = "写入线程未能及时结束；请保留终端并检查磁盘。"
        self.meta.update(status="error" if error or self.error else "complete",
                         error=error or self.error, duration_s=self.elapsed,
                         imu_samples=self.count, audio_frames=self.audio_count,
                         audio_status_events=self.audio_status)
        save_json(self.path / "metadata.json", self.meta)


def load_csv(path, columns):
    if not path.exists() or len(path.read_text().splitlines()) < 2:
        return np.empty((0, columns))
    return np.loadtxt(path, delimiter=",", skiprows=1, ndmin=2)


def plot_rows(imu):
    """Keep each axis's extrema in each display bin, not every Nth sample."""
    if len(imu) <= 1200:
        return imu[:, np.r_[0, 5:11]].tolist()
    chosen = {0, len(imu) - 1}
    for group in np.array_split(np.arange(len(imu)), 100):
        v = imu[group, 5:11]
        chosen.update(group[np.argmin(v, axis=0)])
        chosen.update(group[np.argmax(v, axis=0)])
    return imu[sorted(chosen)][:, np.r_[0, 5:11]].tolist()


def charts(imu, audio, hz):
    q = quality(imu, hz)
    f = features(imu, audio, hz) if q["timing_ok"] else np.empty((0, 4))
    return {"imu": plot_rows(imu), "audio": audio[:, [0, 4]].tolist(),
            "features": f.tolist(), "spectrum": spectrum(imu, hz) if q["timing_ok"] else {},
            "quality": q, "acc_correlation": correlation(f), "gyro_correlation": correlation(f, 2)}


def imu_response_error(data, stream="raw"):
    """Surface control responses instead of letting SDK count them as drops.

    No numeric status-code meaning is guessed: retain the wire evidence.
    """
    if bytes(data[:2]) == b"\x21\x09" and len(data) >= 3 and data[2] != 0:
        return (f"IMU {stream} 请求收到固件状态 {bytes(data).hex(' ')}，未获得所选数据流。"
                "raw 不支持时可选择项目同款 token 模式作探索；token 的单位与预处理尚未确认。")
    if bytes(data[:2]) == b"\x21\x0a" or (stream == "raw" and bytes(data[:2]) == b"\x21\x05"):
        return "raw 请求返回了 token/四元数流，无法按原始六轴分析；请检查固件模式。"
    return ""


async def start_project_tokens(session, on_sample):
    """Same START as project touchpad; observe tokens without MNN/cursor output.

    Current SDK's generic demux reserves 21 05 for touchpad_active. Our local
    ObservedSession explicitly sends those packets to its existing IMU decoder.
    No SDK or product file is changed.
    """
    from ring_python_sdk.imu import ImuProcessor
    from ring_python_sdk.touchpad.core import START
    if session.imu_active or session.touchpad_active or session.quaternion_active:
        raise RuntimeError("请先停止已有 IMU 流")
    session.imu = ImuProcessor(session._seg_path("imu", "ring_tokens.csv"), None, False,
                               2000, 16, 200, 200, 10, encode_mode="token", on_sample=on_sample)
    session.imu_active = True
    session._imu_starting = True
    try:
        await session.client.write_gatt_char(session.rx_uuid, START, response=False)
    finally:
        session._imu_starting = False


def make_report(path):
    meta = json.loads((path / "metadata.json").read_text())
    markers = json.loads((path / "markers.json").read_text())
    imu, audio = load_csv(path / "imu.csv", 17), load_csv(path / "audio.csv", 7)
    hz = meta["config"]["hz"]
    summary = summarize(imu, audio, markers, hz)
    summary["signals"] = ({"acc": "T1–T3 (token units)", "gyro": "T4–T6 (token units)"}
                          if meta["config"].get("stream") == "token"
                          else {"acc": "acceleration (m/s²)", "gyro": "gyroscope (deg/s)"})
    summary["metadata"] = meta
    save_json(path / "summary.json", summary)
    with (path / "conditions.csv").open("w", newline="") as f:
        if summary["conditions"]:
            writer = csv.DictWriter(f, fieldnames=list(summary["conditions"][0]))
            writer.writeheader(); writer.writerows(summary["conditions"])
    # Report plots show the last 30s, statistics cover every saved sample.
    tail = imu[imu[:, 0] >= imu[-1, 0] - 30] if len(imu) else imu
    a = audio[audio[:, 0] >= tail[0, 0]] if len(tail) and len(audio) else audio
    state = {"status": "已保存", "error": meta.get("error", ""), "demo": meta["synthetic"],
             "path": str(path), "config": meta["config"], "hardware": meta["hardware"],
             "count": len(imu), "audio_frames": meta.get("audio_frames", 0),
             "elapsed": meta.get("duration_s", 0), "markers": markers,
             "summary": summary, "timing": TIMING_NOTE, **charts(tail, a, hz)}
    html = (Path(__file__).with_name("dashboard.html")).read_text()
    payload = json.dumps(json_safe(state), ensure_ascii=False, allow_nan=False).replace("<", "\\u003c")
    html = html.replace("/*BOOTSTRAP*/null", payload)
    (path / "report.html").write_text(html, encoding="utf-8")
    return summary


class Lab:
    def __init__(self, output):
        self.output = output
        self.lock = threading.RLock()
        self.recorder = None
        self.thread = None
        self.stop = threading.Event()
        self.status = "待开始"
        self.error = ""
        self.config = Config(stream="token", accel_fs=16, gyro_fs=2000)
        self.summary = None
        self.sdk_stats = {}
        self.protocol_events = []

    def start(self, config):
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise ValueError("当前实验还未结束")
            self.stop.clear()
            self.config = config
            self.recorder = None
            self.summary = None
            self.sdk_stats = {}
            self.protocol_events = []
            self.error = ""
            self.status = "正在准备" if config.demo else "正在连接戒指"
            self.thread = threading.Thread(target=self._run, daemon=True, name="imu-lab-capture")
            self.thread.start()

    def _run(self):
        try:
            asyncio.run(self._capture())
        except Exception as exc:
            self.error = str(exc)
        finally:
            r = self.recorder
            if r:
                self.status = "正在保存"
                r.close(self.error)
                self.error = self.error or r.error
                try:
                    self.summary = make_report(r.path)
                except Exception as exc:
                    self.error = self.error or f"原始数据已保留，但报告生成失败: {exc}"
            self.status = "出错" if self.error else "已保存"

    async def _capture(self):
        c = self.config
        path = self.output / (datetime.now().strftime("%Y%m%d_%H%M%S_") + secrets.token_hex(3) + ("_DEMO" if c.demo else ""))
        session, mic = None, None
        try:
            hardware = {}
            if not c.demo:
                from ring_python_sdk import RingSession
                lab = self

                class ObservedSession(RingSession):
                    def _demux(self, sender, data):
                        if c.stream == "token" and data[:2] == b"\x21\x05" and self.imu is not None:
                            from ring_python_sdk.touchpad.core import parse_tokens
                            try:
                                parse_tokens(bytes(data))  # same validity checks as the project
                            except ValueError:
                                self.imu.stats.invalid_packet_count += 1
                                return
                            self.imu.handle_notification(sender, data)
                            return
                        if (self.imu is not None and data and data[0] == 0x21 and
                                (len(data) < 9 or data[1] not in (2, 4))):
                            event = {"host_monotonic": time.monotonic(), "packet_hex": bytes(data[:24]).hex(" ")}
                            lab.protocol_events = (lab.protocol_events + [event])[-32:]
                            reason = imu_response_error(data, c.stream)
                            if reason and lab.recorder:
                                lab.recorder.error = reason
                        super()._demux(sender, data)

                session = ObservedSession(name_keyword=c.name, timeout_s=5,
                                      data_root=path.parent / "sdk", battery_poll_enabled=False)
                if re.fullmatch(r"[0-9a-fA-F:-]{17,36}", c.name):
                    connected = await session.connect_target(c.name)
                else:
                    devices = await session.scan(quiet=True)
                    exact = [d for d in devices if (d.name or "").lower() == c.name.lower()]
                    matches = exact or devices
                    if len(matches) > 1:
                        raise RuntimeError("发现多枚戒指，请填写完整名称后重试：" + "、".join(d.name or d.address for d in matches))
                    connected = bool(matches) and await session.connect_device(matches[0])
                if not connected:
                    raise RuntimeError("未找到戒指。请确认开机，并在占用它的 Mythlink/SDK 中断开连接后重试。")
                # query_info() sends only; the INFO notification is asynchronous.
                deadline = time.monotonic() + 2
                while session.device_info is None and time.monotonic() < deadline and not self.stop.is_set():
                    await asyncio.sleep(.05)
                if self.stop.is_set():
                    return
                from ring_python_sdk.core.device_info import format_device_info
                import ring_python_sdk
                hardware = {"name": session.target_name, "address": session.target_address,
                            "info": format_device_info(session.device_info), "sdk_path": ring_python_sdk.__file__,
                            "stream": c.stream}
                component = session.device_info.component(1) if session.device_info else None
                reported = {1: "icm42688", 2: "icm45686"}.get(component.model) if component and component.present else None
                if c.stream == "raw" and c.chip == "auto" and not reported:
                    raise RuntimeError("固件未报告可识别的 IMU 型号；请选择实际芯片后重试，避免错误单位换算。")
                if c.chip != "auto" and reported and c.chip != reported:
                    raise RuntimeError(f"手动芯片配置与设备报告 {reported} 不一致，请修正。")
                session.imu_chip = reported or (c.chip if c.chip != "auto" else "icm42688")
                hardware.update(chip=reported or (c.chip if c.chip != "auto" else "unknown"), chip_source="device_info" if reported else "manual_or_unknown",
                                sdk_capture=str(session.session_dir))
                if self.stop.is_set():
                    return
            r = self.recorder = Recorder(path, c, hardware)
            if c.demo:
                self.status = "演示数据采集中"
                await self._demo(r)
                return
            if c.mic != "none":
                import sounddevice as sd
                device = None if c.mic == "default" else int(c.mic)
                info = sd.query_devices(device, "input")
                r.meta["hardware"]["microphone"] = dict(info)
                mic = sd.InputStream(device=device, channels=1, samplerate=c.audio_rate,
                                     blocksize=c.audio_rate // 50, dtype="float32", callback=r.on_audio)
                mic.start()
            else:
                r.meta["hardware"]["microphone"] = "disabled"
            save_json(path / "metadata.json", r.meta)
            if c.stream == "token":
                await start_project_tokens(session, r.on_imu)
            else:
                await session.imu_on(gyro_hz=c.hz, accel_hz=c.hz, gyro_fs=c.gyro_fs,
                                     accel_fs=c.accel_fs, frames_per_packet=10, encode_mode=0,
                                     lp=False, on_sample=r.on_imu)
            self.status = "采集中"
            no_data_deadline = time.monotonic() + 8
            while not self.stop.is_set():
                if r.error:
                    raise RuntimeError(r.error)
                if not session.client or not session.client.is_connected:
                    raise RuntimeError("戒指连接已断开；当前实验已结束，请重新开始。")
                self.sdk_stats = asdict(session.imu.stats) if session.imu else self.sdk_stats
                now = time.monotonic() - r.start
                if time.monotonic() > no_data_deadline:
                    if not r.count or (r.imu and now - r.imu[-1][1] > 3):
                        raise RuntimeError("3 秒以上无 IMU 数据；请检查连接或降低请求采样率。")
                    if mic and (r.last_audio_host is None or now - r.last_audio_host > 3):
                        raise RuntimeError("麦克风没有持续输出；请检查系统麦克风权限与输入设备。")
                await asyncio.sleep(.1)
        finally:
            if mic is not None:
                try:
                    mic.stop(); mic.close()
                except Exception as exc:
                    self.error = self.error or f"麦克风关闭失败: {exc}"
            if session:
                if self.recorder:
                    if session.imu:
                        self.sdk_stats = asdict(session.imu.stats)
                    self.recorder.meta["sdk_stats"] = self.sdk_stats
                    self.recorder.meta["protocol_events"] = self.protocol_events
                try:
                    await asyncio.wait_for(session.disconnect(), timeout=8)
                except Exception as exc:
                    self.error = self.error or f"断开戒指时发生错误: {exc}"

    async def _demo(self, r):
        rng = np.random.default_rng(20261023)
        c = r.config
        i, frame = 0, 0
        while not self.stop.is_set():
            elapsed = time.monotonic() - r.start
            label = r.markers[-1]["label"]
            voice = label in ("self_speech", "vowel", "both", "other_speech", "playback")
            own = label in ("self_speech", "vowel", "both")
            # Deliberately injected correlation ONLY for software verification.
            while i / c.hz <= elapsed:
                t = i / c.hz
                env = .3 + .7 * np.sin(2 * np.pi * .7 * t) ** 2
                vibration = (.02 if own else .001) * env * np.sin(2 * np.pi * 31 * t)
                motion = .8 * np.sin(2 * np.pi * 2 * t) if label == "movement" else 0
                values = rng.normal(0, .001, 6) + [vibration + motion, 0, 9.80665, vibration * 5 + motion * 5, 0, 0]
                raw = np.r_[values[:3] / 9.80665 * 16384, values[3:] * 131]
                r.submit("imu", [t, elapsed, t, i, i // 10, *values, *raw])
                i += 1
            n = c.audio_rate // 50
            t = (frame + np.arange(n)) / c.audio_rate
            env = .3 + .7 * np.sin(2 * np.pi * .7 * t) ** 2
            pcm = rng.normal(0, .0003, n) + (voice * .08 * env * np.sin(2 * np.pi * 155 * t))
            r.submit("audio", ((frame + n / 2) / c.audio_rate, elapsed, pcm, ""))
            frame += n
            if r.error:
                raise RuntimeError(r.error)
            await asyncio.sleep(max(.001, frame / c.audio_rate - (time.monotonic() - r.start)))

    def snapshot(self):
        r = self.recorder
        result = {"status": self.status, "error": self.error, "demo": self.config.demo,
                  "active": bool(self.thread and self.thread.is_alive()),
                  "config": asdict(self.config), "summary": self.summary, "timing": TIMING_NOTE,
                  "sdk_stats": self.sdk_stats, "protocol_events": self.protocol_events}
        if r:
            imu, audio = r.arrays()
            result.update(charts(imu, audio, self.config.hz))
            result.update(path=str(r.path), count=r.count, audio_frames=r.audio_count,
                          elapsed=r.elapsed if r.closed else time.monotonic() - r.start,
                          hardware=r.meta["hardware"], markers=list(r.markers), audio_status=r.audio_status)
        return json_safe(result)


def serve(lab, port=8766, open_browser=True):
    token = secrets.token_urlsafe(24)
    html = Path(__file__).with_name("dashboard.html").read_text()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def allowed_host(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def respond(self, data, code=200, kind="application/json; charset=utf-8"):
            body = data.encode() if isinstance(data, str) else json.dumps(json_safe(data), ensure_ascii=False, allow_nan=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            if not self.allowed_host():
                return self.respond({"error": "Invalid Host"}, 403)
            if self.path == "/":
                return self.respond(html.replace("/*TOKEN*/''", json.dumps(token)), kind="text/html; charset=utf-8")
            if self.headers.get("X-Lab-Token") != token:
                return self.respond({"error": "Invalid token"}, 403)
            if self.path == "/api/state":
                return self.respond(lab.snapshot())
            if self.path == "/api/devices":
                try:
                    import sounddevice as sd
                    items = [{"id": i, "name": d["name"]} for i, d in enumerate(sd.query_devices()) if d["max_input_channels"]]
                    return self.respond({"devices": items})
                except Exception as exc:
                    return self.respond({"devices": [], "error": str(exc)})
            return self.respond({"error": "Not found"}, 404)

        def do_POST(self):
            if not self.allowed_host() or self.headers.get("X-Lab-Token") != token:
                return self.respond({"error": "Invalid token/host"}, 403)
            origin = self.headers.get("Origin")
            if origin and origin != f"http://127.0.0.1:{self.server.server_port}":
                return self.respond({"error": "Invalid origin"}, 403)
            try:
                n = int(self.headers.get("Content-Length", 0))
                if not 0 < n <= 8192:
                    raise ValueError("请求大小无效")
                data = json.loads(self.rfile.read(n))
                if self.path == "/api/start":
                    lab.start(Config.parse(data))
                elif self.path == "/api/stop":
                    lab.stop.set()
                elif self.path == "/api/mark":
                    if not lab.recorder or lab.status not in ("采集中", "演示数据采集中"):
                        raise ValueError("请先开始采集")
                    lab.recorder.mark(data["label"], data.get("note", ""))
                else:
                    return self.respond({"error": "Not found"}, 404)
                self.respond({"ok": True})
            except (ValueError, TypeError, KeyError) as exc:
                self.respond({"error": str(exc)}, 400)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"IMU Speech Lab: {url}\n数据目录: {lab.output}\nCtrl+C 停止服务并保存。", flush=True)
    if open_browser:
        webbrowser.open(url)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        while True:
            time.sleep(.3)
    except KeyboardInterrupt:
        lab.stop.set()
        if lab.thread:
            lab.thread.join(timeout=25)
        server.shutdown()
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description="Ringo IMU + speech experiment (local dashboard)")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "imu_speech_lab")
    parser.add_argument("--report", type=Path, help="Rebuild offline report from a saved session directory")
    args = parser.parse_args()
    if args.report:
        make_report(args.report.resolve())
        print(args.report.resolve() / "report.html")
        return
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    serve(Lab(args.output.resolve()), args.port, not args.no_browser)


if __name__ == "__main__":
    main()
