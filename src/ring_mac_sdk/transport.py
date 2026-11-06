"""One CoreBluetooth session, serialized writes, independently matched ACKs."""
import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from . import protocol as p
from .errors import RingError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiscoveredRing:
    identifier: str
    name: str | None
    rssi: int
    _device: Any = field(repr=False, compare=False)


async def scan(timeout: float = 5.0) -> list[DiscoveredRing]:
    """NUS or named Ringo/R1 candidates; identity is verified by INFO after connect."""
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    from bleak import BleakScanner
    # Ringo advertises SMP (and optionally HID), not the NUS UUID.
    found = await BleakScanner.discover(timeout=timeout, return_adv=True,
                                        service_uuids=[p.SERVICE, p.SMP_SERVICE])
    def candidate(device, advertisement):
        name = advertisement.local_name or device.name or ""
        return (p.SERVICE in [u.lower() for u in advertisement.service_uuids]
                or re.fullmatch(r"(?:Ringo|R1)[0-9a-fA-F]{4}", name) is not None
                or name.endswith("的Ringo"))
    return sorted((DiscoveredRing(d.address, a.local_name or d.name, a.rssi, d)
                   for d, a in found.values() if candidate(d, a)), key=lambda d: -d.rssi)


@dataclass
class Pending:
    future: asyncio.Future
    decode: Any
    hook: Any
    accepting: bool = False


def response_key(data):
    if len(data) < 2:
        return None
    head = tuple(data[:2])
    if head in ((0x26, 8), (0x38, 0x80), (0x39, 0x80)):
        return (*head, data[2]) if len(data) >= 3 else None
    return head


class Transport:
    def __init__(self, device, on_data, on_disconnect, client_factory=None):
        self.device, self.on_data, self.on_disconnect = device, on_data, on_disconnect
        self.client_factory = client_factory
        self.client = None
        self.pending = {}
        self.write_lock = asyncio.Lock()
        self.generation = 0
        self.ready = False
        self.closing = False
        self.device_info = None

    @property
    def connected(self):
        return self.client is not None and self.client.is_connected

    async def connect(self):
        if self.client is not None:
            raise RingError("busy", "A connection already exists")
        factory = self.client_factory
        if factory is None:
            from bleak import BleakClient
            factory = BleakClient
        self.generation += 1
        epoch = self.generation
        self.closing = False
        device = self.device._device if isinstance(self.device, DiscoveredRing) else self.device
        client = factory(device, disconnected_callback=lambda _: self._lost(epoch), timeout=15)
        self.client = client
        try:
            async with asyncio.timeout(20):
                await client.connect()
                if not client.services.get_service(p.SERVICE):
                    raise RingError("unsupported", "NUS service unavailable")
                request = client.services.get_characteristic(p.REQUEST)
                notify = client.services.get_characteristic(p.NOTIFY)
                if request is None or notify is None or "write" not in request.properties or "notify" not in notify.properties:
                    raise RingError("unsupported", "Required NUS write/notify characteristics unavailable")
                await client.start_notify(p.NOTIFY, lambda _, raw: self._notify(epoch, bytes(raw)))
                info = await self.request(p.INFO, (0x2A, 2), p.info, initializing=True)
                if not self.connected or self.generation != epoch:
                    raise RingError("disconnected")
                self.ready = True
                self.device_info = info
                return info
        except BaseException:
            await self.close()
            raise

    def _lost(self, epoch):
        if epoch != self.generation:
            return
        self.ready = False
        self.device_info = None
        self.generation += 1
        self._fail_pending()
        self.on_disconnect()

    def _fail_pending(self):
        for call in self.pending.values():
            if not call.future.done():
                call.future.set_exception(RingError("disconnected"))

    async def close(self):
        self.ready = False
        self.device_info = None
        self.closing = True
        self.generation += 1
        self._fail_pending()
        client, self.client = self.client, None
        if client is not None:
            try:
                await asyncio.wait_for(client.disconnect(), 5)
            finally:
                self.on_disconnect()

    def _notify(self, epoch, data):
        if epoch != self.generation:
            return
        call = self.pending.get(response_key(data))
        if call and call.accepting and not call.future.done():
            try:
                result = call.decode(data)
                if call.hook:
                    call.hook(result)
                call.future.set_result(result)
            except Exception as exc:
                call.future.set_exception(exc)
            return
        self.on_data(data)

    async def request(self, command, key, decode, *, hook=None, initializing=False, shutdown=False, timeout=3.0):
        if not self.connected or (not initializing and not self.ready) or (self.closing and not shutdown):
            raise RingError("not_ready")
        if key in self.pending:
            raise RingError("busy", "An operation with the same ACK key is pending")
        if self.device_info is not None and self.device_info.hardware_revision == 1 and self.pending:
            raise RingError("busy", "Ringo permits only one pending control operation")
        epoch = self.generation
        call = Pending(asyncio.get_running_loop().create_future(), decode, hook)
        self.pending[key] = call
        phase = "queue"
        try:
            async with asyncio.timeout(timeout):
                async with self.write_lock:
                    if self.generation != epoch or not self.connected:
                        raise RingError("disconnected")
                    call.accepting = True
                    phase = "write"
                    try:
                        await asyncio.wait_for(self.client.write_gatt_char(p.REQUEST, command, response=True), 2)
                    except (TimeoutError, asyncio.CancelledError):
                        # No completed write: invalidate the session before another write.
                        await self.close()
                        raise
                    except Exception as exc:
                        raise RingError("send_failed", str(exc)) from exc
                    phase = "ack"
                result = await call.future if decode is not None else None
                if self.generation != epoch:
                    raise RingError("disconnected")
                return result
        except TimeoutError as exc:
            raise RingError("timeout", f"BLE {phase} phase timed out", phase=phase) from exc
        finally:
            if self.pending.get(key) is call:
                del self.pending[key]
            if call.future.done() and not call.future.cancelled():
                call.future.exception()  # consume disconnect exception if write failed first
            call.future.cancel()

    async def send(self, command):
        """Await the BLE write only, for legacy commands with no business ACK."""
        return await self.request(command, (command[0], -1, command[1]), None)
