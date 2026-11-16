"""Application-side subscription to events produced by the touchpad process."""
import asyncio
import uuid

from ring_python_sdk.sdk_provider import RingError
from ring_python_sdk.touchpad.events import TouchpadStats
from .ipc import unpack_error


class ProcessTouchpad:
    protocol = 'raw2'

    def __init__(self, session, on_event, *, on_pointer_move=None, on_stats=None, on_end=None,
                 on_discarded_event=None, duration_s=None):
        from ring_python_sdk.sdk_provider import Touchpad
        # Use the provider's bounded serial delivery without creating its receiver/model.
        from importlib import import_module
        Delivery = import_module(Touchpad.__module__.rsplit('.', 1)[0]+'.delivery').Delivery
        self.runtime = session._sdk_transport.runtime
        self.identity = str(uuid.uuid4())
        self.on_event, self.pointer, self.on_stats, self.on_end = on_event, on_pointer_move, on_stats, on_end
        self.duration_s = duration_s
        self.closed = False
        self.error = None
        self.stats = TouchpadStats()
        self.delivery = Delivery(self._report, lambda *_: None)
        self._stop_task = None
        self._ended = False

    def _report(self, issue, message, severity='WARNING', **counts):
        if severity == 'ERROR':
            self.ended(RingError('consumer_failed', message))

    @property
    def diagnostics(self):
        return self.runtime.diagnostics

    async def prepare(self):
        self.runtime.stream = self
        try:
            await self.runtime.touch.call('start', stream_id=self.identity,
                                          duration_s=self.duration_s, timeout=30.)
        except TimeoutError as exc:
            self.error = RingError('input_timeout', 'Touchpad did not produce its first result within 30 seconds')
            raise self.error from exc
        except Exception as exc:
            self.error = exc
            raise

    def start(self):
        pass  # The worker publishes RUNNING only after its first model result.

    def receive(self, kind, value):
        if self.closed or self._ended:
            return
        if kind == 'stats':
            self.stats = value
        callback = {'frame': self.on_event, 'pointer': self.pointer, 'stats': self.on_stats}[kind]
        if callback is None:
            return
        async def deliver():
            if not self.closed and not self._ended:
                await self.delivery.invoke(callback, value)
        if not self.delivery.offer(deliver):
            self.ended(RingError('consumer_slow', 'SDK event consumer queue is full; touchpad stopped'))

    def ended(self, error):
        if self.closed or self._ended:
            return
        self._ended = True
        self.error = error
        self.delivery.discard()
        if self.on_end is not None:
            self.on_end(self)

    def stop_now(self):
        self.closed = True
        self.delivery.discard()
        if self._stop_task is None:
            self._stop_task = asyncio.create_task(self._stop())

    async def _stop(self):
        try:
            error = await self.runtime.touch.call('stop', stream_id=self.identity, timeout=10.)
            self.error = self.error or unpack_error(error)
        except Exception as exc:
            self.error = self.error or exc

    async def close(self):
        self.stop_now()
        await asyncio.shield(self._stop_task)
        if self.runtime.stream is self:
            self.runtime.stream = None
