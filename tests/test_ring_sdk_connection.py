"""Real provider connection/control flow with an emulated BLE endpoint."""
import asyncio
import struct
from types import SimpleNamespace

import pytest

from ring_mac_sdk import protocol as p
from ring_mac_sdk.errors import RingError
from ring_python_sdk import RingSession
from ring_python_sdk.session.sdk_transport import connect_session
from test_ring_mac_sdk_delivery import FakeClient, FakeModel, status, token
from test_public_sdk_integration import audio_packet, compact


class Client(FakeClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.subscriptions = self.disconnects = 0

    async def start_notify(self, uuid, callback):
        assert uuid == p.NOTIFY
        self.subscriptions += 1
        await super().start_notify(uuid, callback)

    async def disconnect(self):
        self.disconnects += 1
        await super().disconnect()


def mock_model(monkeypatch):
    from ring_python_sdk.touchpad import raw2
    factory = raw2.Raw2Stream
    monkeypatch.setattr(raw2, 'Raw2Stream', lambda *a, **kw: factory(*a, **kw, model_factory=FakeModel))


def test_product_connect_uses_provider_info_before_sensors_and_one_notification_subscription(monkeypatch, tmp_path):
    import bleak
    from ring_python_sdk.session import connection
    from ring_python_sdk.audio.processor import AudioProcessor
    clients = []
    def make(*args, **kwargs):
        client = Client(*args, **kwargs)
        clients.append(client)
        return client
    monkeypatch.setattr(bleak, 'BleakClient', make)
    monkeypatch.setattr(connection, 'BleakClient', lambda *a, **kw: pytest.fail('Legacy BLE connection used'))
    async def sync_time(*a, **kw): pass
    monkeypatch.setattr(RingSession, 'sync_time', sync_time)
    mock_model(monkeypatch)

    async def run():
        session = RingSession('Ringo', 1, data_root=tmp_path, connection_backend='sdk', battery_poll_enabled=False)
        assert await session.connect_device(SimpleNamespace(name='RingoTest', address='test'))
        client = clients[0]
        assert client.commands == [p.INFO]
        assert session._sdk_transport.ready and session.device_info.hw_rev == 1
        assert session._sdk_transport.device_info.swipe_available
        assert client.subscriptions == 1
        gestures, pcm, frames = [], [], []
        await session.swipe_on(on_trigger=gestures.append, print_triggers=False)
        session.mic = AudioProcessor(tmp_path/'audio.wav', on_pcm=lambda *a: pcm.append(a))
        session.mic_active = True
        await session.touchpad_on(protocol='raw2', on_event=frames.append)
        assert session.touchpad.transport is session._sdk_transport
        client.callback(None, compact())
        client.callback(None, audio_packet())
        async with asyncio.timeout(1):
            while not frames:
                await asyncio.sleep(.001)
        assert gestures and pcm
        await session.query_battery()
        assert session.battery_pct == 85
        await session.touchpad_off()
        assert client.is_connected and session.mic_active and session.swipe_active
        assert client.subscriptions == 1 and len(clients) == 1
        # Close local audio; FakeClient deliberately implements no MIC command.
        session.mic.close(); session.mic = None; session.mic_active = False
        await session.disconnect()
        assert client.disconnects == 1 and session.client is None
        assert session._sdk_transport is None
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['info', 'nus'])
def test_invalid_identity_or_services_cannot_publish_ready_or_start_streams(monkeypatch, tmp_path, failure):
    import bleak
    clients = []
    class Invalid(Client):
        async def write_gatt_char(self, uuid, data, **kw):
            self.commands.append(data)
            self.callback(None, b'\x2a\x02\x01\xff')
    def make(*a, **kw):
        client = Invalid(*a, **kw)
        if failure == 'nus': client.services.get_service = lambda _: None
        clients.append(client)
        return client
    monkeypatch.setattr(bleak, 'BleakClient', make)
    async def run():
        session = RingSession('Ringo', 1, data_root=tmp_path, connection_backend='sdk')
        with pytest.raises(Exception):
            await session.connect_device(SimpleNamespace(name='RingoTest', address='test'))
        assert session.client is None and not session._was_connected
        assert session.device_info is None and not session.touchpad_active
        assert clients[0].commands == ([p.INFO] if failure == 'info' else [])
        assert clients[0].disconnects == 1
    asyncio.run(run())


