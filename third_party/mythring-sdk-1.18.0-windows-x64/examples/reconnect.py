"""python examples/reconnect.py <address-or-macOS-UUID>

Reconnect only this explicitly selected device until Ctrl+C. Unlike the Android
foreground demo this may establish a new system BLE link, not just a data channel.
"""
import asyncio
import sys

from mythring import Ring


async def main(address):
    ring = Ring(address)
    delays = (1, 2, 5, 10, 30)
    attempt = 0
    try:
        while True:
            try:
                await ring.connect()
                attempt = 0
                print("已连接", ring.device_info.firmware_version)
                await ring.wait_disconnected()
            except Exception as error:
                print("连接失败:", error)
            finally:
                await ring.disconnect()
            delay = delays[min(attempt, len(delays) - 1)]
            attempt += 1
            print(f"{delay} 秒后重连；Ctrl+C 停止。")
            await asyncio.sleep(delay)
    finally:
        await ring.disconnect()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    try:
        asyncio.run(main(sys.argv[1]))
    except KeyboardInterrupt:
        pass
