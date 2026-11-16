"""Bounded, local-only worker messages. No application objects cross processes."""
import asyncio
from dataclasses import dataclass
import itertools
import queue

from ring_python_sdk.sdk_provider import RingError


def pack_error(error):
    if error is None:
        return None
    return dict(reason=getattr(error, 'reason', 'worker_error'), message=str(error),
                device_code=getattr(error, 'device_code', None),
                response=getattr(error, 'response', None), phase=getattr(error, 'phase', None))


def unpack_error(value):
    return RingError(**value) if value is not None else None


def offer(channel, message):
    try:
        channel.put_nowait(message)
        return True
    except queue.Full:
        return False


async def messages(channel):
    """One outstanding read keeps a paused event loop from accumulating callbacks."""
    while True:
        try:
            yield await asyncio.to_thread(channel.get, True, .1)
        except queue.Empty:
            continue


@dataclass
class Pending:
    future: asyncio.Future
    hook: object = None


class RPC:
    def __init__(self, peer, actor, outbound):
        self.peer, self.actor, self.outbound = peer, actor, outbound
        self.ids = itertools.count(1)
        self.pending = {}
        self.error = None

    async def call(self, operation, *, timeout=30., hook=None, **params):
        if self.error is not None:
            raise self.error
        key = next(self.ids)
        pending = Pending(asyncio.get_running_loop().create_future(), hook)
        self.pending[key] = pending
        try:
            if not offer(self.outbound, ('call', self.actor, key, operation, params)):
                raise RingError('ipc_overflow', 'SDK control queue is full')
            async with asyncio.timeout(timeout):
                return await pending.future
        except (asyncio.CancelledError, TimeoutError):
            offer(self.outbound, ('cancel', self.actor, key))
            raise
        finally:
            self.pending.pop(key, None)

    def receive(self, message):
        kind, peer, key, *values = message
        if peer != self.peer or kind not in ('ack', 'reply'):
            return False
        pending = self.pending.get(key)
        if pending is None or pending.future.done():
            return True
        if kind == 'ack':
            try:
                if pending.hook is not None:
                    pending.hook(values[0])
            except Exception as exc:
                pending.future.set_exception(exc)
                offer(self.outbound, ('cancel', self.actor, key))
        else:
            value, error = values
            if error is not None:
                pending.future.set_exception(unpack_error(error))
            else:
                pending.future.set_result(value)
        return True

    def fail(self, error):
        self.error = error
        for pending in tuple(self.pending.values()):
            if not pending.future.done():
                pending.future.set_exception(error)


class Server:
    def __init__(self, name, send, handle, cancel=None):
        self.name, self.send, self.handle, self.cancel = name, send, handle, cancel
        self.tasks = {}

    def receive(self, message):
        kind, actor, key, *args = message
        if kind == 'cancel':
            task = self.tasks.get((actor, key))
            if task is not None:
                task.cancel()
            if self.cancel is not None:
                self.cancel((actor, key))
            return
        if kind != 'call':
            raise RingError('protocol_error', 'Invalid SDK worker command')
        async def run():
            try:
                value = await self.handle((actor, key), *args)
                self.send(actor, ('reply', self.name, key, value, None))
            except asyncio.CancelledError:
                self.send(actor, ('reply', self.name, key, None,
                                 pack_error(RingError('cancelled', 'SDK operation cancelled'))))
            except Exception as exc:
                self.send(actor, ('reply', self.name, key, None, pack_error(exc)))
            finally:
                self.tasks.pop((actor, key), None)
        self.tasks[actor, key] = asyncio.create_task(run())

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
