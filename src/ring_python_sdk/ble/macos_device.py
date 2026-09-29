"""Resolve a remembered CoreBluetooth UUID without requiring advertisements.

The presence helper can already hold the physical link when the GUI reconnects.
Create the Bleak device and delegate inside the current asyncio loop; never reuse
a peripheral/delegate from the UI discovery thread. Adapter targets Bleak 3.x.
"""
from __future__ import annotations

import asyncio
from uuid import UUID


async def retrieve_device(identifier: str, timeout: float):
    try:
        uuid = str(UUID(identifier))
    except ValueError:
        return None
    from Foundation import NSUUID
    from bleak.backends.device import BLEDevice
    from bleak.backends.corebluetooth.CentralManagerDelegate import CentralManagerDelegate

    manager = CentralManagerDelegate()
    await asyncio.wait_for(manager.wait_until_ready(), timeout=timeout)
    native_uuid = NSUUID.alloc().initWithUUIDString_(uuid)
    peripherals = manager.central_manager.retrievePeripheralsWithIdentifiers_([native_uuid])
    for peripheral in peripherals:
        address = str(peripheral.identifier().UUIDString())
        if address.lower() == uuid:
            return BLEDevice(address, peripheral.name(), (peripheral, manager))
    return None
