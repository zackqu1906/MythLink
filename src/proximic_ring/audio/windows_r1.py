"""Release WinRT GATT services even when the R1 link drops before SDK cleanup."""
import asyncio
import logging
import traceback

from bleak.exc import BleakError
from ring_python_sdk_r1 import ConnectionState, Ring


def release_error_frames(error):
    # The runtime retains errors for diagnostics. Completed SDK/WinRT frames
    # can otherwise retain GATT objects for the lifetime of the desktop app.
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        traceback.clear_frames(error.__traceback__)
        error = error.__cause__ or error.__context__


class WindowsR1Ring(Ring):
    _lost_client = None

    def _lost(self, gatt):
        if gatt is self._gatt and self.state == ConnectionState.READY:
            # The SDK drops its last Gatt reference for an unsolicited disconnect.
            self._lost_client = gatt.client
        super()._lost(gatt)

    async def _close(self):
        client = self._gatt.client if self._gatt else self._lost_client
        self._lost_client = None
        # Bleak 3.0.2 turns this WinRT lease on at connect; Close alone
        # can leave the peer's previous binding session alive on Windows.
        session = getattr(getattr(client, "_backend", None), "_session", None)
        if session is not None:
            try:
                session.maintain_connection = False
            except OSError:
                pass  # Windows already closed this session.
        try:
            await super()._close()
        finally:
            if client is not None:
                # SDK 0.4.1 skips disconnect() when is_connected is false.
                # WinRT still owns the enumerated services in that state. Bleak's
                # disconnect disposes them, and is a no-op after a clean close.
                try:
                    async with asyncio.timeout(10):
                        await client.disconnect()
                except (BleakError, OSError, TimeoutError) as exc:
                    logging.getLogger(__name__).warning("R1 GATT cleanup: %s", exc)
