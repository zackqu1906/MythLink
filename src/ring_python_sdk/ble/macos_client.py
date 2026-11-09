"""CoreBluetooth cancellation compatibility for the pinned Bleak 1.1.1.

The delivered SDK still owns connect, NUS validation and control scheduling.
Its close() also needs to cancel a *pending* native connection: Bleak 1.1.1's
disconnect() otherwise returns early while CBPeripheral is still connecting.
"""
import asyncio
import logging

from CoreBluetooth import CBPeripheralStateDisconnected
from bleak.backends.corebluetooth.client import BleakClientCoreBluetooth

log = logging.getLogger(__name__)


class CancellableMacClient(BleakClientCoreBluetooth):
    async def disconnect(self):
        peripheral = self._peripheral
        manager = self._central_manager_delegate
        if (peripheral is not None and manager is not None
                and peripheral.state() != CBPeripheralStateDisconnected):
            # Includes connecting/connected/disconnecting. Await the native
            # didDisconnect acknowledgement before allowing another attempt.
            try:
                await manager.disconnect(peripheral)
            except (asyncio.CancelledError, Exception):
                # CoreBluetooth can omit the ACK during rapid cancellation.
                # The provider bounds close() to 5 seconds. Retire this central
                # on timeout/failure so a retry cannot replace its callbacks.
                # Its late native callbacks still have the live process loop.
                loop = asyncio.get_running_loop()
                if getattr(loop, "_ringo_central_manager", None) is manager:
                    del loop._ringo_central_manager
                manager._disconnect_callbacks.pop(peripheral.identifier(), None)
                log.warning("[BLE_CANCEL] Native disconnect not acknowledged; retired connection delegate")
                raise
        else:
            await super().disconnect()


def create_client(*args, **kwargs):
    from bleak import BleakClient

    return BleakClient(*args, backend=CancellableMacClient, **kwargs)
