"""Use the delivered SDK as the sole BLE connection/control owner.

The provider's connect, INFO validation, notification matching and disconnect
implementation are unchanged. Legacy processors consume unmatched packets;
their control helpers use a facade instead of writing to Bleak directly.
"""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
import logging
import sys
import time

from ring_python_sdk.core.device_info import DeviceInfo, InfoComponent
from ring_python_sdk.sdk_provider import GestureClient, RingError, Transport
from ring_python_sdk.sdk_provider import protocol as p
from .windows_link import request_throughput

log = logging.getLogger(__name__)


class SessionTransport(Transport):
    def __init__(self, session, device, *, client_factory=None):
        self.session = session
        if client_factory is None and sys.platform == 'darwin':
            from ring_python_sdk.ble.macos_client import create_client
            client_factory = create_client
        super().__init__(device, lambda data: session._demux(None, data), self._disconnected,
                         client_factory=client_factory)
        # Queue whole control transactions, including ACK waits. This extends
        # the SDK's busy rejection to safely serve multiple product consumers.
        self.control_lock = asyncio.Lock()
        self._control_owner = None
        self._connection_lost = asyncio.Event()
        self.stop_confirmed = False
        self.gesture = GestureClient(self)
        self.link_preference = None

    def release_link_preference(self):
        preference, self.link_preference = self.link_preference, None
        if preference is not None:
            preference.release()

    def _disconnected(self):
        self.release_link_preference()
        self._connection_lost.set()
        if self.session._sdk_transport is self and self.session._was_connected:
            self.session._on_ble_disconnected(self.session.client)

    def _update_info(self, info):
        self.device_info = info
        self.session.device_info = DeviceInfo(
            1, info.hardware_revision, *info.firmware_numeric_version,
            tuple(InfoComponent(**asdict(c)) for c in info.components))
        log.info('[SDK_CONNECTION] verified firmware=%s hardware=%s swipe=%s',
                 info.firmware_version, info.hardware_revision, info.swipe_available)

    @asynccontextmanager
    async def control_transaction(self):
        """Reserve the SDK queue through a legacy operation's response wait.

        Direct awaits may enter again (a legacy query calls SDK send/request).
        Child tasks must still queue; inheriting a context variable is not
        sufficient to establish ownership of the BLE control channel.
        """
        task = asyncio.current_task()
        if self._control_owner is task:
            yield
            return
        epoch = self.generation
        async with self.control_lock:
            # A queued operation belongs to the connection it was submitted on.
            if self.generation != epoch or self.session._sdk_transport is not self:
                raise RingError('disconnected')
            self._control_owner = task
            try:
                yield
            finally:
                self._control_owner = None

    async def request(self, command, *args, **kwargs):
        # All callers, including inherited connect()/send(), use this one gate.
        async with self.control_transaction():
            raw2 = command[:1] == b'\x3c'
            if command in (p.START, p.STOP):
                self.stop_confirmed = False
            name = {p.START: 'START', p.STOP: 'STOP', p.INFO: 'INFO', p.BATTERY: 'BATTERY'}.get(command, command.hex())
            tag = 'RAW2_CONTROL' if raw2 else 'SDK_CONTROL'
            started = time.monotonic()
            log.info('[%s] command=%s stage=send', tag, name)
            try:
                result = await super().request(command, *args, **kwargs)
            except BaseException as exc:
                log.warning('[%s] command=%s stage=failed elapsed_ms=%.1f error=%r',
                            tag, name, (time.monotonic()-started)*1000, exc)
                raise
            if command == p.STOP:
                self.stop_confirmed = result.stopped
            elif command == p.INFO:
                self._update_info(result)
            log.info('[%s] command=%s stage=complete elapsed_ms=%.1f response=%s',
                     tag, name, (time.monotonic()-started)*1000,
                     asdict(result) if hasattr(result, '__dataclass_fields__') else result)
            return result

    async def wait_for_legacy_reply(self, event, timeout):
        """Abort a legacy response wait when its SDK connection is lost."""
        if not self.connected or not self.ready or self.closing:
            raise RingError('disconnected')
        epoch = self.generation
        reply = asyncio.create_task(event.wait())
        lost = asyncio.create_task(self._connection_lost.wait())
        try:
            done, _ = await asyncio.wait((reply, lost), timeout=timeout,
                                         return_when=asyncio.FIRST_COMPLETED)
            if (lost in done or self.generation != epoch or not self.connected
                    or not self.ready or self.closing):
                raise RingError('disconnected')
            if reply not in done:
                raise TimeoutError
        finally:
            reply.cancel()
            lost.cancel()
            await asyncio.gather(reply, lost, return_exceptions=True)


class LegacyControlClient:
    """Existing sensor helpers keep their API, but cannot bypass SDK writes."""
    def __init__(self, transport):
        self.transport = transport

    @property
    def is_connected(self):
        return self.transport.connected and self.transport.ready and not self.transport.closing

    @property
    def mtu_size(self):
        return self.transport.client.mtu_size if self.transport.client else 0

    @property
    def services(self):
        return self.transport.client.services if self.transport.client else None

    async def write_gatt_char(self, uuid, data, *, response=False):
        if str(uuid).lower() != p.REQUEST:
            raise ValueError('Product controls must use the SDK NUS request characteristic')
        command = bytes(data)
        if command == p.INFO:
            await self.transport.request(command, (0x2a, 2), p.info)
        elif command == p.BATTERY:
            def decode(packet):
                result = p.battery(packet)
                self.transport.session._demux(None, packet)
                return result
            await self.transport.request(command, (0x29, 2), decode)
        elif command in (b'\x26\x00', b'\x26\x01'):
            # Preserve Ringo write-only vs R1 acknowledged gesture semantics.
            await self.transport.gesture.set_mode(command[1])
        else:
            # The SDK owns the BLE write. Legacy operations with a response
            # hold control_transaction through their existing demux/event wait;
            # commands without a business ACK finish after the BLE response.
            await self.transport.send(command)


async def connect_session(session, target, *, client_factory=None):
    """Called under RingSession's connection lock; no alternate BLE handshake."""
    session.device_info = None
    session._was_connected = False
    transport = SessionTransport(session, target, client_factory=client_factory)
    session._sdk_transport = transport
    session.client = LegacyControlClient(transport)
    session.tx_uuid, session.rx_uuid = p.NOTIFY, p.REQUEST
    await transport.connect()
    # Without this Windows keeps a 30 ms / latency-4 link and Raw2 stalls.
    transport.link_preference = request_throughput(transport.client)
    session.reconnecting = False
    session._was_connected = True
    log.info('[SDK_CONNECTION] ready backend=%s mtu=%s', Transport.__module__.split('.')[0], session.client.mtu_size)
