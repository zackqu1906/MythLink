"""python examples/raw_touchpad.py <address-or-macOS-UUID>"""
import asyncio
import sys

from mythring import Ring


async def main(address):
    async with Ring(address) as ring:
        ring.on("touchpad_tokens", print)
        ring.on("error", lambda error: print("错误:", error))
        await ring.start_touchpad_raw()
        print("正在接收 Raw2 特征。需要直接的运动/点击结果时运行 touchpad.py。Ctrl+C 退出。")
        try:
            await ring.wait_disconnected()
        finally:
            if ring.is_connected:
                await ring.stop_touchpad()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    try:
        asyncio.run(main(sys.argv[1]))
    except KeyboardInterrupt:
        pass
