"""Resolve a remembered CoreBluetooth UUID without requiring advertisements.

The presence helper can already hold the physical link when the GUI reconnects.
Create the Bleak device and delegate inside the current asyncio loop; never reuse
a peripheral/delegate from a different asyncio loop. Supports pinned Bleak 1.1.1
(readiness checked in init) and Bleak 3.x (async readiness).
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

    loop = asyncio.get_running_loop()
    manager = getattr(loop, "_ringo_central_manager", None)
    if manager is None:
        manager = CentralManagerDelegate()
        wait_until_ready = getattr(manager, "wait_until_ready", None)
        if wait_until_ready is not None:
            await asyncio.wait_for(wait_until_ready(), timeout=timeout)
        # Reuse the native central for repeated connections on this loop. Its
        # pending disconnect acknowledgements must outlive any one session.
        loop._ringo_central_manager = manager
    native_uuid = NSUUID.alloc().initWithUUIDString_(uuid)
    peripherals = manager.central_manager.retrievePeripheralsWithIdentifiers_([native_uuid])
    for peripheral in peripherals:
        address = str(peripheral.identifier().UUIDString())
        if address.lower() == uuid:
            return BLEDevice(address, peripheral.name(), (peripheral, manager))
    return None
