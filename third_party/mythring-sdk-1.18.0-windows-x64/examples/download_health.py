"""python examples/download_health.py <address-or-macOS-UUID> <output-directory>"""
import asyncio
from pathlib import Path
import sys

from mythring import Ring


async def main(address, output):
    folder = Path(output)
    folder.mkdir(parents=True, exist_ok=True)
    async with Ring(address) as ring:
        records = await ring.health.list_recordings()
        for record in records:
            if record.status not in (1, 2, 3):
                continue
            result = await ring.health.download_recording(record)
            path = folder / f"recording-{record.id}.rh"
            # Existing downloads are not silently overwritten.
            with path.open("xb") as target:
                target.write(result.data)
            print(path, len(result.blocks), "blocks")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    try:
        asyncio.run(main(sys.argv[1], sys.argv[2]))
    except KeyboardInterrupt:
        pass
