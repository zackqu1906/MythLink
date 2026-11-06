"""Keep legacy request state and response waits inside the SDK control queue."""

import asyncio
from functools import wraps


async def wait_control_reply(session, event, timeout):
    """Called directly after send, while the transaction still owns the queue."""
    transport = session._sdk_transport
    if transport is None:
        await asyncio.wait_for(event.wait(), timeout)
    else:
        await transport.wait_for_legacy_reply(event, timeout)


def serialized_control(method):
    """Serialize a complete legacy transaction when using the SDK connection.

    Acquire before clearing shared reply state, and release only after the
    reply, timeout, error or cancellation. The original legacy backend keeps
    its existing behavior; no SDK import is required on that path.
    """
    @wraps(method)
    async def run(self, *args, **kwargs):
        transport = self._sdk_transport
        if transport is None:
            return await method(self, *args, **kwargs)
        async with transport.control_transaction():
            return await method(self, *args, **kwargs)

    return run
