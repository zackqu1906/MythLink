"""Thin smpclient adapter used by the OTA orchestration layer."""

from __future__ import annotations

from collections.abc import AsyncIterator

from smpclient import SMPClient
from smpclient.generics import error, success
from smpclient.requests.image_management import ImageStatesRead, ImageStatesWrite
from smpclient.requests.os_management import ResetWrite
from smpclient.transport.ble import SMPBLETransport
from smpclient.transport import SMPTransportDisconnected

from ring_python_sdk.ota.types import OtaImageState


class SmpOtaBackend:
    def __init__(self, target_name: str, timeout_s: float) -> None:
        self._client = SMPClient(SMPBLETransport(), target_name)
        self._timeout_s = timeout_s
        self._connected = False

    async def connect(self) -> None:
        # Mark attempted connections too: service discovery can fail after BLE connects.
        self._connected = True
        import asyncio
        await asyncio.wait_for(self._client.connect(self._timeout_s), self._timeout_s + 5)

    async def disconnect(self) -> None:
        if self._connected:
            self._connected = False
            await self._client.disconnect()

    async def read_state(self) -> tuple[OtaImageState, ...]:
        response = await self._client.request(ImageStatesRead(), timeout_s=self._timeout_s)
        if error(response):
            raise RuntimeError(f"image state-read failed: {response}")
        if not success(response):  # pragma: no cover - exhaustiveness guard
            raise RuntimeError("unexpected image state response")
        return tuple(
            OtaImageState(
                slot=int(image.slot),
                version=image.version,
                hash_hex=bytes(image.hash or b"").hex(),
                bootable=bool(image.bootable),
                pending=bool(image.pending),
                confirmed=bool(image.confirmed),
                active=bool(image.active),
                permanent=bool(image.permanent),
            )
            for image in response.images
        )

    async def upload(self, image: bytes, slot: int) -> AsyncIterator[int]:
        # smpclient calls this "slot", but encodes it as SMP's image index.
        # Ringo has one image (0); MCUboot selects its secondary slot itself.
        async for offset in self._client.upload(image, slot=slot):
            yield offset

    async def mark_test(self, image_hash: bytes) -> None:
        response = await self._client.request(
            ImageStatesWrite(hash=image_hash, confirm=False), timeout_s=self._timeout_s
        )
        if error(response):
            raise RuntimeError(f"mark test-swap failed: {response}")

    async def confirm(self, image_hash: bytes) -> None:
        response = await self._client.request(
            ImageStatesWrite(hash=image_hash, confirm=True), timeout_s=self._timeout_s
        )
        if error(response):
            raise RuntimeError(f"confirm running image failed: {response}")

    async def reset(self) -> None:
        try:
            response = await self._client.request(ResetWrite(), timeout_s=self._timeout_s)
        except SMPTransportDisconnected:
            # Some firmware reboots before acknowledging reset. Success is still
            # gated on the exact active+confirmed hash after reconnection.
            return
        if error(response):
            raise RuntimeError(f"reset failed: {response}")


__all__ = ["SmpOtaBackend"]
