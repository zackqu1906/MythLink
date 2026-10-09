#!/usr/bin/env python3
"""Observe real Ring Touchpad clicks with the application's double-click detector."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from proximic_ring.touchpad_clicks import TouchpadClicks
from ring_python_sdk.touchpad import TouchpadClick


class ClickRecorder:
    def __init__(self, folder, *, device, demo=False, contacts=False):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=False)
        self.file = (self.folder / "events.jsonl").open("x", encoding="utf-8")
        self.detector = TouchpadClicks()
        self.contacts = contacts
        self.started = time.monotonic()
        self.raw = self.single = self.double = 0
        self.motion_resets = self.stream_resets = 0
        self.last_click = None
        self.stats = None
        self.ready = False
        self.mode = "demo" if demo else "ring"
        self.write("start", device=device, interval_ms=self.detector.interval * 1000)

    def write(self, event, **fields):
        self.file.write(json.dumps(dict(event=event, mode=self.mode,
            utc=datetime.now(timezone.utc).isoformat(timespec="milliseconds"), **fields), ensure_ascii=False) + "\n")
        self.file.flush()

    def decisions(self, decisions, now):
        for decision in decisions:
            interval = ((decision.second.timestamp - decision.first.timestamp) * 1000
                        if decision.second is not None else None)
            if decision.kind == "double":
                self.double += 1
                print(f"  >>> 双击 #{self.double}  间隔 {interval:.1f} ms ≤ {self.detector.interval * 1000:.0f} ms", flush=True)
            else:
                self.single += 1
                print(f"  → 单击 #{self.single}  立即触发（处理延迟 {(now-decision.first.timestamp)*1000:.1f} ms）", flush=True)
            self.write(decision.kind, first=asdict(decision.first),
                       second=asdict(decision.second) if decision.second is not None else None,
                       interval_ms=interval, resolved_at=now)

    def observe(self, event, *, now=None):
        now = time.monotonic() if now is None else now
        if event.kind == "move":
            return
        fields = asdict(event)
        decisions = self.detector.feed(event, now=now)
        if event.kind == "click":
            self.raw += 1
            gap = None if self.last_click is None else (event.timestamp-self.last_click.timestamp)*1000
            self.last_click = event
            interval_label = "本组第一下" if gap is None else f"距上次 click {gap:.1f} ms"
            print(f"[CLICK #{self.raw}] step={event.step}  {interval_label}", flush=True)
            fields.update(raw_index=self.raw, previous_interval_ms=gap, age_ms=(now-event.timestamp)*1000)
        elif event.kind == "contact" and event.state == "reset":
            # The SDK uses step=0 for a whole processor reset; nonzero steps
            # describe expired trajectory frames, often several in one batch.
            stream_reset = event.step == 0
            if stream_reset:
                self.stream_resets += 1
                self.last_click = None
            else:
                self.motion_resets += 1
            fields.update(reset_scope="stream" if stream_reset else "expired_motion",
                          click_pair_reset=stream_reset)
            # Keep every event in the log, but summarize in the status line.
        elif self.contacts:
            if event.kind == "contact":
                print(f"[接触] {event.state}  step={event.step}", flush=True)
            elif event.kind == "click_verdict":
                print(f"[模型判定] {'click' if event.is_click else '非 click'}  step={event.step}", flush=True)
        self.write("sdk", **fields)
        self.decisions(decisions, now)

    def observe_stats(self, stats):
        self.stats = stats
        warm = stats.warmup_frames >= 200
        if warm != self.ready:
            self.ready = warm
            print("[就绪] 可以开始手测单击／双击。" if warm else "[预热] 正在恢复模型状态…", flush=True)
        self.write("stats", **asdict(stats))

    def status(self):
        detail = (f"tokens={self.stats.tokens}  预热={self.stats.warmup_frames}/200  "
                  f"接触概率={self.stats.contact_probability:.2f}  模型重置={self.stats.resets}"
                  if self.stats else "等待 Touchpad 数据…")
        print(f"[状态] {detail} | CLICK={self.raw}  单击={self.single}  双击={self.double}", flush=True)
        if self.motion_resets or self.stream_resets:
            print(f"       过期轨迹清理={self.motion_resets}  数据流重置={self.stream_resets}"
                  "（累计；重置只清除双击配对记录，已触发的单击不撤回）", flush=True)

    def finish(self, reason):
        # No deferred click to flush; only discard the pairing history.
        self.detector.clear()
        summary = dict(mode=self.mode, reason=reason, raw_clicks=self.raw,
                       single_clicks=self.single, double_clicks=self.double,
                       expired_motion_resets=self.motion_resets, stream_resets=self.stream_resets,
                       interval_ms=self.detector.interval*1000,
                       duration_s=round(time.monotonic()-self.started, 3))
        try:
            self.write("stop", **{k: v for k, v in summary.items() if k != "mode"})
            (self.folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        finally:
            self.file.close()
        print(f"\n结束：原始 CLICK {self.raw} 次，单击 {self.single} 次，双击 {self.double} 次。", flush=True)
        print(f"日志：{self.folder}", flush=True)


def demo(recorder):
    print("[模拟] 不连接 Ring；以下事件仅用于检查测试程序。", flush=True)
    for step, timestamp in ((1, 10.), (2, 11.), (3, 11.4), (4, 12.), (5, 12.6)):
        recorder.observe(TouchpadClick(step, timestamp), now=timestamp)


async def run(args, *, session_factory=None):
    folder = args.output or PROJECT_ROOT / "data" / "touchpad_double_click" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    recorder = ClickRecorder(Path(folder).expanduser().resolve(), device=args.device,
                             demo=args.demo, contacts=args.contacts)
    session = None
    reason = "completed"
    try:
        print(f"Touchpad 双击测试 | 设备 {args.device} | 最大间隔 {recorder.detector.interval * 1000:.0f} ms", flush=True)
        print(f"日志：{recorder.folder}", flush=True)
        if args.demo:
            demo(recorder)
            return
        print(f"请先在主程序或其他蓝牙工具中断开 {args.device}，再运行本测试。", flush=True)
        print("仅观察，不移动鼠标、不输入文字。Ctrl+C 停止并保存日志。", flush=True)
        if session_factory is None:
            from ring_python_sdk import RingSession
            session_factory = RingSession
        session = session_factory(name_keyword="Ringo", timeout_s=args.timeout,
            data_root=recorder.folder / "sdk", auto_reconnect=False, battery_poll_enabled=False)
        if not await session.connect_target(args.device):
            raise RuntimeError(f"未连接到 {args.device}，请确认戒指在线且已从主程序断开。")
        print(f"[已连接] {session.target_name}  {session.target_address}", flush=True)
        recorder.write("connected", name=session.target_name, address=session.target_address)
        queue = asyncio.Queue(maxsize=256)
        stopped = asyncio.Event()
        errors = []
        def on_event(event):
            if event.kind != "move":
                queue.put_nowait(("event", event))
        def on_stats(stats):
            queue.put_nowait(("stats", stats))
        def on_stopped(error):
            errors.append(error)
            stopped.set()
        await session.touchpad_on(on_event=on_event, on_stats=on_stats,
            on_stopped=on_stopped, duration_s=args.seconds or None)
        print("[预热] 等待约 200 帧。请先单击，再试两次快速点击；每组之间停顿约 1 秒。", flush=True)
        next_status = time.monotonic() + 2
        while not stopped.is_set():
            if session.client is None or not session.client.is_connected:
                raise ConnectionError("Ring 蓝牙连接已断开，请重新运行测试。")
            while not queue.empty():
                kind, value = queue.get_nowait()
                if kind == "event": recorder.observe(value)
                else: recorder.observe_stats(value)
            if time.monotonic() >= next_status:
                recorder.status()
                next_status = time.monotonic() + 2
            await asyncio.sleep(.01)
        if errors and errors[-1]:
            raise errors[-1]
    except asyncio.CancelledError:
        reason = "interrupted"
        raise
    except Exception as exc:
        reason = f"error: {exc}"
        raise
    finally:
        try:
            if session is not None:
                await session.disconnect()
        except Exception as exc:
            reason += f"; cleanup error: {exc}"
            raise
        finally:
            recorder.finish(reason)


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--device", default="8F56", help="设备名称片段或 BLE UUID（默认 8F56）")
    cli.add_argument("--seconds", type=float, default=0, help="测试秒数，0 表示持续到 Ctrl+C")
    cli.add_argument("--timeout", type=float, default=8, help="连接扫描超时秒数")
    cli.add_argument("--contacts", action="store_true", help="额外显示接触起落和模型 click 判定")
    cli.add_argument("--output", type=Path, help="日志目录，须为尚不存在的新目录")
    cli.add_argument("--demo", action="store_true", help="仅检查程序，不连接 Ring")
    return cli


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)
    if not math.isfinite(args.seconds) or args.seconds < 0:
        cli.error("--seconds 必须是有限非负数")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        cli.error("--timeout 必须是有限正数")
    try:
        asyncio.run(run(args))
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"测试失败：{exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
