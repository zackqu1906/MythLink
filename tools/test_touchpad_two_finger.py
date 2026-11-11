#!/usr/bin/env python3
"""Try Raw2 two-finger recognition on a real ring (Windows x64 / Apple Silicon Mac).

By default nothing is sent to the system: the tool only prints and records
what the model recognised. Add --scroll to also post wheel events to the
foreground window on Windows, exactly as the product's two-finger scroll does.

  .runtime\\venv\\Scripts\\python.exe tools\\test_touchpad_two_finger.py --device Ringo8F56
  .runtime\\venv\\Scripts\\python.exe tools\\test_touchpad_two_finger.py --demo
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import json
import math
from pathlib import Path
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ring_python_sdk.touchpad.events import Raw2PointerClick, Raw2PointerMove  # noqa: E402
from ring_python_sdk.touchpad.two_finger import Direction, TwoFingerGestures  # noqa: E402

LABELS = {"SINGLE": "单指", "TWO_FINGER": "双指", "UNKNOWN": "未分类"}
ARROWS = {Direction.UP: "↑ 上", Direction.UP_RIGHT: "↗ 右上", Direction.RIGHT: "→ 右",
          Direction.DOWN_RIGHT: "↘ 右下", Direction.DOWN: "↓ 下", Direction.DOWN_LEFT: "↙ 左下",
          Direction.LEFT: "← 左", Direction.UP_LEFT: "↖ 左上"}
OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "two-finger-test"
LAST_DEVICE = OUTPUT_ROOT / "last_device.txt"
# A segment ends when no motion of the same mode arrives for this long.
SEGMENT_GAP_S = .15


def mode_name(mode) -> str:
    return str(getattr(mode, "value", mode)).upper()


class Segment:
    def __init__(self, mode, now):
        self.mode, self.started, self.last = mode, now, now
        self.frames = 0
        self.dx = self.dy = self.path = 0.

    def add(self, event, now):
        self.frames += 1
        self.dx += event.dx
        self.dy += event.dy
        self.path += math.hypot(event.dx, event.dy)
        self.last = now


class TwoFingerProbe:
    def __init__(self, folder: Path, *, scroll_gain: float, mouse=None, quiet_single=False):
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=False)
        self.log = (folder / "events.jsonl").open("x", encoding="utf-8")
        self.scroll_gain, self.mouse, self.quiet_single = scroll_gain, mouse, quiet_single
        self.started = time.monotonic()
        self.segment: Segment | None = None
        self.frames = {"SINGLE": 0, "TWO_FINGER": 0, "UNKNOWN": 0}
        self.segments = {"SINGLE": [], "TWO_FINGER": [], "UNKNOWN": []}
        self.clicks = []
        self.wheel_posts = 0
        self.swipes = []
        self.gestures = TwoFingerGestures(on_swipe=self.swipe)

    def write(self, event, **fields):
        fields = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in fields.items()}
        self.log.write(json.dumps(dict(event=event, t=round(time.monotonic() - self.started, 4), **fields),
                                  ensure_ascii=False) + "\n")

    def stamp(self):
        return f"[{time.monotonic() - self.started:7.2f}s]"

    def pointer(self, event):
        now = time.monotonic()
        if isinstance(event, Raw2PointerClick):
            self.close_segment(now)
            name = {("left", 1): "左键单击", ("left", 2): "左键双击", ("right", 1): "右键单击"}.get(
                (event.button, event.count), f"{event.button}×{event.count}")
            self.clicks.append(name)
            self.write("click", button=event.button, count=event.count, click_id=event.click_id)
            print(f"{self.stamp()} ● {name}", flush=True)
            return
        if not isinstance(event, Raw2PointerMove):
            return
        self.gestures.feed(event)
        mode = mode_name(event.mode)
        self.frames[mode] = self.frames.get(mode, 0) + 1
        self.write("move", mode=mode, dx=event.dx, dy=event.dy, frame=event.step,
                   session=event.session_id, generation=event.generation)
        if self.segment is None or self.segment.mode != mode:
            self.close_segment(now)
            self.segment = Segment(mode, now)
        self.segment.add(event, now)
        if mode == "TWO_FINGER" and self.mouse is not None:
            # Same mapping as the product: wheel = -dy * scroll gain (pixel units).
            try:
                result = self.mouse.apply([dict(kind="scroll", dy=-event.dy * self.scroll_gain,
                                                stream=[event.session_id, event.generation])])
                self.wheel_posts += result.get("scrolls", 0)
            except Exception as exc:  # Esc or SendInput failure: stop output, keep observing.
                self.mouse.disable()
                self.mouse = None
                print(f"{self.stamp()} ！滚轮输出已停止：{exc}（识别继续）", flush=True)

    def swipe(self, swipe):
        self.swipes.append(swipe)
        self.write("swipe", direction=swipe.direction.value, angle=swipe.angle_deg, dx=swipe.dx, dy=swipe.dy,
                   distance=swipe.distance, duration_ms=swipe.duration_s * 1000, frames=swipe.frames)
        print(f"{self.stamp()} ■ 双指 {ARROWS[swipe.direction]:<5}  角度 {swipe.angle_deg:5.0f}°  "
              f"距离 {swipe.distance:6.1f} px  ({swipe.dx:+.0f}, {swipe.dy:+.0f})  "
              f"{swipe.duration_s * 1000:4.0f} ms  {swipe.frames} 帧", flush=True)

    def tick(self):
        self.gestures.tick()
        if self.segment is not None and time.monotonic() - self.segment.last > SEGMENT_GAP_S:
            self.close_segment(time.monotonic())

    def close_segment(self, now):
        segment, self.segment = self.segment, None
        if segment is None:
            return
        duration = (segment.last - segment.started) * 1000
        record = dict(mode=segment.mode, duration_ms=duration, frames=segment.frames,
                      dx=segment.dx, dy=segment.dy, path=segment.path)
        self.segments[segment.mode].append(record)
        self.write("segment", **record)
        if segment.mode == "TWO_FINGER":
            pass  # Reported per gesture by TwoFingerGestures (see swipe()).
        elif not self.quiet_single and segment.path >= 1:
            label = LABELS.get(segment.mode, segment.mode)
            print(f"{self.stamp()} · {label}移动  {duration:5.0f} ms  {segment.frames:3d} 帧  "
                  f"路径 {segment.path:6.1f} px", flush=True)

    def finish(self, reason):
        self.close_segment(time.monotonic())
        self.gestures.flush()
        two = self.segments["TWO_FINGER"]
        by_direction = {d.value: sum(s.direction is d for s in self.swipes) for d in Direction}
        total = sum(self.frames.values())
        summary = dict(
            reason=reason, seconds=round(time.monotonic() - self.started, 1),
            frames=self.frames, two_finger_share=round(self.frames["TWO_FINGER"] / total, 3) if total else 0,
            two_finger_segments=len(two),
            two_finger_avg_ms=round(sum(s["duration_ms"] for s in two) / len(two)) if two else 0,
            two_finger_up=sum(s["dy"] < 0 for s in two), two_finger_down=sum(s["dy"] > 0 for s in two),
            two_finger_swipes=len(self.swipes), swipe_directions=by_direction,
            single_segments=len(self.segments["SINGLE"]), clicks=self.clicks, wheel_posts=self.wheel_posts)
        self.write("summary", **summary)
        self.log.close()
        (self.folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                                  encoding="utf-8")
        print("\n========== 汇总 ==========")
        print(f"结束原因：{reason}    时长 {summary['seconds']} s")
        print(f"运动帧：单指 {self.frames['SINGLE']}  双指 {self.frames['TWO_FINGER']}  "
              f"未分类 {self.frames['UNKNOWN']}  （双指占比 {summary['two_finger_share']:.1%}）")
        print(f"双指手势 {len(self.swipes)} 次：" + "  ".join(
            f"{ARROWS[d]} {by_direction[d.value]}" for d in Direction))
        print(f"单指片段 {summary['single_segments']} 段；点击 {len(self.clicks)} 次")
        if self.mouse is not None:
            print(f"实际发送滚轮 {self.wheel_posts} 次")
        print(f"记录：{self.folder}")


async def run_ring(args, probe):
    from ring_python_sdk import RingSession
    from ring_python_sdk.touchpad.raw2_model import raw2_platform_supported
    if not raw2_platform_supported():
        raise SystemExit("当前环境不支持 Raw2：需要 Windows x64（已安装 onnxruntime，"
                         "且未设置 PROXIMIC_WINDOWS_RAW2=0）或 Apple Silicon Mac。")
    session = RingSession(args.device, args.connect_timeout, connection_backend="sdk",
                          battery_poll_enabled=False)
    stopped = asyncio.Event()
    outcome = []
    last_stats = [0.]

    def on_stats(stats):
        now = time.monotonic()
        if now - last_stats[0] >= 5:
            last_stats[0] = now
            age = stats.last_data_age_s
            age = f"{age * 1000:.0f} ms" if age is not None else "-"
            print(f"{probe.stamp()}   (tokens={stats.tokens} resets={stats.resets} 数据间隔 {age})", flush=True)

    def on_stopped(error):
        outcome.append(error)
        stopped.set()

    print(f"连接 {args.device} ...", flush=True)
    try:
        if not await session.connect_target(args.device):
            raise SystemExit("无法连接所选戒指。确认蓝牙已打开、戒指可连接；Windows 弹出配对提示时先完成配对。")
        info = getattr(session._sdk_transport, "device_info", None)
        if info is not None:
            print(f"固件 {info.firmware_version}  硬件 {info.hardware_revision}  "
                  f"MTU {session.client.mtu_size}", flush=True)
            probe.write("connected", firmware=info.firmware_version, hardware=info.hardware_revision,
                        mtu=session.client.mtu_size)
        if info is not None and ".touchpad." not in info.firmware_version:
            raise SystemExit(
                f"这枚戒指的固件是 {info.firmware_version}，不支持新版 Raw2 触摸板（没有双指识别）。\n"
                "需要配套触摸板固件，例如 2.1.6-dev.touchpad.9.debug（dev9）。请换一枚戒指（输入 D）。")
        print("启动触摸板（加载模型、等待首包）...", flush=True)
        await session.touchpad_on(protocol="raw2", on_event=lambda _: None, on_pointer_move=probe.pointer,
                                  on_stats=on_stats, on_stopped=on_stopped, duration_s=args.seconds)
        print(f"\n就绪。请在戒指上用两根手指上下滑动（单指移动、点击也会显示）。"
              f"{args.seconds:.0f} 秒后自动结束，Ctrl+C 提前结束。\n", flush=True)
        while not stopped.is_set():
            probe.tick()
            try:
                await asyncio.wait_for(stopped.wait(), .05)
            except TimeoutError:
                pass
        error = outcome[-1] if outcome else None
        return f"错误：{error}" if error else "到时"
    finally:
        try:
            await session.touchpad_off()
        except Exception:
            pass
        await session.disconnect()


async def run_demo(args, probe):
    """Synthetic events to check the display and log; no ring, no system input."""
    def move(dx, dy, mode, step):
        return Raw2PointerMove(dx, dy, 1., step, time.monotonic(), mode=mode, session_id="demo")
    step = 0
    eight = [(0, -3), (2, -2), (3, 0), (2, 2), (0, 3), (-2, 2), (-3, 0), (-2, -2)]
    for mode, dx, dy, frames in [("SINGLE", 2, 0, 20), *[("TWO_FINGER", x, y, 20) for x, y in eight],
                                 ("SINGLE", 0, 1.5, 10)]:
        for _ in range(frames):
            step += 1
            probe.pointer(move(dx, dy, mode, step))
            await asyncio.sleep(.005)
        await asyncio.sleep(.2)
        probe.tick()
    probe.pointer(Raw2PointerClick(step, time.monotonic(), count=2, click_id=1, session_id="demo"))
    return "演示结束"


def run_once(args):
    mouse = None
    if args.scroll and not args.demo:
        if sys.platform != "win32":
            raise SystemExit("--scroll 仅支持 Windows")
        from ring_python_sdk.touchpad.windows import WindowsSystemMouse
        mouse = WindowsSystemMouse()
        mouse.stop_on_escape = True  # Esc stops wheel output immediately.
        mouse.enable()
    folder = OUTPUT_ROOT / datetime.now().strftime("%Y%m%d-%H%M%S")
    probe = TwoFingerProbe(folder, scroll_gain=args.scroll_gain, mouse=mouse, quiet_single=args.quiet_single)
    reason = "中断"
    try:
        reason = asyncio.run(run_demo(args, probe) if args.demo else run_ring(args, probe))
    except KeyboardInterrupt:
        reason = "Ctrl+C"
    except SystemExit as exc:
        reason = str(exc)
        print(reason)
    except Exception as exc:
        # e.g. START acknowledged but no Raw2 first packet within 3 s.
        reason = f"{type(exc).__name__}: {exc}"
        print(f"\n触摸板启动/运行失败：{reason}")
    finally:
        if probe.mouse is not None:
            probe.mouse.disable()
        probe.finish(reason)


def ask(prompt, default=""):
    try:
        value = input(prompt).replace("﻿", "").strip()
    except EOFError:
        raise SystemExit("输入已结束")
    return value or default


def choose_device(timeout):
    from ring_python_sdk.ble.control import scan_all_devices
    remembered = LAST_DEVICE.read_text(encoding="utf-8").strip() if LAST_DEVICE.is_file() else ""
    while True:
        found = asyncio.run(scan_all_devices(timeout))
        rings = [d for d in found if d.name and "ring" in d.name.casefold()]
        shown = rings or [d for d in found if d.name]
        print()
        if not shown:
            print("没有扫描到戒指。确认蓝牙已打开、戒指在附近，且 Mythlink 主程序已关闭。")
        elif not rings:
            print("没有名称含 Ring 的设备，下面列出所有有名称的设备：")
        for index, device in enumerate(shown, 1):
            rssi = f"{device.rssi} dBm" if getattr(device, "rssi", None) is not None else ""
            mark = "  ← 上次" if remembered and device.device.address == remembered else ""
            print(f"  [{index}] {device.name:<20} {device.device.address}  {rssi}{mark}")
        hint = "输入序号选择，R 重新扫描，也可直接输入名称/蓝牙地址"
        if remembered:
            hint += f"，回车使用上次（{remembered}）"
        answer = ask(hint + "：", remembered)
        if answer.lower() == "r" or not answer:
            continue
        if answer.isdigit() and 1 <= int(answer) <= len(shown):
            answer = shown[int(answer) - 1].device.address
        LAST_DEVICE.parent.mkdir(parents=True, exist_ok=True)
        LAST_DEVICE.write_text(answer, encoding="utf-8")
        return answer


def interactive(args):
    print("==== Raw2 双指测试 ====")
    print("请先关闭 Mythlink 主程序（它会占用戒指的蓝牙连接）。\n")
    from ring_python_sdk.touchpad.raw2_model import raw2_platform_supported
    if not raw2_platform_supported():
        raise SystemExit("当前环境不支持 Raw2：需要 Windows x64 并已安装 onnxruntime。")
    args.device = choose_device(args.connect_timeout)
    while True:
        seconds = ask(f"测试时长（秒，回车 = {args.seconds:.0f}）：", str(int(args.seconds)))
        try:
            args.seconds = float(seconds)
        except ValueError:
            pass
        args.scroll = ask("同时向前台窗口发送真实滚轮？先打开一个长网页。Esc 可随时停止（y/N）：", "n").lower() == "y"
        print()
        run_once(args)
        again = ask("\n回车再测一次，D 换戒指，Q 退出：", "").lower()
        if again == "q":
            return
        if again == "d":
            args.device = choose_device(args.connect_timeout)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--device", help="戒指名称、扫描序号或蓝牙地址（如 Ringo8F56）；不填则交互选择")
    parser.add_argument("--seconds", type=float, default=60.)
    parser.add_argument("--connect-timeout", type=float, default=8.)
    parser.add_argument("--scroll", action="store_true", help="Windows：同时向前台窗口发送真实滚轮")
    parser.add_argument("--scroll-gain", type=float, default=1., help="滚动倍率（产品默认 1）")
    parser.add_argument("--quiet-single", action="store_true", help="不打印单指移动片段")
    parser.add_argument("--demo", action="store_true", help="不连接戒指，用合成事件检查输出")
    args = parser.parse_args()
    if not (math.isfinite(args.seconds) and args.seconds > 0):
        parser.error("--seconds 必须为正数")
    try:
        if args.demo or args.device:
            run_once(args)
        else:
            interactive(args)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
