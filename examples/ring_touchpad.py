#!/usr/bin/env python3
"""SDK example: print typed touchpad events; macOS can also control its mouse."""
import argparse
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ring_python_sdk import RingSession
from ring_python_sdk.touchpad import TouchpadMove, TouchpadClick, TouchpadContact


async def run(args):
    mouse = None
    if args.mouse:
        from ring_python_sdk.touchpad.macos import MacSystemMouse
        mouse = MacSystemMouse()
        if not mouse.permitted():
            mouse.request_permission()
        mouse.enable()  # No cursor control until the caller explicitly enables it.
    session = RingSession(args.device, args.connect_timeout)
    session.battery_poll_enabled = False
    stopped = asyncio.Event()
    outcome = []
    def on_event(event):
        if mouse:mouse.handle(event)
        elif isinstance(event, TouchpadMove):
            if event.dx or event.dy:print(f'move {event.dx:.3f} {event.dy:.3f}')
        elif isinstance(event, TouchpadClick):print('left click')
        elif isinstance(event, TouchpadContact):print(f'contact {event.state} frame={event.step}')
    def on_stopped(error):
        outcome.append(error)
        if mouse:mouse.disable()
        stopped.set()
    try:
        if not await session.connect_target(args.device):
            raise RuntimeError('Could not connect to the selected ring')
        await session.touchpad_on(on_event=on_event, on_stopped=on_stopped,
                                  on_stats=lambda s: print(f'tokens={s.tokens} warmup={s.warmup_frames}/200 resets={s.resets}'),
                                  duration_s=args.seconds)
        await stopped.wait()
        if outcome and outcome[-1]:raise outcome[-1]
    finally:
        if mouse:mouse.disable()
        await session.disconnect()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', required=True, help='Ring name, scan index, Windows BLE address or macOS BLE UUID')
    parser.add_argument('--seconds', type=float, default=90.)
    parser.add_argument('--connect-timeout', type=float, default=8.)
    parser.add_argument('--mouse', action='store_true', help='Control the real macOS mouse; requires Accessibility permission')
    args = parser.parse_args()
    if args.seconds <= 0:parser.error('--seconds must be positive')
    if args.mouse and sys.platform != 'darwin':
        parser.error('--mouse 目前只支持 macOS；Windows 请运行不带 --mouse 的事件测试')
    try:asyncio.run(run(args))
    except KeyboardInterrupt:pass
