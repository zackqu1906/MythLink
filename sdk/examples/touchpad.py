"""Receive SDK events without any application or system-input integration."""
import argparse
import asyncio
import multiprocessing

from ring_python_sdk import RingSession


async def run(args):
    ring = RingSession('Ringo', 8, connection_backend='process', auto_reconnect=False)
    counts = {'frames': 0, 'pointer': 0}
    def frame(_event):
        counts['frames'] += 1
    def pointer(_event):
        counts['pointer'] += 1
    try:
        if not await ring.connect_target(args.device):
            raise RuntimeError('Ring not found')
        await ring.touchpad_on(protocol='raw2', on_event=frame, on_pointer_move=pointer,
                               on_stopped=lambda error: print('Touchpad stopped:', error), duration_s=None)
        for _ in range(args.seconds):
            await asyncio.sleep(1)
            print(counts, ring.process_diagnostics)
    finally:
        await ring.disconnect()


if __name__ == '__main__':
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', required=True)
    parser.add_argument('--seconds', type=int, default=30)
    asyncio.run(run(parser.parse_args()))
