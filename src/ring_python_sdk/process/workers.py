"""SDK worker entry points. Neither worker imports the application or its UI."""
import asyncio
import multiprocessing
import sys
import time
from types import SimpleNamespace

from ring_python_sdk.sdk_provider import RingError, Transport, clock_ns, protocol as p
from .ipc import RPC, Server, messages, offer, pack_error
from .transport import RemoteTransport


def _run(coroutine):
    # Keep CoreBluetooth's loop open for late native callbacks until process exit.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(coroutine)
    finally:
        tasks = asyncio.all_tasks(loop)
        for task in tasks:
            task.cancel()
        loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))


def io_main(commands, replies, data, touch, overflow, stop, metrics, client_factory=None):
    initialized = False
    if sys.platform == 'win32':
        import ctypes
        result = ctypes.windll.ole32.CoInitializeEx(None, 0)
        initialized = result in (0, 1)
    try:
        _run(_io(commands, replies, data, touch, overflow, stop, metrics, client_factory))
    finally:
        if initialized:
            ctypes.windll.ole32.CoUninitialize()


async def _io(commands, replies, data, touch, overflow, stop, metrics, client_factory):
    transport = None
    preference = None
    lock = asyncio.Lock()
    lease = None
    quitting = asyncio.Event()

    def send(actor, message):
        if not offer(touch if actor == 'touch' else replies, message):
            # A control reply cannot be dropped while pretending the operation succeeded.
            stop.set()

    def received(packet):
        metrics[0] += 1
        stamp = clock_ns()
        if packet[:1] == b'\x3c':
            metrics[1] += int(packet[:2] in (b'\x3c\x02', b'\x3c\x13'))
            if not offer(touch, ('packet', stamp, packet)):
                metrics[4] += 1
                overflow.set()
        elif not offer(data, ('sensor', stamp, packet)):
            metrics[3] += 1

    def disconnected():
        send('app', ('lost',))
        send('touch', ('lost',))

    def cancel(key):
        nonlocal lease
        if lease == key:
            lease = None
            lock.release()

    async def handle(key, operation, params):
        nonlocal transport, preference, lease
        actor, request_id = key
        if operation == 'connect':
            if transport is not None:
                raise RingError('busy', 'SDK worker already owns a connection')
            target = params['target']
            factory = client_factory
            if factory is None and sys.platform == 'darwin':
                from ring_python_sdk.ble.macos_client import create_client
                from ring_python_sdk.ble.macos_device import retrieve_device
                target = await retrieve_device(target, 5.) or target
                factory = create_client
            transport = Transport(target, received, disconnected, client_factory=factory)
            info = await transport.connect()
            from ring_python_sdk.session.windows_link import request_throughput
            preference = request_throughput(transport.client)
            return info, transport.client.mtu_size, transport.generation
        if operation == 'shutdown':
            quitting.set()
            return None
        if operation == 'abort_touch':
            for task_key, task in tuple(server.tasks.items()):
                if task_key[0] == 'touch':
                    task.cancel()
            if lease is not None and lease[0] == 'touch':
                cancel(lease)
            async with lock:
                if transport is not None and transport.ready:
                    return await transport.request(p.STOP, (0x3c, 0x11), p.status, shutdown=True)
            return None
        if operation == 'release':
            if lease == tuple(params['lease']):
                cancel(lease)
            return None
        if transport is None or not transport.ready:
            raise RingError('not_ready')
        if operation == 'acquire':
            await lock.acquire()
            lease = key
            return lease
        if operation != 'request' or lease != tuple(params['lease']) or lease[0] != actor:
            raise RingError('protocol_error', 'SDK command does not own the control transaction')
        decode = bytes if params['decode'] else None
        def acknowledged(packet):
            # Enqueue ACK before any following Raw2 packet from the same notifier.
            send(actor, ('ack', 'io', request_id, packet))
        return await transport.request(params['command'], tuple(params['key']), decode,
                                       hook=acknowledged if decode else None,
                                       shutdown=params['shutdown'], timeout=params['request_timeout'])

    server = Server('io', send, handle, cancel)
    async def receive():
        async for message in messages(commands):
            server.receive(message)
    reader = asyncio.create_task(receive())
    try:
        previous = time.monotonic()
        while not stop.is_set() and not quitting.is_set():
            await asyncio.sleep(.05)
            now = time.monotonic()
            metrics[5] = max(metrics[5], max(0., now-previous-.05)*1000)
            previous = now
            if reader.done():
                reader.result()
            parent = multiprocessing.parent_process()
            if parent is not None and not parent.is_alive():
                break
    finally:
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
        await server.close()
        if preference is not None:
            preference.release()
        if transport is not None:
            await transport.close()


class ArrivalQueue(asyncio.Queue):
    """Retain I/O receipt time when the unchanged receiver queues a packet."""
    arrival = None

    def put_nowait(self, item):
        if self.arrival is not None:
            item = item[0], self.arrival, item[2]
        super().put_nowait(item)