def test_raw2_ack_wait_serializes_legacy_mic_and_battery_commands(monkeypatch, tmp_path):
    mock_model(monkeypatch)
    async def run():
        session = RingSession('Ringo', 1, data_root=tmp_path)
        start_sent = asyncio.Event()
        class Pending(Client):
            async def write_gatt_char(self, uuid, command, *, response):
                assert response is True  # Even callers with legacy response=False.
                if command == p.START:
                    self.commands.append(command)
                    start_sent.set()
                elif command == b'\x20\x00\x01':
                    self.commands.append(command)
                else:
                    await super().write_gatt_char(uuid, command, response=response)
        await connect_session(session, 'offline', client_factory=Pending)
        client = session._sdk_transport.client
        start = asyncio.create_task(session.touchpad_on(protocol='raw2', on_event=lambda _: None))
        await asyncio.wait_for(start_sent.wait(), 1)
        mic = asyncio.create_task(session.client.write_gatt_char(p.REQUEST, b'\x20\x00\x01', response=False))
        battery = asyncio.create_task(session.query_battery())
        await asyncio.sleep(.02)
        assert client.commands == [p.INFO, p.STOP, p.START]
        assert not mic.done() and not battery.done()
        client.callback(None, status())
        client.callback(None, token())
        await asyncio.gather(start, mic, battery)
        assert client.commands[-2:] == [b'\x20\x00\x01', p.BATTERY]
        assert session.battery_pct == 85
        await session.touchpad_off()
        await session.disconnect()
    asyncio.run(run())


def test_provider_disconnect_cleans_streams_and_stale_callbacks_cannot_touch_new_connection(monkeypatch, tmp_path):
    from ring_python_sdk.audio.processor import AudioProcessor
    mock_model(monkeypatch)
    async def run():
        session = RingSession('Ringo', 1, data_root=tmp_path)
        await connect_session(session, 'old', client_factory=Client)
        old = session._sdk_transport.client
        session.mic = AudioProcessor(tmp_path/'audio.wav')
        session.mic_active = True
        stopped = []
        await session.touchpad_on(protocol='raw2', on_event=lambda _: None, on_stopped=stopped.append)
        await old.disconnect()
        await session._wait_touchpad_cleanup()
        assert not session.mic_active and not session.touchpad_active and stopped
        assert not session.client.is_connected and not session._was_connected
        await session._detach_client(send_stops=False)
        await connect_session(session, 'new', client_factory=Client)
        current = session.client
        old.callback(None, b'\x29\x02\x00\x00\x01')
        old.disconnected_callback(old)
        assert session.client is current and current.is_connected
        assert session.battery_pct is None
        await session.disconnect()
    asyncio.run(run())


def test_raw2_cannot_silently_borrow_a_legacy_ble_connection(tmp_path):
    async def run():
        session = RingSession('Ringo', 1, data_root=tmp_path)
        session.client = SimpleNamespace(is_connected=True)
        session.rx_uuid = p.REQUEST
        with pytest.raises(Exception, match='validated ring_mac_sdk'):
            await session.touchpad_on(protocol='raw2', on_event=lambda _: None)
        assert session.touchpad is None and not session.touchpad_active
    asyncio.run(run())


def test_cancelled_ble_write_uses_provider_connection_invalidation(monkeypatch, tmp_path):
    mock_model(monkeypatch)
    async def run():
        writing = asyncio.Event()
        class Slow(Client):
            async def write_gatt_char(self, uuid, command, **kwargs):
                if command == b'\x20\x00\x01':
                    writing.set()
                    await asyncio.Event().wait()
                else:
                    await super().write_gatt_char(uuid, command, **kwargs)
        session = RingSession('Ringo', 1, data_root=tmp_path)
        await connect_session(session, 'test', client_factory=Slow)
        client = session._sdk_transport.client
        await session.touchpad_on(protocol='raw2', on_event=lambda _: None)
        send = asyncio.create_task(session.client.write_gatt_char(p.REQUEST, b'\x20\x00\x01'))
        await asyncio.wait_for(writing.wait(), 1)
        send.cancel()
        with pytest.raises(asyncio.CancelledError):
            await send
        await session._wait_touchpad_cleanup()
        assert not session.client.is_connected and not session._sdk_transport.ready
        assert not session.touchpad_active and client.disconnects == 1
        await session._detach_client(send_stops=False)
    asyncio.run(run())


