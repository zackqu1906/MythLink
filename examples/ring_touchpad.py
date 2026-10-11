#!/usr/bin/env python3
"""SDK example: typed events, or explicit Windows/macOS mouse output (90s default)."""
import argparse
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ring_python_sdk import RingSession
from ring_python_sdk.touchpad import TouchpadMove, TouchpadClick


async def run(args):
    mouse = None
    if args.mouse:
        if sys.platform == 'win32':
            from ring_python_sdk.touchpad.windows import WindowsSystemMouse
            mouse = WindowsSystemMouse()
        elif sys.platform == 'darwin':
            from ring_python_sdk.touchpad.macos import MacSystemMouse
            mouse = MacSystemMouse()
            if not mouse.permitted():
                mouse.request_permission()
        else:
            raise RuntimeError('System mouse output supports Windows and macOS')
        mouse.enable()  # No cursor control until the caller explicitly enables it.
    session = RingSession(args.device, args.connect_timeout)
    session.battery_poll_enabled = False
    stopped = asyncio.Event()
    outcome = []
    def on_event(event):
        if mouse:
            if sys.platform != 'win32' or isinstance(event, TouchpadClick): mouse.handle(event)
        elif isinstance(event, TouchpadMove):
            if event.dx or event.dy:print(f'move {event.dx:.3f} {event.dy:.3f}')
        elif sys.platform != 'win32' or isinstance(event, TouchpadClick): print('left click')
    def on_stopped(error):
        outcome.append(error)
        if mouse:mouse.disable()
        stopped.set()
    try:
        if not await session.connect_target(args.device):
            raise RuntimeError('Could not connect to the selected ring')
        pointer_options = dict(on_pointer_move=mouse.handle) if mouse and sys.platform == 'win32' else {}
        await session.touchpad_on(on_event=on_event, on_stopped=on_stopped, **pointer_options,
                                  on_stats=lambda s: print(f'tokens={s.tokens} warmup={s.warmup_frames}/200 resets={s.resets}'),
                                  duration_s=args.seconds)
        await stopped.wait()
        if outcome and outcome[-1]:raise outcome[-1]
    finally:
        if mouse:mouse.disable()
        await session.disconnect()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', required=True, help='Ring name, scan index, Windows address or macOS BLE UUID')
    parser.add_argument('--seconds', type=float, default=90.)
    parser.add_argument('--connect-timeout', type=float, default=8.)
    parser.add_argument('--mouse', action='store_true', help='Control the Windows/macOS mouse; macOS requires Accessibility permission')
    args = parser.parse_args()
    if args.seconds <= 0:parser.error('--seconds must be positive')
    try:asyncio.run(run(args))
    except KeyboardInterrupt:pass