def touch_main(commands, io_commands, replies, data, overflow, stop, metrics, model_factory=None):
    _run(_touch(commands, io_commands, replies, data, overflow, stop, metrics, model_factory))


async def _touch(commands, io_commands, replies, data, overflow, stop, metrics, model_factory):
    from ring_python_sdk.touchpad.raw2 import Raw2Stream, StrokeTouchpad

    class WorkerTouchpad(StrokeTouchpad):
        def __init__(self, stream, factory):
            super().__init__(stream, factory)
            self.queue = ArrivalQueue(maxsize=self.queue.maxsize)

        async def _model_call(self, function, timeout):
            result = await super()._model_call(function, timeout)
            if timeout != 20:  # Loading/warming the model is not an input token.
                metrics[2] += 1
            return result

    class WorkerStream(Raw2Stream):
        _receiver_type = WorkerTouchpad

        async def _start_receiver(self):
            await self.receiver.start()

        def _explain_error(self, error):
            device = self.receiver.reporter.device or {}
            if error is self.receiver.error and device.get('state') == 2:
                return RingError('stream_failed', str(error), device_code=device.get('result'),
                                 response=dict(device), phase='stream')
            return error

        async def _watch(self):
            try:
                try:
                    async with asyncio.timeout(self.duration_s):
                        await asyncio.shield(self.receiver.task)
                except TimeoutError:
                    await self.receiver.stop()
                except Exception as exc:
                    self.error = exc
                self.error = self.error or self._explain_error(self.receiver.error)
                if (sys.platform == 'win32' and self.error is None and not self.closed
                        and 'DEVICE_STOPPED' in self.receiver.reporter.totals):
                    self.error = RingError('device_error', 'Ring stopped the touchpad stream')
            finally:
                if not self.closed and self.on_end:
                    self.on_end(self)

    rpc = RPC('io', 'touch', io_commands)
    session = SimpleNamespace(_was_connected=False, device_info=None)
    transport = RemoteTransport(session, rpc)
    session._sdk_transport = transport
    stream = None
    stream_id = None
    quitting = asyncio.Event()

    def send(_actor, message):
        if not offer(replies, message):
            stop.set()

    def event(identity, kind, value):
        if not offer(data, ('touch_event', identity, kind, value)):
            metrics[8] += 1
            overflow.set()

    async def handle(_key, operation, params):
        nonlocal stream, stream_id
        if operation == 'configure':
            transport.configure(*params['connection'])
            return None
        if operation == 'shutdown':
            quitting.set()
            return None
        if operation == 'start':
            if stream is not None and not stream.closed:
                raise RingError('busy', 'Touchpad worker already has a stream')
            identity = stream_id = params['stream_id']
            overflow.clear()
            stream = WorkerStream(session, lambda value: event(identity, 'frame', value),
                                  on_pointer_move=lambda value: event(identity, 'pointer', value),
                                  on_stats=lambda value: event(identity, 'stats', value),
                                  on_end=lambda value: send('app', ('ended', identity, pack_error(value.error))),
                                  duration_s=params['duration_s'], model_factory=model_factory)
            await stream.prepare()
            stream.start()
            return None
        if operation == 'stop':
            if stream is not None and params['stream_id'] == stream_id:
                await stream.close()
                return pack_error(stream.error)
            return None
        raise RingError('protocol_error', 'Unknown touchpad worker operation')

    server = Server('touch', send, handle)
    async def receive():
        async for message in messages(commands):
            kind = message[0]
            if kind in ('reply', 'ack'):
                rpc.receive(message)
            elif kind == 'packet':
                _, stamp, packet = message
                metrics[7] = max(metrics[7], max(0., (clock_ns()-stamp)/1e6))
                if stream is not None:
                    stream.receiver.queue.arrival = stamp
                    try:
                        stream.submit(packet)
                    finally:
                        stream.receiver.queue.arrival = None
            elif kind == 'lost':
                transport.lost()
                if stream is not None:
                    stream.receiver.invalidate('Ring disconnected', 'DEVICE_STOPPED')
            else:
                server.receive(message)
    reader = asyncio.create_task(receive())
    try:
        previous = time.monotonic()
        while not stop.is_set() and not quitting.is_set():
            await asyncio.sleep(.05)
            now = time.monotonic()
            metrics[6] = max(metrics[6], max(0., now-previous-.05)*1000)
            previous = now
            if reader.done():
                reader.result()
            if stream is not None:
                if overflow.is_set():
                    stream.receiver.invalidate('SDK process queue overflow; input stopped', 'IPC_OVERFLOW')
            parent = multiprocessing.parent_process()
            if parent is not None and not parent.is_alive():
                break
    finally:
        if stream is not None:
            await stream.close()
        await server.close()
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
        rpc.fail(RingError('disconnected', 'Touchpad worker stopped'))
