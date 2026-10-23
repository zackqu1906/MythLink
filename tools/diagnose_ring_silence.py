"""Isolate digital-silence failures with bounded, local-only MIC captures.

Disconnect the product UI first. No ASR, input method, pointer delivery, firmware
updates are used. Device reboot is only used with --reboot. Each stage reports
scalar PCM evidence.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import struct
import time

from ring_python_sdk.session import RingSession


async def run(args):
    args.output.mkdir(parents=True, exist_ok=True)
    rows, phase = [], ['connecting']
    session = RingSession('Ringo', timeout_s=8, data_root=args.output,
                          auto_reconnect=False, battery_poll_enabled=False)
    def pcm(sequence, data):
        values = struct.unpack(f'<{len(data)//2}h', data)
        rows.append(dict(phase=phase[0], elapsed=round(time.monotonic()-started, 3),
                         sequence=sequence, samples=len(values),
                         nonzero=sum(v != 0 for v in values), peak=max(map(abs, values), default=0)))

    async def observe(label):
        phase[0] = label
        print('PHASE ' + label, flush=True)
        before = len(rows)
        await asyncio.sleep(args.seconds)
        subset = rows[before:]
        print('RESULT ' + json.dumps(dict(phase=label, blocks=len(subset),
              samples=sum(r['samples'] for r in subset), nonzero=sum(r['nonzero'] for r in subset),
              zero_blocks=sum(r['nonzero'] == 0 for r in subset),
              tail_nonzero=sum(r['nonzero'] for r in subset[-20:])), ensure_ascii=False), flush=True)

    started = time.monotonic()
    try:
        if not await session.connect_target(args.selector):
            raise RuntimeError('Ring not found')
        await session.query_info()
        await asyncio.sleep(1)
        print('DEVICE ' + str(session.device_info), flush=True)
        if args.reboot:
            time_before = await session.query_time()
            print('TIME_BEFORE ' + str(time_before), flush=True)
            await session.mic_on('adpcm', on_pcm=pcm)
            await observe('before_reboot_request')
            await session.mic_off()
            await session.reboot()
            for _ in range(30):
                await asyncio.sleep(.1)
                if session.client is None or not session.client.is_connected:
                    break
            print(f'REBOOT ok={session.reboot_last_ok} err={session.reboot_last_err}', flush=True)
            await session.disconnect()
            await asyncio.sleep(3)
            if not await session.connect_target(args.selector):
                raise RuntimeError('Ring did not reconnect after reboot')
            time_after = await session.query_time()
            print('TIME_AFTER ' + str(time_after), flush=True)
            confirmed = bool(time_before and time_after
                             and time_after.uptime_ms < time_before.uptime_ms)
            print('REBOOT_CONFIRMED ' + str(confirmed), flush=True)
            await asyncio.sleep(1)
            await session.mic_on('adpcm', on_pcm=pcm)
            await observe('after_reboot_request')
            for cycle in range(args.cycles):
                await session.mic_off(); await asyncio.sleep(.75)
                await session.mic_on('adpcm', on_pcm=pcm)
                await observe(f'after_request_mic_restart_{cycle + 1}')
            return
        if args.codecs:
            for codec in ('adpcm', 'pcm', 'adpcm'):
                await session.mic_on(codec, on_pcm=pcm)
                await observe('codec_' + codec)
                await session.mic_off(); await asyncio.sleep(.75)
            return
        await session.mic_on('adpcm', on_pcm=pcm)
        await observe('mic_only_default')
        await session.mic_off(); await asyncio.sleep(.75)
        await session.mic_on('adpcm', on_pcm=pcm)
        await observe('mic_restart_default')
        await session.swipe_on(print_triggers=False, print_profile=False)
        await observe('mic_with_gestures')
        await session.quaternion_on(on_frame=lambda _: None)
        await observe('mic_with_gestures_and_quaternion')
        await session.quaternion_off(); await session.swipe_off()
        await session.mic_off(); await asyncio.sleep(.75)
        await session.mic_on('adpcm', hardware_gain_db=0, software_gain_db=0, on_pcm=pcm)
        await observe('mic_explicit_zero_db')
        await session.mic_off(); await asyncio.sleep(.75)
        await session.mic_on('adpcm', on_pcm=pcm)
        await observe('mic_restored_default')
        for cycle in range(args.cycles):
            await session.mic_off(); await asyncio.sleep(.75)
            await session.mic_on('adpcm', on_pcm=pcm)
            await observe(f'repeated_start_{cycle + 1}')
    finally:
        await session.disconnect()
        (args.output/'blocks.json').write_text(json.dumps(rows, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selector', required=True)
    parser.add_argument('--seconds', type=float, default=6)
    parser.add_argument('--cycles', type=int, default=0)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--codecs', action='store_true')
    modes.add_argument('--reboot', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 2 <= args.seconds <= 30: parser.error('--seconds must be 2..30')
    if not 0 <= args.cycles <= 20: parser.error('--cycles must be 0..20')
    asyncio.run(run(args))
