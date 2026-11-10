"""Record SDK frames/events for labelled dwell calibration; never post OS input."""
import argparse
import asyncio
from dataclasses import asdict
import json
from queue import SimpleQueue, Empty
from pathlib import Path
import time

from ring_mac_sdk import Ring
from ring_mac_sdk.interaction import delta


async def collect(args):
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    control = output / 'control.json'
    control.write_text('{"phase": "waiting"}\n')
    rows = SimpleQueue()
    phase = 'waiting'
    def record(kind, **fields):
        rows.put(dict(type=kind, received_ns=time.monotonic_ns(), label=phase, **fields))
    def drain():
        pending = []
        while True:
            try:
                pending.append(rows.get_nowait())
            except Empty:
                return pending
    def event(value):
        record(type(value).__name__, **asdict(value))
    def status(value):
        record('status', **asdict(value))
        print(json.dumps(dict(type='status', **asdict(value)), ensure_ascii=False), flush=True)
    ring = Ring(args.identifier, on_touchpad=event, on_touchpad_status=status,
                on_touchpad_diagnostics=lambda value: record('diagnostics', **asdict(value)))
    try:
        async with ring:
            record('device', **asdict(ring.device_info))
            await ring.touchpad.start()
            engine = ring.touchpad.engine
            recognize = engine.recognize
            def measured(recognition, now):
                recognize(recognition, now)
                dx, dy = delta(recognition.x, recognition.y)
                record('frame', **asdict(recognition), dx=dx, dy=dy,
                       session_id=ring.touchpad.session, generation=ring.touchpad.generation,
                       episode_id=engine.active.id if engine.active else None,
                       mode=engine.mode(engine.active) if engine.active else None)
            engine.recognize = measured
            print(json.dumps(dict(type='ready', control=str(control)), ensure_ascii=False), flush=True)
            deadline = time.monotonic() + args.seconds
            with (output / 'samples.jsonl').open('w') as handle:
                while time.monotonic() < deadline:
                    try:
                        requested = json.loads(control.read_text())['phase']
                    except (ValueError, KeyError):
                        requested = phase
                    if requested != phase:
                        phase = requested
                        record('phase_start')
                        print(json.dumps(dict(type='phase', phase=phase)), flush=True)
                    pending = drain()
                    handle.writelines(json.dumps(row, ensure_ascii=False)+'\n' for row in pending)
                    handle.flush()
                    if phase == 'stop':
                        break
                    if ring.touchpad.status.stage == 'FAILED' or ring.link_state != 'CONNECTED':
                        raise RuntimeError('SDK stream failed or disconnected')
                    await asyncio.sleep(.1)
                await ring.touchpad.stop()
    finally:
        with (output / 'samples.jsonl').open('a') as handle:
            handle.writelines(json.dumps(row, ensure_ascii=False)+'\n' for row in drain())
        print(json.dumps(dict(type='saved', path=str(output / 'samples.jsonl'))), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('identifier')
    parser.add_argument('--output', required=True)
    parser.add_argument('--seconds', type=float, default=240)
    args = parser.parse_args()
    asyncio.run(collect(args))


if __name__ == '__main__':
    main()
