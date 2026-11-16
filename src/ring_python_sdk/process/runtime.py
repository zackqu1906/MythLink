"""Own the two SDK workers and keep application callbacks outside both."""
import asyncio
import logging
import multiprocessing

from ring_python_sdk.sdk_provider import RingError
from .ipc import RPC, messages, unpack_error
from .workers import io_main, touch_main

log = logging.getLogger(__name__)


class ProcessRuntime:
    def __init__(self, *, client_factory=None, model_factory=None):
        context = multiprocessing.get_context('spawn')
        self.commands = context.Queue(256)
        self.replies = context.Queue(256)
        self.data = context.Queue(512)
        self.sensor_data = context.Queue(512)
        self.touch_messages = context.Queue(512)
        self.overflow, self.stop = context.Event(), context.Event()
        self.metrics = context.Array('d', 9, lock=False)
        self.io = RPC('io', 'app', self.commands)
        self.touch = RPC('touch', 'app', self.touch_messages)
        self.io_process = context.Process(target=io_main, name='RingSDK-BLE', daemon=True,
            args=(self.commands, self.replies, self.sensor_data, self.touch_messages, self.overflow,
                  self.stop, self.metrics, client_factory))
        self.touch_process = context.Process(target=touch_main, name='RingSDK-Touchpad', daemon=True,
            args=(self.touch_messages, self.commands, self.replies, self.data, self.overflow,
                  self.stop, self.metrics, model_factory))
        self.transport = self.stream = None
        self.tasks = []
        self.closing = False
        self._close_task = None
        self._touch_failed = False
        self._sensor_drops = 0

    @property
    def diagnostics(self):
        names = ('notifications', 'raw2_packets', 'computed_tokens', 'dropped_sensor_packets',
                 'dropped_raw2_packets', 'max_ble_loop_lag_ms', 'max_touch_loop_lag_ms',
                 'max_raw2_ipc_age_ms', 'dropped_output_events')
        return dict(zip(names, self.metrics), ble_pid=self.io_process.pid,
                    touchpad_pid=self.touch_process.pid)

    async def connect(self, target):
        try:
            self.tasks = [asyncio.create_task(self._controls()), asyncio.create_task(self._data(self.data)),
                          asyncio.create_task(self._data(self.sensor_data)),
                          asyncio.create_task(self._monitor())]
            self.io_process.start()
            self.touch_process.start()
            connection = await self.io.call('connect', target=target)
            self.transport.configure(*connection)
            await self.touch.call('configure', connection=connection)
        except BaseException:
            await self.close()
            raise

    async def _controls(self):
        async for message in messages(self.replies):
            kind = message[0]
            if kind in ('ack', 'reply'):
                (self.io if message[1] == 'io' else self.touch).receive(message)
            elif kind == 'lost':
                self.transport.lost()
            elif kind == 'ended' and self.stream is not None:
                _, identity, error = message
                if identity == self.stream.identity:
                    self.stream.ended(unpack_error(error))

    async def _data(self, channel):
        async for message in messages(channel):
            if self.closing:
                continue
            try:
                if message[0] == 'sensor':
                    if self.transport.ready:
                        self.transport.session._demux(None, message[2])
                elif self.stream is not None and message[1] == self.stream.identity:
                    self.stream.receive(*message[2:])
            except Exception:
                log.exception('SDK consumer callback failed')

    async def _monitor(self):
        while not self.closing:
            await asyncio.sleep(.05)
            if self.closing:
                return
            if self.io_process.exitcode is not None or self.stop.is_set():
                error = RingError('worker_failed', 'SDK BLE worker exited or its control queue overflowed')
                self.io.fail(error)
                self.touch.fail(error)
                self.transport.lost()
                asyncio.create_task(self.close())
                return
            if self.touch_process.exitcode is not None and not self._touch_failed:
                self._touch_failed = True
                error = RingError('worker_failed', 'SDK touchpad worker exited; reconnect to start it again')
                self.touch.fail(error)
                if self.stream is not None:
                    self.stream.ended(error)
                try:
                    await self.io.call('abort_touch', timeout=8.)
                except Exception:
                    log.exception('Could not confirm touchpad STOP after worker exit')
            drops = int(self.metrics[3])
            if drops != self._sensor_drops:
                log.warning('SDK sensor consumer queue dropped %d packets', drops-self._sensor_drops)
                self._sensor_drops = drops

    async def close(self):
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await asyncio.shield(self._close_task)

    async def _close(self):
        self.closing = True
        try:
            for rpc, process in ((self.touch, self.touch_process), (self.io, self.io_process)):
                if process.pid is not None and process.is_alive() and rpc.error is None:
                    try:
                        await rpc.call('shutdown', timeout=5.)
                    except Exception:
                        pass
                    await asyncio.to_thread(process.join, 1.)
            self.stop.set()
            for process in (self.touch_process, self.io_process):
                if process.pid is not None:
                    if process.is_alive():
                        process.terminate()
                    await asyncio.to_thread(process.join, 2.)
                    if process.is_alive():
                        process.kill()
                        await asyncio.to_thread(process.join)
        finally:
            error = RingError('disconnected', 'SDK workers stopped')
            self.io.fail(error)
            self.touch.fail(error)
            for task in self.tasks:
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
            for channel in (self.commands, self.replies, self.data, self.sensor_data, self.touch_messages):
                channel.cancel_join_thread()
                channel.close()
