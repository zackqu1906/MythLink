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
from urllib.parse import urlparse, parse_qs
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

from .analysis import LABELS, correlation, features, json_safe, quality, spectrum, summarize
from .speech import detect_speech

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
IMU_HEADER = "t,host_t,device_s,sample_index,packet_seq,ax,ay,az,gx,gy,gz,raw_ax,raw_ay,raw_az,raw_gx,raw_gy,raw_gz"
AUDIO_HEADER = "t,host_t,wav_frame,frames,rms,peak,status_flag"
TIMING_NOTE = ("IMU 与戒指麦克风共用戒指 uptime 时钟，保留原始时间戳、包序号和主机接收时间。"
               "IMU 包内时间由 SDK 回填；音频暂按块末时间戳向前回填 16 kHz PCM。"
               "音频块时间戳的采样边界尚未经固件确认，可能有一个块（通常 100 ms）的固定偏移；"
               "可在回看中调整音频对齐偏移。共享时间轴不代表已完成精密硬件同步，相关延迟不可当作传播时间。")


def save_json(path, value):
    # Atomic replacement keeps the last valid metadata/report on interruption.
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(json_safe(value), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(path)


@dataclass
class Config:
    demo: bool = False
    name: str = "Ringo8F56"
    chip: str = "auto"
    stream: str = "raw"
    hz: int = 200
    accel_fs: int = 16
    gyro_fs: int = 2000
    frames_per_packet: int = 10
    mic: str = "ring"
    audio_rate: int = 16000
    note: str = "手放桌面，正常佩戴"

    @classmethod
    def parse(cls, data):
        c = cls(**data)
        if type(c.demo) is not bool or c.chip not in ("auto", "icm42688", "icm45686"):
            raise ValueError("芯片配置无效")
        if c.stream != "raw":
            raise ValueError("新实验只采集新 SDK 的原始六轴；历史 token 实验仍可回看。")
        if (c.hz, c.accel_fs, c.gyro_fs) != (200, 16, 2000) or c.chip != "auto":
            raise ValueError("新 SDK 固定使用 200 Hz、±16 g、±2000 °/s；不能修改量程后沿用固定解码比例。")
        if c.frames_per_packet != 10 or c.audio_rate != 16000:
            raise ValueError("包大小或音频采样率无效")
        if not isinstance(c.name, str) or not c.name.strip() or len(c.name) > 128:
            raise ValueError("请输入戒指名称或地址")
        if c.mic not in ("ring", "none") or not isinstance(c.note, str) or len(c.note) > 500:
            raise ValueError("麦克风或备注无效")
        return c


class DeviceClock:
    """One device-to-host anchor, shared across independently arriving streams."""
    def __init__(self):
        self.base = self.anchor = None

    def map(self, uptime_ms, host_t, backfill_s=0.):
        if self.base is None:
            self.base, self.anchor = float(uptime_ms), host_t - backfill_s
        delta = (float(uptime_ms) - self.base + 2**31) % 2**32 - 2**31
        return self.anchor + delta / 1000


class Recorder:
    """One bounded writer queue; no disk I/O in BLE callbacks."""
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
        self.clock = DeviceClock()
        self.audio_previous_uptime = None
        self.audio_previous_seq = None
        self.audio_previous_end = None
        self.audio_zero_samples = 0
        self.audio_nonzero_samples = 0
        self.audio_missing_blocks = 0
        self.audio_blocks = 0
        self.error = ""
        self.previous_uptime = None
        self.device_seconds = 0.
        self.anchor = 0.
        self.elapsed = 0.
        self.closed = False
        self.q = queue.Queue(maxsize=10000)
        self.meta = {"schema": 3, "created": datetime.now().astimezone().isoformat(),
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

    def on_wire(self, direction, data):
        self.submit("packet", {"host_t": time.monotonic() - self.start,
                               "direction": direction, "hex": bytes(data).hex()})

    def on_imu(self, sample):
        host = time.monotonic() - self.start
        if sample.raw is None and self.config.stream == "raw":
            self.error = "收到非原始六轴帧；请使用支持 raw IMU 的固件。"
            return
        current = float(sample.uptime_ms)
        if self.previous_uptime is not None:
            delta = (current - self.previous_uptime + 2**31) % 2**32 - 2**31
            if delta <= 0 or delta > 10000:
                self.error = "设备时间重复、回退或长时间中断；已停止，避免生成错误频谱。"
                return
            self.device_seconds += delta / 1000
        self.previous_uptime = current
        aligned = self.clock.map(current, host, (self.config.frames_per_packet - 1) / self.config.hz)
        self.submit("imu", [aligned, host, self.device_seconds,
                            sample.sample_index, sample.packet_seq, *sample.accel_ms2,
                            *sample.gyro_dps, *(sample.raw if sample.raw is not None else [float("nan")] * 6)])

    def on_ring_audio(self, frame):
        """Preserve SDK-decoded PCM and device timestamps, including gaps."""
        host = time.monotonic() - self.start
        pcm = np.frombuffer(frame.pcm, dtype="<i2").astype(np.float32) / 32768.
        if not len(pcm) or frame.sample_rate != 16000:
            self.error = "戒指音频格式不符合 16 kHz PCM16 协议"
            return
        if self.audio_previous_uptime is not None:
            dt = (frame.uptime_ms - self.audio_previous_uptime + 2**31) % 2**32 - 2**31
            if dt <= 0 or dt > 10000:
                self.error = "戒指音频设备时间回退或长时间中断；已停止"
                return
            self.audio_missing_blocks += max(0, ((frame.sequence - self.audio_previous_seq) & 65535) - 1)
        end = self.clock.map(frame.uptime_ms, host)
        start = end - len(pcm) / 16000
        # A header timestamp describes a block, not the arrival of each sample.
        # Sub-millisecond rounding is tolerated; real overlap is never hidden.
        if self.audio_previous_end is not None and start < self.audio_previous_end - .002:
            self.error = "戒指音频时间戳与 PCM 长度重叠；已停止，保留原始数据"
            return
        self.audio_previous_uptime, self.audio_previous_seq = frame.uptime_ms, frame.sequence
        self.audio_previous_end = end
        self.audio_blocks += 1
        nonzero = int(np.count_nonzero(pcm))
        self.audio_nonzero_samples += nonzero
        self.audio_zero_samples = 0 if nonzero else self.audio_zero_samples + len(pcm)
        self.submit("audio_block", [host, frame.sequence, frame.uptime_ms, start, end, len(pcm), self.audio_missing_blocks])
        for offset in range(0, len(pcm), 320):
            part = pcm[offset:offset + 320]
            self.submit("audio", (start + (offset + len(part) / 2) / 16000, host, part, ""))

    def audio_health(self):
        return {"source": "synthetic" if self.config.demo else self.config.mic,
                "blocks": self.audio_blocks, "nonzero_samples": self.audio_nonzero_samples,
                "zero_run_s": self.audio_zero_samples / 16000,
                "missing_blocks": self.audio_missing_blocks,
                "ready": self.config.demo or (self.audio_nonzero_samples > 0 and self.audio_zero_samples < 32000)}

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
                 (self.path / "ring_audio_blocks.csv").open("w", newline="") as fb, \
                 (self.path / "ble_packets.jsonl").open("w") as fp, \
                 wave.open(str(self.path / "audio.wav"), "wb") as wav:
                wi, wa, we = csv.writer(fi), csv.writer(fa), csv.writer(fe)
                header = IMU_HEADER
                if self.config.stream == "token":
                    header = header.replace("ax,ay,az,gx,gy,gz,", "t1,t2,t3,t4,t5,t6,", 1)
                wi.writerow(header.split(",")); wa.writerow(AUDIO_HEADER.split(","))
                we.writerow(["host_t", "status"])
                wb = csv.writer(fb)
                wb.writerow(["host_t", "block_seq", "device_uptime_ms", "start_t", "end_t", "frames", "missing_blocks_total"])
                wav.setparams((1, 2, self.config.audio_rate, 0, "NONE", "not compressed"))
                last_flush = time.monotonic()
                while True:
                    item = self.q.get()
                    if item is None:
                        break
                    kind, data = item
                    with self.lock:
                        if kind == "packet":
                            fp.write(json.dumps(data) + "\n")
                        elif kind == "imu":
                            wi.writerow(data)
                            self.imu.append(data)
                            self.count += 1
                        elif kind == "audio_block":
                            wb.writerow(data)
                        else:
                            t, host, pcm, status = data
                            row = [t, host, self.audio_count, len(pcm),
                                   float(np.sqrt(np.mean(pcm.astype(float) ** 2))), float(np.max(np.abs(pcm))), int(bool(status))]
                            wa.writerow(row)
                            wav.writeframesraw(np.clip(np.rint(pcm * 32768), -32768, 32767).astype("<i2").tobytes())
                            self.audio.append(row)
                            self.audio_count += len(pcm)
                            self.last_audio_host = host
                            if status:
                                self.audio_status += 1
                                we.writerow([host, status])
                    if time.monotonic() - last_flush > 1:
                        fi.flush(); fa.flush(); fe.flush(); fb.flush(); fp.flush()
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
                         audio_status_events=self.audio_status, audio_health=self.audio_health())
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
    return {"imu": imu[:, np.r_[0, 5:17]].tolist(),
            "audio": audio[:, [0, 4, 5, 2, 3, 6]].tolist(),
            "features": f.tolist(), "spectrum": spectrum(imu, q["packet_hz"]) if q["timing_ok"] else {},
            "quality": q, "acc_correlation": correlation(f), "gyro_correlation": correlation(f, 2)}


def imu_response_error(data, stream="raw"):
    """Surface control responses instead of letting SDK count them as drops.

    No numeric status-code meaning is guessed: retain the wire evidence.
    """
    if bytes(data[:2]) == b"\x21\x09" and len(data) >= 3 and data[2] != 0:
        return (f"IMU {stream} 请求收到固件状态 {bytes(data).hex(' ')}，未获得所选数据流。"
                "请检查是否已升级到支持 raw 的 public_dev 固件。")
    if bytes(data[:2]) == b"\x21\x0a" or (stream == "raw" and bytes(data[:2]) == b"\x21\x05"):
        return "raw 请求返回了 token/四元数流，无法按原始六轴分析；请检查固件模式。"
    return ""


def dashboard_html():
    return Path(__file__).with_name("dashboard.html").read_text().replace(
        "/*DASHBOARD_SCRIPT*/", Path(__file__).with_name("dashboard.js").read_text())


def review_state(path, summary=None):
    meta = json.loads((path / "metadata.json").read_text())
    markers = json.loads((path / "markers.json").read_text())
    imu, audio = load_csv(path / "imu.csv", 17), load_csv(path / "audio.csv", 7)
    hz = meta["config"]["hz"]
    if summary is None:
        summary = json.loads((path / "summary.json").read_text())
    speech = detect_speech(path, audio, 'synthetic' if meta['synthetic'] else meta['config'].get('mic'))
    return {"status": "已保存", "active": False, "review": True, "speech": speech,
            "error": meta.get("error", ""), "demo": meta["synthetic"],
            "session_id": path.name, "path": str(path), "config": meta["config"],
            "hardware": meta["hardware"], "count": len(imu),
            "audio_frames": meta.get("audio_frames", 0), "audio_health": meta.get("audio_health", {}),
            "elapsed": meta.get("duration_s", 0), "markers": markers,
            "summary": summary, "timing": meta.get("timing", TIMING_NOTE), **charts(imu, audio, hz)}


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
    state = review_state(path, summary)
    save_json(path / 'speech_segments.json', state['speech'])
    html = dashboard_html()
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
        self.config = Config()
        self.summary = None
        self.sdk_stats = {}
        self.protocol_events = []

    def start(self, config):
        config = Config.parse(asdict(config))
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
        session = None
        try:
            if c.demo:
                r = self.recorder = Recorder(path, c)
                self.status = "演示数据采集中"
                await self._demo(r)
                return
            from .raw_sdk import RawSession
            session = RawSession(
                lambda sample: self.recorder.on_imu(sample) if self.recorder else None,
                lambda frame: self.recorder.on_ring_audio(frame) if self.recorder else None,
                lambda direction, data: self.recorder.on_wire(direction, data) if self.recorder else None)
            await session.connect(c.name)
            if self.stop.is_set():
                return
            r = self.recorder = Recorder(path, c, session.hardware)
            r.meta["hardware"]["microphone"] = ({"source": "ring", "name": session.hardware["name"],
                "codec": "adpcm", "sample_rate": 16000, "channels": 1, "software_gain": 1.0}
                if c.mic == "ring" else "disabled")
            await session.start(microphone=c.mic == "ring")
            save_json(path / "metadata.json", r.meta)
            self.status = "正在检查戒指音频" if c.mic == "ring" else "采集中"
            no_data_deadline = time.monotonic() + 8
            while not self.stop.is_set():
                session.drain()
                if r.error or session.error:
                    raise RuntimeError(r.error or session.error)
                if not session.connected:
                    raise RuntimeError("戒指连接已断开；当前实验已结束，请重新开始。")
                self.sdk_stats = dict(session.stats)
                now = time.monotonic() - r.start
                if c.mic == "ring":
                    if r.audio_zero_samples >= 32000:
                        raise RuntimeError("戒指麦克风连续输出 2 秒全零 PCM；已保存数据并停止，不能开始语音对照实验。")
                    if r.audio_health()["ready"]:
                        self.status = "采集中"
                if time.monotonic() > no_data_deadline:
                    if not r.count or (r.imu and now - r.imu[-1][1] > 3):
                        raise RuntimeError("3 秒以上无 IMU 数据；请检查连接。")
                    if c.mic == "ring" and (r.last_audio_host is None or now - r.last_audio_host > 3):
                        raise RuntimeError("戒指麦克风没有持续输出；已停止。请检查固件是否允许音频与 IMU 同时传输。")
                await asyncio.sleep(.1)
        finally:
            if session:
                try:
                    await session.disconnect()
                except Exception as exc:
                    self.error = self.error or f"断开戒指时发生错误: {exc}"
                finally:
                    self.error = self.error or session.error
                    if self.recorder:
                        self.sdk_stats = dict(session.stats)
                        self.recorder.meta["sdk_stats"] = self.sdk_stats
                        self.recorder.meta["audio_sdk_stats"] = dict(session.audio_stats)
                        self.recorder.meta["packet_types"] = dict(session.packet_types)

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
                raw = np.rint(np.r_[values[:3] / 9.80665 * 2048, values[3:] * 16.4])
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
                          hardware=r.meta["hardware"], markers=list(r.markers), audio_status=r.audio_status,
                          audio_health=r.audio_health())
        return json_safe(result)


