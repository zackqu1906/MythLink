"""python examples/gestures.py <CoreBluetooth UUID>"""
import asyncio
import sys
from ring_mac_sdk import Ring, GestureMode, WearingHand, SwipeDirection


async def main(identifier):
    async with Ring(identifier, on_gesture=print) as ring:
        print(ring.device_info)
        print("battery", await ring.battery_level())
        if ring.device_info.gesture_settings_available:
            print(await ring.gesture.read_hand())
            print(await ring.gesture.read_swipe_direction())
        # Optional explicit configuration:
        # await ring.gesture.set_hand(WearingHand.LEFT)
        # await ring.gesture.set_swipe_direction(SwipeDirection.REVERSED)
        print(await ring.gesture.set_mode(GestureMode.NORMAL))
        try:
            await asyncio.sleep(60)
        finally:
            if ring.link_state == "CONNECTED":
                await ring.gesture.set_mode(GestureMode.OFF)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
