import asyncio
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from mythring import DiscoveredRing, LinkState, NotConnectedError, ProtocolError, Ring, scan
from mythring.protocol import GET_BATTERY, GET_INFO, NOTIFY_CHARACTERISTIC_UUID, SERVICE_UUID, WRITE_CHARACTERISTIC_UUID

INFO = bytes.fromhex("2a0201020201060000")


class FakeServices:
    missing = False
    def get_service(self, uuid):
        return None if self.missing else object()

    def get_characteristic(self, uuid):
        return SimpleNamespace(uuid=uuid, properties=["write", "notify"], max_write_without_response_size=182)


class FakeClient:
    instances = []
    invalid_info = False
    mtu_size = 185
    missing = False
    def __init__(self, device, **kwargs):
        self.device = device
        self.callback = kwargs["disconnected_callback"]
        self.is_connected = False
        self.services = FakeServices()
        self.services.missing = self.missing
        self.notify = None
        self.info_packet = INFO
        self.writes = []
        self.block_write = None
        self.active_writes = 0
        self.max_writes = 0
        self.__class__.instances.append(self)

    async def connect(self):
        self.is_connected = True

    async def disconnect(self):
        self.is_connected = False
        self.callback(self)

    async def start_notify(self, characteristic, callback):
        self.notify = callback

    def send(self, packet):
        self.notify(None, bytearray(packet))

    async def write_gatt_char(self, characteristic, packet, response):
        self.writes.append(bytes(packet))
        self.active_writes += 1
        self.max_writes = max(self.max_writes, self.active_writes)
        try:
            if packet == self.block_write:
                await asyncio.Event().wait()
            if packet == GET_INFO:
                self.send(b"\x2a\x02bad" if self.invalid_info else self.info_packet)
            elif packet == GET_BATTERY:
                self.send(b"\x29\x02\x00\x00\x54")
            await asyncio.sleep(0)
        finally:
            self.active_writes -= 1


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        FakeClient.instances = []
        FakeClient.invalid_info = False
        FakeClient.missing = False
        self.bleak_patch = patch("bleak.BleakClient", FakeClient)
        self.bleak_patch.start()
        self.health_patch = patch("mythring.client.HealthClient.connected", self.no_health)
        self.health_patch.start()
        self.retrieve = self.enterContext(patch("mythring.client.retrieve_device", new_callable=AsyncMock))
        self.retrieve.return_value = None
        self.system_connected = self.enterContext(patch("mythring.client.system_connected_devices", new_callable=AsyncMock))
        self.system_connected.return_value = []
        self.ring = Ring("test-uuid", write_timeout=0.03)

    async def no_health(self):
        return None

    async def asyncTearDown(self):
        await self.ring.disconnect()
        self.health_patch.stop()
        self.bleak_patch.stop()

    async def test_connect_info_battery_local_disconnect(self):
        states = []
        remove = self.ring.on("link_state", states.append)
        await self.ring.connect()
        self.assertTrue(self.ring.is_connected)
        self.assertEqual(self.ring.device_info.firmware_version, "2.1.6.0")
        self.assertEqual(await self.ring.get_battery_level(), 84)
        old = FakeClient.instances[-1]
        await self.ring.disconnect()
        self.assertIsNone(self.ring.device_info)
        self.assertIsNone(self.ring.status.battery)
        self.assertNotIn(b"\x37\x00\xa5", old.writes)
        self.assertEqual(states, [LinkState.CONNECTING, LinkState.CONNECTED, LinkState.DISCONNECTED])
        remove()

    async def test_invalid_info_closes_connection(self):
        FakeClient.invalid_info = True
        with self.assertRaises(ProtocolError):
            await self.ring.connect()
        self.assertFalse(FakeClient.instances[-1].is_connected)
        self.assertEqual(self.ring.link_state, LinkState.DISCONNECTED)

    async def test_missing_service_closes_connection(self):
        FakeClient.missing = True
        with self.assertRaises(ProtocolError):
            await self.ring.connect()
        self.assertFalse(FakeClient.instances[-1].is_connected)

    async def test_old_notification_and_disconnect_ignored(self):
        await self.ring.connect()
        old = FakeClient.instances[-1]
        await self.ring.disconnect()
        await self.ring.connect()
        old.send(b"\x29\x02\x00\x00\x10")
        old.callback(old)
        self.assertTrue(self.ring.is_connected)
        self.assertIsNone(self.ring.status.battery)

    async def test_reconnect_before_backend_disconnect_callback(self):
        await self.ring.connect()
        old = FakeClient.instances[-1]
        old.is_connected = False
        with patch.object(self.ring.touchpad, "disconnected", wraps=self.ring.touchpad.disconnected) as reset:
            await self.ring.connect()
        self.assertTrue(self.ring.is_connected)
        self.assertEqual(reset.call_count, 2)  # old connection cleanup, new connection reset
        old.callback(old)
        self.assertTrue(self.ring.is_connected)

    async def test_backend_constructor_failure_resets_state(self):
        with patch("bleak.BleakClient", side_effect=RuntimeError("backend unavailable")):
            with self.assertRaisesRegex(RuntimeError, "backend unavailable"):
                await self.ring.connect()
        self.assertEqual(self.ring.link_state, LinkState.DISCONNECTED)
        await asyncio.wait_for(self.ring.wait_disconnected(), 0.1)

    async def test_touchpad_complete_handshake_through_transport(self):
        from test_touchpad import info_packet, status_packet, token_packet
        from mythring.touchpad import START, STOP, TouchpadStage
        await self.ring.connect()
        client = FakeClient.instances[-1]
        client.info_packet = info_packet()
        original_write = client.write_gatt_char

        async def write(characteristic, packet, response):
            await original_write(characteristic, packet, response)
            if packet == STOP:
                client.send(status_packet(state=0))
            elif packet == START:
                client.send(status_packet())
                client.send(token_packet())

        client.write_gatt_char = write
        received = []
        self.ring.on("touchpad_tokens", received.append)
        await self.ring.start_touchpad()
        self.assertEqual(self.ring.touchpad.stage, TouchpadStage.RUNNING)
        self.assertEqual(received[0][0].sequence, 0)
        await self.ring.stop_touchpad()
        self.assertEqual(self.ring.touchpad.stage, TouchpadStage.STOPPED)

    async def test_writes_serialized(self):
        await self.ring.connect()
        await asyncio.gather(*(self.ring.send_command(b"\x39\x00") for _ in range(5)))
        self.assertEqual(FakeClient.instances[-1].max_writes, 1)

    async def test_write_timeout_closes_and_fails_queued_write(self):
        await self.ring.connect()
        client = FakeClient.instances[-1]
        client.block_write = b"\x39\x00"
        results = await asyncio.gather(self.ring.send_command(b"\x39\x00"), self.ring.send_command(b"\x38\x00"), return_exceptions=True)
        self.assertIsInstance(results[0], asyncio.TimeoutError)
        self.assertIsInstance(results[1], NotConnectedError)
        self.assertNotIn(b"\x38\x00", client.writes)
        self.assertFalse(self.ring.is_connected)

    async def test_cancel_submitted_write_closes(self):
        await self.ring.connect()
        client = FakeClient.instances[-1]
        client.block_write = b"\x39\x00"
        task = asyncio.create_task(self.ring.send_command(b"\x39\x00"))
        while client.block_write not in client.writes:
            await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.ring.is_connected)

    async def test_touchpad_info_is_forwarded(self):
        await self.ring.connect()
        with patch.object(self.ring.touchpad, "feed", return_value=True) as feed:
            FakeClient.instances[-1].send(INFO)
        feed.assert_called_once_with(INFO)

    async def test_mtu_forwarded_and_health_gate(self):
        await self.ring.connect()
        self.assertEqual(self.ring.health._mtu(), 185)
        with self.assertRaises(ProtocolError):
            await self.ring.start_imu_tokens()

    async def test_scan_preserves_ble_device(self):
        device = SimpleNamespace(address="a-mac-uuid", name="R1ABCD")
        advert = SimpleNamespace(local_name="R1ABCD", rssi=-51)
        async def discover(**_):
            return {device.address: (device, advert), "other": (SimpleNamespace(name="Other"), advert.__class__(local_name="Other", rssi=-2))}
        with patch("bleak.BleakScanner.discover", discover):
            rings = await scan(0.1)
        self.assertEqual(len(rings), 1)
        self.assertIs(rings[0].device, device)
        self.assertEqual(Ring(rings[0]).address, device.address)

    async def test_scan_merges_system_connected_without_rssi_or_local_connection(self):
        advertised = SimpleNamespace(address="ABC", name="R1ABCD")
        weaker = SimpleNamespace(address="def", name="R1EF12")
        duplicate = SimpleNamespace(address="abc", name="R1ABCD")
        # System-connected does not imply connected to this CBCentralManager.
        connected = SimpleNamespace(address="ghi", name="RingoDF0A", state=lambda: 0)
        other_nus = SimpleNamespace(address="other", name="Other UART")
        unnamed_nus = SimpleNamespace(address="unnamed", name=None)
        self.system_connected.return_value = [duplicate, connected, other_nus, unnamed_nus]
        found = {
            "ABC": (advertised, SimpleNamespace(local_name="R1ABCD", rssi=-42)),
            "def": (weaker, SimpleNamespace(local_name="R1EF12", rssi=-80)),
        }
        with patch("bleak.BleakScanner.discover", new=AsyncMock(return_value=found)):
            rings = await scan(0.2)
        self.assertEqual([row.address for row in rings], ["ABC", "def", "ghi", "other", "unnamed"])
        self.assertIs(rings[0].device, advertised)
        self.assertEqual(rings[0].source, "advertisement")
        self.assertIs(rings[2].device, connected)
        self.assertIsNone(rings[2].rssi)
        self.assertEqual(rings[2].source, "system_connected")
        self.assertEqual(rings[-1].name, "unnamed")
        self.system_connected.assert_awaited_once_with(SERVICE_UUID, timeout=0.2)

    async def test_system_nus_candidate_still_requires_valid_info(self):
        device = SimpleNamespace(address="other", name="Other UART")
        self.ring = Ring(DiscoveredRing(device.address, device.name, None, device, "system_connected"))
        FakeClient.invalid_info = True
        with self.assertRaises(ProtocolError):
            await self.ring.connect()
        self.assertEqual(self.ring.link_state, LinkState.DISCONNECTED)
        self.assertFalse(FakeClient.instances[-1].is_connected)

    async def test_known_uuid_uses_retrieved_device_without_advertising(self):
        address = "1E90A22E-0A05-6F12-DAC7-912391B2AC0B"
        device = SimpleNamespace(address=address, name="RingoDF0A")
        self.ring = Ring(address)
        self.retrieve.return_value = device
        await self.ring.connect()
        self.assertIs(FakeClient.instances[-1].device, device)
        self.retrieve.assert_awaited_once_with(address, timeout=30.0)
        self.assertTrue(self.ring.is_connected)

    async def test_known_uuid_miss_preserves_bleak_fallback(self):
        await self.ring.connect()
        self.assertEqual(FakeClient.instances[-1].device, "test-uuid")

    async def test_windows_hidden_hid_firmware_rejection_prevents_mapping_write(self):
        from mythring.hid import HidError, HidGesture, HidKey
        device = SimpleNamespace(address="C0:01:02:03:04:05", name="R1ABCD")
        self.ring = Ring(device)
        with patch("sys.platform", "win32"), patch.object(FakeServices, "get_service",
                                                           lambda _, uuid: object() if uuid == SERVICE_UUID else None):
            await self.ring.connect()
        self.assertEqual(await self.ring.get_battery_level(), 84)
        self.assertIsNone(await self.ring.read_rssi())
        client = FakeClient.instances[-1]
        async def reject_read(_characteristic, packet, response):
            client.writes.append(packet)
            client.send(struct.pack("<BBBHh", 0x30, 5, HidGesture.LEFT, HidKey.DISABLED, -95))
        client.write_gatt_char = reject_read
        with self.assertRaises(HidError) as caught:
            await self.ring.hid.save(HidGesture.LEFT, HidKey.F9)
        self.assertEqual(caught.exception.reason, "unsupported_item")
        self.assertEqual([p for p in client.writes if p[0] == 0x30], [b"\x30\x04\x03"])

    async def test_native_device_skips_uuid_retrieval(self):
        device = SimpleNamespace(address="native-uuid", name="RingoDF0A")
        self.ring = Ring(device)
        await self.ring.connect()
        self.retrieve.assert_not_awaited()
        self.assertIs(FakeClient.instances[-1].device, device)

    async def test_cancelled_uuid_retrieval_resets_connecting_state(self):
        self.retrieve.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.ring.connect()
        self.assertEqual(self.ring.link_state, LinkState.DISCONNECTED)
        self.assertTrue(self.ring._disconnected.is_set())
        self.assertEqual(FakeClient.instances, [])