def serve(lab, port=8766, open_browser=True):
    token = secrets.token_urlsafe(24)
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
                return self.respond(dashboard_html().replace("/*TOKEN*/''", json.dumps(token)), kind="text/html; charset=utf-8")
            if self.headers.get("X-Lab-Token") != token:
                return self.respond({"error": "Invalid token"}, 403)
            if self.path == "/api/state":
                return self.respond(lab.snapshot())
            route = urlparse(self.path)
            if route.path == "/api/sessions":
                sessions = []
                for path in sorted(lab.output.glob("*/metadata.json"), reverse=True):
                    if not (path.parent / "summary.json").exists():
                        continue
                    meta = json.loads(path.read_text())
                    sessions.append({"id": path.parent.name, "created": meta.get("created"),
                                     "samples": meta.get("imu_samples", 0), "synthetic": meta.get("synthetic", False),
                                     "mic": meta.get("config", {}).get("mic", "unknown"),
                                     "stream": meta.get("config", {}).get("stream", "unknown")})
                return self.respond({"sessions": sessions})
            if route.path in ("/api/review", "/api/wav"):
                try:
                    sid = parse_qs(route.query).get("session", [""])[0]
                    if not re.fullmatch(r"\d{8}_\d{6}_[a-f0-9]{6}(?:_DEMO)?", sid):
                        raise ValueError("实验目录无效")
                    path = (lab.output / sid).resolve()
                    if path.parent != lab.output.resolve() or not (path / "summary.json").exists():
                        raise ValueError("实验尚未保存或不存在")
                    if route.path == "/api/review":
                        return self.respond(review_state(path))
                    content = (path / "audio.wav").read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/wav")
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers(); self.wfile.write(content)
                    return
                except (ValueError, OSError, KeyError) as exc:
                    return self.respond({"error": str(exc)}, 400)
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
