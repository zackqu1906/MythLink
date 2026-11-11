"""Native retrieval contracts using fake CoreBluetooth objects, never a radio."""
import asyncio
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from mythring import _corebluetooth as cb
from mythring.protocol import SERVICE_UUID

ADDRESS = "1E90A22E-0A05-6F12-DAC7-912391B2AC0B"


class CoreBluetoothTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.peripheral = SimpleNamespace(
            identifier=lambda: SimpleNamespace(UUIDString=lambda: ADDRESS),
            name=lambda: "RingoDF0A", state=lambda: 0,
        )
        self.central = SimpleNamespace(
            retrieveConnectedPeripheralsWithServices_=Mock(return_value=[self.peripheral]),
            retrievePeripheralsWithIdentifiers_=Mock(return_value=[self.peripheral]),
        )
        self.manager = SimpleNamespace(central_manager=self.central, wait_until_ready=AsyncMock())
        self.factory = Mock(return_value=self.manager)
        self.cb_uuid = Mock(side_effect=lambda value: ("service", value))
        self.ns_uuid = Mock(side_effect=lambda value: ("identifier", value))
        self.enterContext(patch.dict(sys.modules, {
            "CoreBluetooth": SimpleNamespace(CBUUID=SimpleNamespace(UUIDWithString_=self.cb_uuid)),
            "Foundation": SimpleNamespace(NSUUID=SimpleNamespace(
                alloc=lambda: SimpleNamespace(initWithUUIDString_=self.ns_uuid))),
            "bleak.backends.corebluetooth.CentralManagerDelegate": SimpleNamespace(
                CentralManagerDelegate=self.factory),
        }))
        self.enterContext(patch.object(cb.sys, "platform", "darwin"))

    async def test_system_connected_keeps_native_manager_and_state_zero(self):
        def retrieve(services):
            self.manager.wait_until_ready.assert_awaited_once()
            self.assertEqual(services, [("service", SERVICE_UUID)])
            return [self.peripheral]
        self.central.retrieveConnectedPeripheralsWithServices_.side_effect = retrieve
        devices = await cb.system_connected_devices(SERVICE_UUID, timeout=1)
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].address, ADDRESS)
        self.assertEqual(devices[0].name, "RingoDF0A")
        self.assertEqual(devices[0].details, (self.peripheral, self.manager))

    async def test_uuid_retrieval_uses_nsuuid_and_preserves_native_device(self):
        device = await cb.retrieve_device(ADDRESS.lower(), timeout=1)
        self.manager.wait_until_ready.assert_awaited_once()
        self.central.retrievePeripheralsWithIdentifiers_.assert_called_once_with(
            [("identifier", ADDRESS.lower())])
        self.assertEqual(device.details, (self.peripheral, self.manager))

    async def test_unknown_uuid_is_a_normal_miss(self):
        self.central.retrievePeripheralsWithIdentifiers_.return_value = []
        self.assertIsNone(await cb.retrieve_device(ADDRESS, timeout=1))

    async def test_invalid_identifier_does_not_initialize_corebluetooth(self):
        self.assertIsNone(await cb.retrieve_device("not-a-macos-uuid", timeout=1))
        self.factory.assert_not_called()

    async def test_non_mac_does_not_initialize_corebluetooth(self):
        with patch.object(cb.sys, "platform", "linux"):
            self.assertEqual(await cb.system_connected_devices(SERVICE_UUID, timeout=1), [])
            self.assertIsNone(await cb.retrieve_device(ADDRESS, timeout=1))
        self.factory.assert_not_called()

    async def test_retrieval_failure_logs_and_allows_fallback(self):
        self.manager.wait_until_ready.side_effect = RuntimeError("Bluetooth unavailable")
        with self.assertLogs(cb.__name__, level="WARNING") as logs:
            self.assertEqual(await cb.system_connected_devices(SERVICE_UUID, timeout=1), [])
            self.assertIsNone(await cb.retrieve_device(ADDRESS, timeout=1))
        self.assertEqual(len(logs.output), 2)
        self.assertTrue(all("Bluetooth unavailable" in row for row in logs.output))

    async def test_system_retrieval_failure_preserves_advertisement_scan(self):
        from mythring import scan
        device = SimpleNamespace(address=ADDRESS, name="R1DF0A")
        found = {ADDRESS: (device, SimpleNamespace(local_name="R1DF0A", rssi=-50))}
        self.central.retrieveConnectedPeripheralsWithServices_.side_effect = RuntimeError("retrieve failed")
        with patch("bleak.BleakScanner.discover", new=AsyncMock(return_value=found)), \
                self.assertLogs(cb.__name__, level="WARNING"):
            rings = await scan(0.1)
        self.assertEqual(len(rings), 1)
        self.assertIs(rings[0].device, device)
        self.assertEqual(rings[0].source, "advertisement")

    async def test_retrieval_wait_is_bounded(self):
        async def never_ready():
            await asyncio.Event().wait()
        self.manager.wait_until_ready.side_effect = never_ready
        with self.assertLogs(cb.__name__, level="WARNING"):
            self.assertIsNone(await cb.retrieve_device(ADDRESS, timeout=0.001))

    async def test_cancellation_is_not_swallowed(self):
        self.manager.wait_until_ready.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await cb.system_connected_devices(SERVICE_UUID, timeout=1)
        with self.assertRaises(asyncio.CancelledError):
            await cb.retrieve_device(ADDRESS, timeout=1)
