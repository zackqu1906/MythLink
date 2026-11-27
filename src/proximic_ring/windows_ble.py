"""Resolve Windows-paired R1 devices even between BLE advertisements."""
from __future__ import annotations

import asyncio
import logging
import re
import sys

from .ring_devices import ring_family


async def paired_r1_devices(address: str = ""):
    """Return Bleak handles from the OS pairing store, without opening a GATT link."""
    if sys.platform != "win32":
        return []
    from bleak.backends.device import BLEDevice
    from winrt.windows.devices.bluetooth import BluetoothLEDevice
    from winrt.windows.devices.enumeration import DeviceInformation
    from winrt.windows.foundation import IPropertyValue

    address_key = "System.Devices.Aep.DeviceAddress"
    try:
        async with asyncio.timeout(3):
            devices = await DeviceInformation.find_all_async(
                BluetoothLEDevice.get_device_selector_from_pairing_state(True), [address_key])
        result = []
        for info in devices:
            value = info.properties.get(address_key)
            if value is None:
                continue
            mac = value.as_(IPropertyValue).get_string().upper()
            if not re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", mac):
                continue
            if address and mac != address.upper():
                continue
            if not address and ring_family(info.name or "") != "R1":
                continue
            result.append(BLEDevice(mac, info.name or "R1", info.id))
        return result
    except (OSError, RuntimeError, TimeoutError) as exc:
        logging.getLogger(__name__).warning("Windows paired-device lookup unavailable: %s", exc)
        return []
