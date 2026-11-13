"""Confirmed firmware failures recover without reconnecting or replaying input."""
import asyncio
import struct

import pytest

from ring_python_sdk.sdk_provider import RingError, protocol as p
from ring_python_sdk.touchpad import raw2
from ring_python_sdk.touchpad.events import Raw2Reset
from test_ring_mac_sdk_delivery import status, token
from test_touchpad_raw2_integration import enable_gestures, shared_session, until


def device_status(state, result, *, deadline=0):
    packet = bytearray(status(state))
    struct.pack_into('<i', packet, 4, result)
    struct.pack_into('<I', packet, 60, deadline)
    return packet


@pytest.fixture(autouse=True)
def short_delays(monkeypatch):
    monkeypatch.setattr(raw2, 'RAW2_BUSY_RETRY_DELAYS', (.01, .01))
    monkeypatch.setattr(raw2, 'RAW2_RECOVERY_DELAY_S', .01)


@pytest.mark.parametrize('refusals', [1, 3])
def test_busy_start_retries_only_a_bounded_number_of_confirmed_stops(monkeypatch, tmp_path, refusals):
    async def run():
        session, client = await shared_session(monkeypatch, tmp_path)
        original = client.write_gatt_char
        frames = []
        async def write(uuid, command, **kwargs):
            if command == p.START and client.commands.count(p.START) < refusals:
                client.commands.append(command)
                client.callback(None, device_status(0, -16))
            else:
                await original(uuid, command, **kwargs)
        client.write_gatt_char = write
        if refusals == 1:
            await session.touchpad_on(protocol='raw2', on_event=frames.append)
            await until(lambda: frames)
            assert session.touchpad.receiver.status.stage == 'RUNNING'
            assert len([e for e in frames if isinstance(e, Raw2Reset)]) == 1
            await session.touchpad_off()
        else:
            with pytest.raises(RingError, match='戒指暂忙') as error:
                await session.touchpad_on(protocol='raw2', on_event=frames.append)
            assert error.value.device_code == -16 and error.value.response.state == 0
            assert error.value.phase == 'ack' and not frames
        attempts = min(refusals + 1, 3)
        assert client.commands == [p.STOP, p.START, p.STOP] * attempts
        assert client.is_connected and session.touchpad is None
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['running', 'deadline', 'other_code', 'ack_timeout', 'cleanup', 'connection', 'tokens'])
def test_ambiguous_or_unsafe_start_failures_are_not_retried(monkeypatch, tmp_path, failure):
    async def run():
        session, client = await shared_session(monkeypatch, tmp_path)
        original = client.write_gatt_char
        request = session._sdk_transport.request
        async def write(uuid, command, **kwargs):
            if command == p.START:
                client.commands.append(command)
                client.callback(None, device_status(1 if failure == 'running' else 0,
                    -5 if failure == 'other_code' else -16, deadline=int(failure == 'deadline')))
                if failure == 'tokens':
                    client.callback(None, token())
            elif command == p.STOP and p.START in client.commands:
                client.commands.append(command)
                client.callback(None, device_status(0, -16 if failure == 'cleanup' else 0))
                if failure == 'connection':
                    session._sdk_transport.generation += 1
            else:
                await original(uuid, command, **kwargs)
        async def timed_request(command, *args, **kwargs):
            if command == p.START and failure == 'ack_timeout':
                client.commands.append(command)
                raise RingError('timeout', 'START acknowledgement missing', phase='ack')
            return await request(command, *args, **kwargs)
        client.write_gatt_char = write
        session._sdk_transport.request = timed_request
        with pytest.raises(RingError):
            await session.touchpad_on(protocol='raw2', on_event=lambda _: None)
        assert client.commands.count(p.START) == 1
        assert not session.touchpad_active
    asyncio.run(run())


@pytest.mark.parametrize('ending', ['stop', 'duration'])
def test_stop_or_duration_while_recovery_waits_for_first_packet(monkeypatch, tmp_path, ending):
    async def run():
        session, client = await shared_session(monkeypatch, tmp_path)
        stopped, frames = [], []
        await session.touchpad_on(protocol='raw2', on_event=frames.append, on_stopped=stopped.append,
                                  duration_s=.15 if ending == 'duration' else None)
        stream = session.touchpad
        await until(lambda: frames)
        install_compute_fault(client, restart_tokens=False)
        await stream.receiver._inspect_device()
        await until(lambda: client.commands.count(p.START) == 2)
        if ending == 'stop':
            await asyncio.wait_for(session.touchpad_off(), .5)
        await until(lambda: stopped)
        assert stopped == [None] and session.touchpad is None
        assert client.commands.count(p.START) == 2 and client.is_connected
    asyncio.run(run())