class LegacyReplyClient(Client):
    """Legacy replies are injected independently after the BLE write returns."""
    async def write_gatt_char(self, uuid, command, *, response):
        if command[0] in (0x20, 0x23, 0x32, 0x34):
            assert response is True
            self.commands.append(command)
        else:
            await super().write_gatt_char(uuid, command, response=response)

    async def wait_sent(self, command):
        async with asyncio.timeout(1):
            while command not in self.commands:
                await asyncio.sleep(.001)


def test_legacy_reply_wait_blocks_raw2_and_mic_without_sharing_query_results(tmp_path):
    from ring_python_sdk.core.time_sync import build_time_set

    async def run():
        session = RingSession('Ringo', 1, data_root=tmp_path)
        await connect_session(session, 'test', client_factory=LegacyReplyClient)
        transport = session._sdk_transport
        client = transport.client
        first = asyncio.create_task(session.query_time())
        await client.wait_sent(b'\x23\x01')
        second = asyncio.create_task(session.sync_time(2000))
        raw2 = asyncio.create_task(transport.request(p.STOP, (0x3c, 0x11), p.status))
        mic = asyncio.create_task(session.client.write_gatt_char(p.REQUEST, b'\x20\x00\x01'))
        await asyncio.sleep(.02)
        assert client.commands == [p.INFO, b'\x23\x01']
        client.callback(None, b'\x23\x02' + struct.pack('<BQQ', 1, 1000, 10))
        assert (await first).unix_ms == 1000
        await client.wait_sent(build_time_set(2000))
        assert not second.done() and not raw2.done() and not mic.done()
        client.callback(None, b'\x23\x02' + struct.pack('<BQQ', 1, 2000, 20))
        assert (await second).unix_ms == 2000
        await asyncio.gather(raw2, mic)
        assert client.commands[-2:] == [p.STOP, b'\x20\x00\x01']
        await session.disconnect()
    asyncio.run(run())


def test_chunked_legacy_reply_holds_queue_until_end_but_does_not_block_audio(tmp_path):
    from ring_python_sdk.audio.processor import AudioProcessor
    from ring_python_sdk.core.health import build_health_read

    async def run():
        session = RingSession('Ringo', 1, data_root=tmp_path)
        await connect_session(session, 'test', client_factory=LegacyReplyClient)
        client = session._sdk_transport.client
        pcm = []
        session.mic = AudioProcessor(tmp_path/'audio.wav', on_pcm=lambda *args: pcm.append(args))
        session.mic_active = True
        read = asyncio.create_task(session.health_read())
        command = build_health_read(0, 0, 0)
        await client.wait_sent(command)
        battery = asyncio.create_task(session.query_battery())
        client.callback(None, b'\x32\x05' + struct.pack('<IH', 0, 3) + b'abc')
        client.callback(None, audio_packet())
        await asyncio.sleep(.02)
        assert pcm and not read.done() and not battery.done()
        assert client.commands == [p.INFO, command]
        client.callback(None, b'\x32\x05' + struct.pack('<IH', 3, 3) + b'def')
        client.callback(None, b'\x32\x06' + struct.pack('<IB', 6, 1))
        data, end = await read
        assert data.payload == b'abcdef' and end.next_offset == 6 and end.done
        await battery
        assert session.battery_pct == 85
        session.mic.close(); session.mic = None; session.mic_active = False
        await session.disconnect()
    asyncio.run(run())


