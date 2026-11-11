"""python examples/touchpad.py <address-or-macOS-UUID> [--seconds 30]

输出原模型推理结果；不操作系统光标、键盘或鼠标。
"""
import argparse
import asyncio
import time

from mythring import Click, ContactUpdate, Motion, Ring


async def main(args):
    last_motion_log = 0.0

    def received(event):
        nonlocal last_motion_log
        if isinstance(event, Motion):
            # 模型输出为逻辑像素。这里只限制打印频率，不改变业务事件。
            now = time.monotonic()
            if now - last_motion_log >= 0.2:
                print("运动", event.mode.value, event.dx, event.dy)
                last_motion_log = now
        elif isinstance(event, ContactUpdate):
            print("接触", event.episode_id, event.phase.value)
        elif isinstance(event, Click) and not event.is_preview:
            # preview 和 final 共享 click_id，实际操作只消费 final。
            print("点击", event.click_id, event.button.value, event.count)

    async with Ring(args.address) as ring:
        ring.on("touchpad_event", received)
        ring.on("error", lambda error: print("错误:", error))
        await ring.start_touchpad()
        print(f"已启动触摸板，{args.seconds:g} 秒后停止。Ctrl+C 可提前退出。")
        try:
            await asyncio.wait_for(ring.wait_disconnected(), timeout=args.seconds)
        except asyncio.TimeoutError:
            pass
        finally:
            if ring.is_connected:
                await ring.stop_touchpad()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("address", help="本机扫描返回的蓝牙地址或 macOS UUID")
    parser.add_argument("--seconds", type=float, default=30, help="运行时间，默认 30 秒")
    options = parser.parse_args()
    if options.seconds <= 0:
        parser.error("--seconds 必须大于 0")
    try:
        asyncio.run(main(options))
    except KeyboardInterrupt:
        pass
