"""Print SDK events without moving the system cursor."""
import argparse
import asyncio
from dataclasses import asdict
import json

from ring_mac_sdk import Ring, scan


def show(event):
    print(json.dumps(dict(type=type(event).__name__, **asdict(event)), ensure_ascii=False))


async def main(identifier):
    devices = [d for d in await scan(8) if d.identifier == identifier]
    if len(devices) != 1:
        raise RuntimeError('Target not found; run ring-mac scan and select your ring explicitly')
    async with Ring(devices[0], on_touchpad=show, on_touchpad_status=show) as ring:
        print(ring.device_info)
        try:
            await ring.touchpad.start()
            for _ in range(120):
                if ring.touchpad.status.stage == 'FAILED':
                    raise RuntimeError(ring.touchpad.status.message)
                await asyncio.sleep(1)
        finally:
            await ring.touchpad.stop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('identifier')
    try:
        asyncio.run(main(parser.parse_args().identifier))
    except KeyboardInterrupt:
        pass