@pytest.mark.parametrize('outcome', ['timeout', 'cancel', 'device_error'])
def test_legacy_reply_failure_releases_queue_without_disabling_other_controls(tmp_path, outcome):
    from ring_python_sdk.core.constants import CMD_IDENTITY, SUBCMD_IDENTITY_ERROR, SUBCMD_IDENTITY_GET
    from ring_python_sdk.core.identity import build_identity_get

    async def run():
        session = RingSession('Ringo', 1, data_root=tmp_path)
        await connect_session(session, 'test', client_factory=LegacyReplyClient)
        client = session._sdk_transport.client
        query = asyncio.create_task(session.query_identity(timeout_s=.05 if outcome == 'timeout' else 1))
        await client.wait_sent(build_identity_get())
        battery = asyncio.create_task(session.query_battery())
        await asyncio.sleep(.01)
        assert not battery.done() and p.BATTERY not in client.commands
        if outcome == 'cancel':
            query.cancel()
            with pytest.raises(asyncio.CancelledError):
                await query
        elif outcome == 'device_error':
            client.callback(None, bytes((CMD_IDENTITY, SUBCMD_IDENTITY_ERROR, SUBCMD_IDENTITY_GET)) + struct.pack('<h', -5))
            with pytest.raises(RuntimeError, match='failed: -5'):
                await query
        else:
            assert await query is None
        await asyncio.wait_for(battery, 1)
        assert session.battery_pct == 85 and client.is_connected
        await session.disconnect()
    asyncio.run(run())


def test_nested_identity_query_can_reenter_control_queue(tmp_path):
    from ring_python_sdk.core.constants import CMD_IDENTITY, SUBCMD_IDENTITY_STATUS
    from ring_python_sdk.core.identity import build_identity_get

    async def run():
        session = RingSession('Ringo', 1, data_root=tmp_path)
        await connect_session(session, 'test', client_factory=LegacyReplyClient)
        client = session._sdk_transport.client
        challenge = asyncio.create_task(session.challenge_identity())
        await client.wait_sent(build_identity_get())
        # Valid, unprovisioned identity: nested query succeeds, then the public
        # challenge API rejects it for the original business reason.
        reply = bytearray(126)
        reply[:3] = bytes((CMD_IDENTITY, SUBCMD_IDENTITY_STATUS, 1))
        client.callback(None, reply)
        with pytest.raises(RuntimeError, match='not provisioned'):
            await asyncio.wait_for(challenge, 1)
        await asyncio.wait_for(session.query_battery(), 1)
        assert session.battery_pct == 85
        await session.disconnect()
    asyncio.run(run())


def test_cancelled_queued_control_is_never_written(tmp_path):
    async def run():
        session = RingSession('Ringo', 1, data_root=tmp_path)
        await connect_session(session, 'test', client_factory=LegacyReplyClient)
        client = session._sdk_transport.client
        first = asyncio.create_task(session.query_time())
        await client.wait_sent(b'\x23\x01')
        queued = asyncio.create_task(session.sync_time(2000))
        await asyncio.sleep(.01)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        client.callback(None, b'\x23\x02' + struct.pack('<BQQ', 1, 1000, 10))
        assert (await first).unix_ms == 1000
        await session.query_battery()
        assert client.commands == [p.INFO, b'\x23\x01', p.BATTERY]
        await session.disconnect()
    asyncio.run(run())


def test_queued_legacy_control_cannot_move_to_replacement_connection(tmp_path):
    async def run():
        session = RingSession('Ringo', 1, data_root=tmp_path)
        await connect_session(session, 'old', client_factory=LegacyReplyClient)
        old = session._sdk_transport.client
        first = asyncio.create_task(session.query_time())
        await old.wait_sent(b'\x23\x01')
        queued = asyncio.create_task(session.sync_time(2000))
        await asyncio.sleep(.01)
        await session._detach_client(send_stops=False)
        assert await asyncio.wait_for(first, .2) is None
        await connect_session(session, 'new', client_factory=LegacyReplyClient)
        current = session._sdk_transport.client
        with pytest.raises(RingError) as error:
            await asyncio.wait_for(queued, 1)
        assert error.value.reason == 'disconnected'
        assert old.commands == [p.INFO, b'\x23\x01']
        assert current.commands == [p.INFO]
        await session.query_battery()
        assert session.battery_pct == 85
        await session.disconnect()
    asyncio.run(run())