@pytest.mark.parametrize('cancel', [False, True])
def test_stop_or_cancel_during_busy_backoff_prevents_another_start(monkeypatch, tmp_path, cancel):
    monkeypatch.setattr(raw2, 'RAW2_BUSY_RETRY_DELAYS', (10., 10.))
    async def run():
        session, client = await shared_session(monkeypatch, tmp_path)
        original = client.write_gatt_char
        async def write(uuid, command, **kwargs):
            if command == p.START:
                client.commands.append(command)
                client.callback(None, device_status(0, -16))
            else:
                await original(uuid, command, **kwargs)
        client.write_gatt_char = write
        task = asyncio.create_task(session.touchpad_on(protocol='raw2', on_event=lambda _: None))
        await until(lambda: client.commands.count(p.STOP) == 2 and session.touchpad.receiver.task.done())
        if cancel:
            task.cancel()
        else:
            await asyncio.wait_for(session.touchpad_off(), .5)
        with pytest.raises((RingError, asyncio.CancelledError)):
            await task
        assert client.commands == [p.STOP, p.START, p.STOP]
        assert session.touchpad is None and client.is_connected
    asyncio.run(run())


def install_compute_fault(client, *, kind=5, cleanup=True, restart_tokens=True):
    original = client.write_gatt_char
    async def write(uuid, command, **kwargs):
        if command == p.STATUS:
            client.commands.append(command)
            client.callback(None, device_status(2, -34))
        elif command == b'\x3c\x04':
            client.commands.append(command)
            client.callback(None, b'\x3c\x12\x02\x00' + struct.pack('<15I', 42, *([0]*13), kind))
        elif command == p.STOP and not cleanup:
            client.commands.append(command)
            client.callback(None, device_status(2, -16))
        elif command == p.START and not restart_tokens:
            client.commands.append(command)
            client.callback(None, status())
        else:
            await original(uuid, command, **kwargs)
    client.write_gatt_char = write


def test_compute_fault_resets_old_input_recovers_once_and_preserves_mic_gestures(monkeypatch, tmp_path):
    from test_public_sdk_integration import compact
    async def run():
        session, client = await shared_session(monkeypatch, tmp_path)
        frames, stopped, gestures = [], [], []
        processor = enable_gestures(session, tmp_path, gestures.append)
        session.mic_active = True
        try:
            await session.touchpad_on(protocol='raw2', on_event=frames.append, on_stopped=stopped.append, duration_s=None)
            stream = session.touchpad
            await until(lambda: frames)
            old_session = stream.receiver.session
            install_compute_fault(client)
            await stream.receiver._inspect_device()
            await until(lambda: stream.receiver.session != old_session and stream.receiver.status.stage == 'RUNNING')
            await until(lambda: len([e for e in frames if isinstance(e, Raw2Reset)]) == 3)
            assert [e.session_id for e in frames if isinstance(e, Raw2Reset)] == [
                old_session, old_session, stream.receiver.session]
            assert not stopped and stream.error is None
            client.callback(None, compact())
            assert gestures and session.mic_active and session.swipe is processor
            assert not any(command[:1] in (b'\x20', b'\x26') for command in client.commands)
            # A second firmware fault must end the stream, not start a recovery loop.
            await stream.receiver._inspect_device()
            await until(lambda: stopped)
            assert client.commands.count(p.START) == 2
            assert '戒指触控计算异常' in str(stopped[0])
            assert stopped[0].device_code == -34 and stopped[0].response['fault_kind'] == 5
            assert session.touchpad is None and client.is_connected and session.mic_active
        finally:
            await session.touchpad_off()
            processor.close()
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['other_fault', 'cleanup', 'no_tokens', 'stop'])
def test_failed_or_cancelled_compute_recovery_stays_stopped(monkeypatch, tmp_path, failure):
    monkeypatch.setattr(raw2, 'RAW2_RECOVERY_TIMEOUT_S', .05)
    if failure == 'stop':
        monkeypatch.setattr(raw2, 'RAW2_RECOVERY_DELAY_S', 10.)
    async def run():
        session, client = await shared_session(monkeypatch, tmp_path)
        stopped, frames = [], []
        await session.touchpad_on(protocol='raw2', on_event=frames.append, on_stopped=stopped.append, duration_s=None)
        stream = session.touchpad
        await until(lambda: frames)
        install_compute_fault(client, kind=6 if failure == 'other_fault' else 5,
                              cleanup=failure != 'cleanup', restart_tokens=failure != 'no_tokens')
        await stream.receiver._inspect_device()
        if failure == 'stop':
            await until(lambda: len([e for e in frames if isinstance(e, Raw2Reset)]) == 2)
            await asyncio.wait_for(session.touchpad_off(), .5)
        await until(lambda: stopped)
        assert client.commands.count(p.START) == (2 if failure == 'no_tokens' else 1)
        assert session.touchpad is None and not session.touchpad_active and client.is_connected
        if failure == 'no_tokens':
            assert stopped[0].reason == 'input_timeout' and '恢复超时' in str(stopped[0])
    asyncio.run(run())
