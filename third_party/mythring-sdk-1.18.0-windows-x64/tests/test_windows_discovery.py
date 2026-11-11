"""Exercise the WinRT discovery contract with no radio or Windows imports on the host."""
import asyncio
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bleak.backends.device import BLEDevice

from mythring import scan
from mythring._discovery import retrieve_device
from mythring.smp import SmpError, SmpTransport

ADDRESS = "C0:01:02:03:04:05"
PROPERTY = "System.Devices.Aep.DeviceAddress"


def endpoint(name, address):
    return SimpleNamespace(name=name, properties={PROPERTY: address})


class WindowsDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.query = AsyncMock(return_value=[])
        self.selector = Mock(return_value="connected BLE endpoints")
        self.enterContext(patch.dict(sys.modules, {
            "winrt.windows.devices.bluetooth": SimpleNamespace(
                BluetoothLEDevice=SimpleNamespace(get_device_selector_from_connection_status=self.selector),
                BluetoothConnectionStatus=SimpleNamespace(CONNECTED=1)),
            "winrt.windows.devices.enumeration": SimpleNamespace(
                DeviceInformation=SimpleNamespace(find_all_async_with_kind_aqs_filter_and_additional_properties=self.query),
                DeviceInformationKind=SimpleNamespace(ASSOCIATION_ENDPOINT=5)),
            "winrt.system": SimpleNamespace(unbox_string=lambda value: str(value)),
            "bleak.backends.winrt.util": SimpleNamespace(assert_mta=AsyncMock()),
        }))
        self.enterContext(patch.object(sys, "platform", "win32"))

    async def test_scan_merges_named_endpoints_and_preserves_advertising_details(self):
        other = "C0:01:02:03:04:06"
        self.query.return_value = [endpoint("R1ABCD", ADDRESS.lower()), endpoint("Ringo0006", other),
                                   endpoint("Keyboard", "C0:01:02:03:04:07"), endpoint("R1ABCD", "bad")]
        advertised = BLEDevice(ADDRESS, "R1ABCD", "native advertising details")
        found = {ADDRESS: (advertised, SimpleNamespace(local_name="R1ABCD", rssi=-41))}
        with patch("bleak.BleakScanner.discover", new=AsyncMock(return_value=found)):
            rings = await scan(0.2)
        self.selector.assert_called_once_with(1)
        self.query.assert_awaited_once_with("connected BLE endpoints", [PROPERTY], 5)
        self.assertEqual([(r.address, r.source, r.rssi) for r in rings],
                         [(ADDRESS, "advertisement", -41), (other, "system_connected", None)])
        self.assertIs(rings[0].device, advertised)
        self.assertIsInstance(rings[1].device, BLEDevice)

    async def test_known_address_resolves_exact_endpoint_even_with_a_cached_name(self):
        self.query.return_value = [endpoint("R1ABCD", "C0:01:02:03:04:06"), endpoint("old name", ADDRESS)]
        device = await retrieve_device(ADDRESS.lower(), timeout=0.2)
        self.assertEqual((device.address, device.name), (ADDRESS, "old name"))

    async def test_optional_lookup_timeout_falls_back(self):
        async def delayed(*_):
            await asyncio.Event().wait()
        self.query.side_effect = delayed
        with self.assertLogs("mythring._discovery", level="WARNING"):
            self.assertIsNone(await retrieve_device(ADDRESS, timeout=0.01))

    async def test_smp_closed_during_retrieval_never_connects(self):
        transport = SmpTransport(ADDRESS)
        started, resume = asyncio.Event(), asyncio.Event()
        async def delayed(*_):
            started.set()
            await resume.wait()
            return []
        self.query.side_effect = delayed
        with patch("bleak.BleakClient") as client:
            opening = asyncio.create_task(transport.connect())
            await started.wait()
            await transport.close()
            resume.set()
            with self.assertRaises(SmpError):
                await opening
            client.assert_not_called()
