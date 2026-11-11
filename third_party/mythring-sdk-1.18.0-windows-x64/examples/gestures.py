"""python examples/gestures.py <address-or-macOS-UUID>"""
import asyncio
import sys

from mythring import GestureMode, Ring


async def main(address):
    async with Ring(address) as ring:
        ring.on("gesture", print)
        print("固件", ring.device_info.firmware_version)
        print("电量", await ring.get_battery_level())
        await ring.gesture.set_mode(GestureMode.NORMAL)
        try:
            await ring.wait_disconnected()
        finally:
            if ring.is_connected:
                await ring.gesture.set_mode(GestureMode.OFF)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    try:
        asyncio.run(main(sys.argv[1]))
    except KeyboardInterrupt:
        pass
