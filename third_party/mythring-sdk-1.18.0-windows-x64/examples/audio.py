"""Explicit Ringo audio capture; count PCM samples without saving or playing them."""
import argparse
import asyncio

from mythring import Ring


async def main(address: str, seconds: float) -> None:
    async with Ring(address) as ring:
        support = ring.feature_support("audio")
        print(support.to_dict())
        if not ring.capabilities.get("audio"):
            return
        samples = 0

        def on_frame(frame):
            nonlocal samples
            samples += frame.sample_count

        ring.on("audio_frame", on_frame)
        await ring.start_audio()
        try:
            await asyncio.sleep(seconds)
        finally:
            if ring.is_connected:
                await ring.stop_audio()
        print(f"Received {samples} mono PCM16 samples ({samples / 16000:.2f} seconds).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("address", help="BLE address from this PC or a macOS UUID")
    parser.add_argument("--seconds", type=float, default=10)
    args = parser.parse_args()
    if not 0 < args.seconds <= 600:
        parser.error("--seconds must be between 0 and 600")
    asyncio.run(main(args.address, args.seconds))
