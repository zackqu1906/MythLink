"""Transport facade; BLE objects and control arbitration live in the I/O worker."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

from ring_python_sdk.session.sdk_transport import SessionTransport
from ring_python_sdk.sdk_provider import RingError, protocol as p


class RemoteTransport(SessionTransport):
    def __init__(self, session, rpc):
        super().__init__(session, None, client_factory=False)
        self.rpc = rpc
        self._lease = None

    def configure(self, info, mtu, generation):
        self.client = SimpleNamespace(is_connected=True, mtu_size=mtu, services=None)
        self.generation = generation
        self.ready, self.closing = True, False
        self._connection_lost.clear()
        self._update_info(info)

    @asynccontextmanager
    async def control_transaction(self):
        if self._control_owner is asyncio.current_task():
            yield
            return
        async with super().control_transaction():
            lease = await self.rpc.call('acquire')
            self._lease = lease
            try:
                yield
            finally:
                self._lease = None
                if not self.rpc.error:
                    release = asyncio.create_task(self.rpc.call('release', lease=lease, timeout=5.))
                    try:
                        await asyncio.shield(release)
                    except asyncio.CancelledError:
                        await release
                        raise

    async def request(self, command, key, decode, *, hook=None, initializing=False, shutdown=False, timeout=3.):
        if not self.connected or (not initializing and not self.ready) or (self.closing and not shutdown):
            raise RingError('not_ready')
        async with self.control_transaction():
            epoch = self.generation
            if command in (p.START, p.STOP):
                self.stop_confirmed = False
            decoded = []
            def acknowledged(packet):
                value = decode(packet)
                if hook is not None:
                    hook(value)
                decoded.append(value)
            raw = await self.rpc.call('request', command=bytes(command), key=key,
                                      decode=decode is not None, lease=self._lease,
                                      request_timeout=timeout, shutdown=shutdown,
                                      timeout=timeout+5., hook=acknowledged if decode else None)
            if self.generation != epoch or not self.connected:
                raise RingError('disconnected')
            value = (decoded[0] if decoded else decode(raw)) if decode else None
            if command == p.STOP:
                self.stop_confirmed = value.stopped
            elif command == p.INFO:
                self._update_info(value)
            return value

    def lost(self):
        if self.client is not None:
            self.client.is_connected = False
        if self.ready:
            self._lost(self.generation)
