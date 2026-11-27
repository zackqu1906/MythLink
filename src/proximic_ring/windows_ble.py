"""Resolve Windows-paired R1 devices even between BLE advertisements."""
from __future__ import annotations

import asyncio
import logging
import re
import sys
from uuid import UUID

from .ring_devices import ring_family


async def r1_service_open_status(address):
    """Distinguish an unexposed UART from a service WinRT could not share."""
    from winrt.windows.devices.bluetooth import BluetoothLEDevice, BluetoothCacheMode
    from winrt.windows.devices.bluetooth.genericattributeprofile import GattSharingMode

    if not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", address):
        return ""
    try:
        async with asyncio.timeout(4):
            device = await BluetoothLEDevice.from_bluetooth_address_async(int(address.replace(":", ""), 16))
            if device is None:
                return ""
            try:
                result = await device.get_gatt_services_for_uuid_async(
                    UUID("6e400001-b5a3-f393-e0a9-e50e24dcca9e"), BluetoothCacheMode.UNCACHED)
                services = list(result.services)
                try:
                    if services:
                        return (await services[0].open_async(GattSharingMode.SHARED_READ_AND_WRITE)).name
                finally:
                    for service in services:
                        service.close()
            finally:
                device.close()
    except (OSError, RuntimeError, TimeoutError) as exc:
        logging.getLogger(__name__).debug("R1 GATT status unavailable: %s", exc)
    return ""


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