if __name__ == "__main__":
    unittest.main()


class FullIntegrationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = ClientTests.asyncSetUp
    asyncTearDown = ClientTests.asyncTearDown
    no_health = ClientTests.no_health
    async def test_maintenance_blocks_connection_and_writes(self):
        self.ring._firmware_maintenance=object()
        with self.assertRaises(ProtocolError):await self.ring.connect()
        self.ring._firmware_maintenance=None
        await self.ring.connect()
        self.ring._firmware_maintenance=object()
        with self.assertRaises(ProtocolError):await self.ring.get_battery_level()
        self.ring._firmware_maintenance=None

    async def test_owner_ack_disconnect_is_not_system_acl_success(self):
        from mythring.connection import RingDisconnectOutcome
        await self.ring.connect()
        self.ring.owner.timeout=.03
        results=[];self.ring.on('owner_disconnect',results.append)
        pending=asyncio.create_task(self.ring.owner_unbind())
        client=self.ring._client
        for _ in range(100):
            if b'\x37\x00\xa5' in client.writes:break
            await asyncio.sleep(0)
        client.send(b'\x37\x01\x01\x00\x00')
        client.is_connected=False;client.callback(client)
        result=await pending
        self.assertEqual(result.outcome,RingDisconnectOutcome.SYSTEM_STATE_UNCONFIRMED)
        self.assertTrue(result.unbind_acknowledged)
        self.assertTrue(result.link_disconnected)
        self.assertFalse(result.system_disconnected)
        self.assertEqual(results,[result])

    async def test_touchpad_inference_lifecycle_wiring(self):
        from unittest.mock import AsyncMock,Mock
        await self.ring.connect()
        with patch.object(self.ring._recognizer,'start',new_callable=AsyncMock) as start,patch.object(self.ring._recognizer,'stop',new_callable=AsyncMock) as stop,patch.object(self.ring.touchpad,'start',new_callable=AsyncMock) as wire_start,patch.object(self.ring.touchpad,'stop',new_callable=AsyncMock):
            await self.ring.start_touchpad()
            start.assert_awaited_once();wire_start.assert_awaited_once_with(mtu=185, ringo=False)
            await self.ring.stop_touchpad();stop.assert_awaited_once()
        received=[];self.ring.on('touchpad_event',received.append)
        self.ring._recognizer.on_event('sample')
        self.assertEqual(received,['sample'])

    async def test_queued_touchpad_operations_cannot_cross_connections(self):
        from unittest.mock import AsyncMock
        await self.ring.connect()
        for name in ('start_touchpad','start_touchpad_raw','stop_touchpad'):
            with self.subTest(name=name):
                await self.ring._touchpad_lock.acquire()
                task=asyncio.create_task(getattr(self.ring,name)())
                await asyncio.sleep(0)
                await self.ring.disconnect()
                await self.ring.connect()
                self.ring._touchpad_lock.release()
                with self.assertRaises(NotConnectedError):await task

    async def test_disconnect_during_model_load_cannot_start_new_connection(self):
        from unittest.mock import AsyncMock
        await self.ring.connect()
        async def load():
            await self.ring.disconnect()
            await self.ring.connect()
        with patch.object(self.ring._recognizer,'start',side_effect=load),patch.object(self.ring._recognizer,'stop',new_callable=AsyncMock),patch.object(self.ring.touchpad,'start',new_callable=AsyncMock) as wire:
            with self.assertRaises(NotConnectedError):await self.ring.start_touchpad()
            wire.assert_not_awaited()
