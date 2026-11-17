import asyncio
import os
import time

import pytest

from ring_python_sdk import RingSession
from ring_python_sdk.sdk_provider import protocol as p
from ring_python_sdk.session.sdk_transport import connect_session
from process_sdk_fakes import Client, CrashModel, Model


async def until(predicate, timeout=5.):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(.01)


async def connect(tmp_path, target='offline', model=Model):
    session = RingSession('offline', 1, data_root=tmp_path, connection_backend='process')
    session.session_dir = tmp_path
    session._process_model_factory = model
    await connect_session(session, target, client_factory=Client)
    return session


def test_processes_keep_receiving_and_computing_while_application_loop_is_blocked(tmp_path):
    async def run():
        session = await connect(tmp_path)
        transport = session._sdk_transport
        frames, pointer, stopped = [], [], []
        try:
            await session.touchpad_on(protocol='raw2', on_event=frames.append,
                                      on_pointer_move=pointer.append, on_stopped=stopped.append,
                                      duration_s=None)
            await until(lambda: frames and pointer)
            before = transport.runtime.diagnostics
            assert len({os.getpid(), before['ble_pid'], before['touchpad_pid']}) == 3
            time.sleep(.3)  # Deliberately freeze the consumer's entire asyncio loop.
            after = transport.runtime.diagnostics
            assert after['raw2_packets']-before['raw2_packets'] >= 15
            assert after['computed_tokens']-before['computed_tokens'] >= 15
            assert not after['dropped_raw2_packets']
            await session.touchpad_off()
            assert stopped == [None]
            assert transport.connected
        finally:
            await session.disconnect()
        assert not transport.runtime.io_process.is_alive()
        assert not transport.runtime.touch_process.is_alive()
    asyncio.run(run())


def test_device_tx_fault_stops_without_automatic_restart(tmp_path):
    async def run():
        session = await connect(tmp_path)
        stopped = []
        try:
            await session.touchpad_on(protocol='raw2', on_event=lambda _: None,
                                      on_stopped=stopped.append, duration_s=None)
            await session._sdk_transport.send(b'\xff\x01')
            await until(lambda: stopped)
            assert stopped[0].response['fault_kind'] == 3
            assert stopped[0].device_code == -116
            assert await session._sdk_transport.request(p.BATTERY, (0x29, 2), p.battery) == 1
            assert session.client.is_connected
        finally:
            await session.disconnect()
    asyncio.run(run())


def test_audio_and_gestures_survive_repeated_touchpad_start_stop(tmp_path):
    async def run():
        session = await connect(tmp_path)
        pcm, gestures = [], []
        try:
            await session.mic_on('adpcm', on_pcm=lambda *args: pcm.append(args))
            await session.swipe_on(on_trigger=gestures.append, print_triggers=False)
            await until(lambda: pcm and gestures)
            for _ in range(2):
                await session.touchpad_on(protocol='raw2', on_event=lambda _: None, duration_s=None)
                await session.touchpad_off()
            before = len(pcm)
            await until(lambda: len(pcm) > before)
            assert session.mic_active and session.swipe_active and session.client.is_connected
            assert await session._sdk_transport.request(p.BATTERY, (0x29, 2), p.battery) == 2
        finally:
            await session.disconnect()
    asyncio.run(run())


def test_stop_interrupts_start_waiting_for_first_packet(tmp_path):
    async def run():
        session = await connect(tmp_path, target='no_tokens')
        try:
            start = asyncio.create_task(session.touchpad_on(protocol='raw2', on_event=lambda _: None))
            await until(lambda: session.touchpad is not None)
            # A control query confirms START has reached the device.
            async def started():
                while await session._sdk_transport.request(p.BATTERY, (0x29, 2), p.battery) != 1:
                    await asyncio.sleep(.01)
            await asyncio.wait_for(started(), 3.)
            await asyncio.wait_for(session.touchpad_off(), 3.)
            with pytest.raises(Exception) as error:
                await start
            assert error.value.reason == 'cancelled'
            assert not session.touchpad_active and session.client.is_connected
        finally:
            await session.disconnect()
    asyncio.run(run())


