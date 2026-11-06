#!/usr/bin/env python3
"""Ringo gyro calibration utility for the SDK bundled with this delivery.

Uses the pinned SDK's internal serialized transport and protected STOP handshake.
This is a delivery utility, not a new public Ring API. No model code or weights.
"""
import argparse
import asyncio
import json
import struct


def decode_status(data, operation):
    if (len(data) != 18 or data[:2] != b'\x21\x08' or data[2] != operation
            or data[7] != 1 or data[4] > 2 or data[5] & ~7 or data[3] > 6):
        raise ValueError('Invalid or mismatched calibration response')
    count, fs, x, y, z = struct.unpack_from('<HHhhh', data, 8)
    return dict(operation=data[2], result=data[3], state=data[4],
                persisted=bool(data[5] & 1), correction_active=bool(data[5] & 2),
                owns_imu=bool(data[5] & 4), imu_model=data[6], stable_count=count,
                fs_dps=fs, bias_chip_counts=[x, y, z])


def completed(status):
    return (status['result'] == 0 and status['state'] == 2
            and status['persisted'] and status['correction_active'])


async def calibrate(ring, *, run=False, stationary=False, timeout=30,
                    interval=.5, emit=print):
    if run and not stationary:
        raise ValueError('Recalibration requires --stationary: ring off hand, still on a desk, not charging')
    if (ring.device_info.hardware_revision != 1
            or ring.device_info.firmware_version != '2.1.6-dev.touchpad.9.debug'):
        raise ValueError('This utility is validated for the bundled Ringo dev9 firmware only')
    # Reuse the bounded, idempotent STOP handshake; never start the model.
    await ring.touchpad._initial_stop()

    async def request(operation):
        value = await ring._transport.request(
            bytes([0x21, 0x07 if operation else 0x06]), (0x21, 0x08),
            lambda data: decode_status(data, operation))
        emit(value)
        return value

    before = await request(0)
    if not run:
        return before
    if before['state'] == 1:
        raise RuntimeError('Calibration is already running; query status instead of restarting')
    # RUN deletes the old persisted bias. Do not retry if its reply is lost.
    ack = await request(1)
    if ack['result'] != 0:
        raise RuntimeError(f"Calibration rejected: result={ack['result']}")
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(interval)
        status = await request(0)
        if completed(status):
            return status
        if status['result'] != 0:
            raise RuntimeError(f"Calibration failed: result={status['result']}")
    raise TimeoutError('Calibration incomplete. Old bias was cleared; keep ring still and query again. No automatic rollback or retry.')


async def execute(args):
    from ring_mac_sdk import Ring, scan
    candidates = [d for d in await scan(8) if d.identifier == args.identifier]
    if len(candidates) != 1 or candidates[0].name != args.name or not args.name.startswith('Ringo'):
        raise ValueError('No unique matching Ringo name and identifier; scan again, never select by signal strength')
    async with Ring(candidates[0]) as ring:
        await calibrate(ring, run=args.recalibrate, stationary=args.stationary,
                        emit=lambda value: print(json.dumps(value, ensure_ascii=False), flush=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('identifier', help='Exact identifier returned by ring-mac scan')
    parser.add_argument('--name', required=True, help='Exact advertised name, e.g. RingoA4BA')
    parser.add_argument('--recalibrate', action='store_true', help='Clear old gyro bias and recalibrate once')
    parser.add_argument('--stationary', action='store_true', help='Confirm ring is off hand, stationary on desk, not charging')
    args = parser.parse_args()
    if args.recalibrate and not args.stationary:
        parser.error('--recalibrate requires --stationary')
    try:
        asyncio.run(execute(args))
    except KeyboardInterrupt:
        parser.exit(130, 'Interrupted; calibration may continue on the ring. Query before use.\n')
    except Exception as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
