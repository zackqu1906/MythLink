"""Process backend for the public RingSession API; no UI or host-app dependencies."""
from .transport import RemoteTransport


class ProcessTransport(RemoteTransport):
    def __init__(self, session, target, *, client_factory=None):
        from .runtime import ProcessRuntime
        self.runtime = ProcessRuntime(client_factory=client_factory,
                                      model_factory=getattr(session, '_process_model_factory', None))
        self.target = getattr(target, 'address', target)
        super().__init__(session, self.runtime.io)
        self.runtime.transport = self

    async def connect(self):
        await self.runtime.connect(str(self.target))
        return self.device_info

    async def close(self):
        self.closing = True
        await self.runtime.close()
        self.lost()


__all__ = []  # Select through RingSession(connection_backend='process').