def test_control_transaction_reserves_device_across_processes(tmp_path):
    async def run():
        session = await connect(tmp_path)
        transport = session._sdk_transport
        try:
            async with transport.control_transaction():
                start = asyncio.create_task(session.touchpad_on(protocol='raw2', on_event=lambda _: None))
                await asyncio.sleep(.1)
                assert await transport.request(p.BATTERY, (0x29, 2), p.battery) == 0
            await asyncio.wait_for(start, 3.)
            assert await transport.request(p.BATTERY, (0x29, 2), p.battery) == 1
        finally:
            await session.disconnect()
    asyncio.run(run())


def test_cancel_connect_reaps_both_workers(tmp_path):
    async def run():
        session = RingSession('offline', 1, data_root=tmp_path, connection_backend='process')
        marker = tmp_path/'connecting'
        start = asyncio.create_task(connect_session(session, f'wait_connect:{marker}', client_factory=Client))
        await until(marker.exists)
        transport = session._sdk_transport
        start.cancel()
        with pytest.raises(asyncio.CancelledError):
            await start
        assert not transport.runtime.io_process.is_alive()
        assert not transport.runtime.touch_process.is_alive()
        await session.disconnect()
    asyncio.run(run())


def test_failure_to_spawn_second_worker_reaps_first(monkeypatch, tmp_path):
    from ring_python_sdk.process import ProcessTransport

    async def run():
        session = RingSession('offline', 1, data_root=tmp_path, connection_backend='process')
        transport = ProcessTransport(session, 'offline', client_factory=Client)
        session._sdk_transport = transport
        def fail():
            raise RuntimeError('cannot spawn touchpad worker')
        monkeypatch.setattr(transport.runtime.touch_process, 'start', fail)
        with pytest.raises(RuntimeError, match='cannot spawn'):
            await transport.connect()
        assert not transport.runtime.io_process.is_alive()
        assert transport.runtime.touch_process.pid is None
    asyncio.run(run())


def test_model_process_exit_does_not_kill_ble_connection(tmp_path):
    async def run():
        session = await connect(tmp_path, model=CrashModel)
        try:
            with pytest.raises(Exception) as error:
                await session.touchpad_on(protocol='raw2', on_event=lambda _: None)
            assert error.value.reason == 'worker_failed'
            assert session.client.is_connected
            assert await session._sdk_transport.request(p.BATTERY, (0x29, 2), p.battery) == 1
        finally:
            await session.disconnect()
    asyncio.run(run())


def test_io_process_exit_marks_session_disconnected(tmp_path):
    async def run():
        session = await connect(tmp_path)
        transport = session._sdk_transport
        try:
            with pytest.raises(Exception) as error:
                await transport.send(b'\xff\x03')
            assert error.value.reason == 'worker_failed'
            assert not session.client.is_connected
        finally:
            await session.disconnect()
        assert not transport.runtime.touch_process.is_alive()
    asyncio.run(run())


def test_real_paired_model_runs_in_worker(tmp_path):
    async def run():
        session = await connect(tmp_path, model=None)
        frames = []
        try:
            await session.touchpad_on(protocol='raw2', on_event=frames.append, duration_s=None)
            await until(lambda: session.process_diagnostics['computed_tokens'] >= 10)
            assert frames
        finally:
            await session.disconnect()
    asyncio.run(run())


def test_busy_start_is_reported_without_retry(tmp_path):
    async def run():
        session = await connect(tmp_path, target='busy_start')
        try:
            with pytest.raises(Exception) as error:
                await session.touchpad_on(protocol='raw2', on_event=lambda _: None)
            assert error.value.device_code == -16
            assert error.value.phase == 'ack'
            assert await session._sdk_transport.request(p.BATTERY, (0x29, 2), p.battery) == 1
        finally:
            await session.disconnect()
    asyncio.run(run())


def test_stalled_event_consumer_is_bounded_and_does_not_disconnect_ble(tmp_path):
    async def run():
        session = await connect(tmp_path)
        stopped = []
        try:
            await session.touchpad_on(protocol='raw2', on_event=lambda _: None,
                                      on_pointer_move=lambda _: None, on_stopped=stopped.append, duration_s=None)
            time.sleep(2.5)
            await until(lambda: stopped)
            assert stopped[0] is not None
            assert session.process_diagnostics['dropped_output_events'] > 0
            assert session.client.is_connected
            assert await session._sdk_transport.request(p.BATTERY, (0x29, 2), p.battery) == 1
        finally:
            await session.disconnect()
    asyncio.run(run())
