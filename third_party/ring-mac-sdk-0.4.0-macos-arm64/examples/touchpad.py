"""python examples/touchpad.py <CoreBluetooth UUID>

Prints events; applications decide how to map them to their UI.
"""
import asyncio
import sys
from ring_mac_sdk import Click, ContactUpdate, Motion, Ring


def event_received(event):
    if isinstance(event, Motion):
        # dx/dy are already mapped to logical pixels; mode may be UNKNOWN.
        print("motion", event.mode, event.dx, event.dy)
    elif isinstance(event, ContactUpdate):
        print("contact", event.episode_id, event.phase, event.mode)
    elif isinstance(event, Click):
        # Preview is visual feedback only. Dispatch an action only when final.
        print("preview" if event.is_preview else "click", event.click_id, event.button, event.count)


async def main(identifier):
    async with Ring(identifier, on_touchpad=event_received, on_touchpad_status=print) as ring:
        await ring.touchpad.start()  # returns after first valid inference
        await asyncio.sleep(60)
        await ring.touchpad.stop()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
