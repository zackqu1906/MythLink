import asyncio
import time
import uuid

from . import protocol as p
from .errors import RingError
from .interaction import Engine
from .types import Click, ContactUpdate, EventMeta, Motion, TouchpadStatus


class Touchpad:
    # Only the idempotent initialization STOP may be retried while Ringo's
    # owner-encrypted session becomes ready. START and cleanup STOP never retry.
    INITIAL_STOP_BUDGET = 12.0
    INITIAL_STOP_ATTEMPT = 3.0
    INITIAL_STOP_RETRY_DELAY = .25

    def __init__(self, transport, emit, status_changed, model_factory=None):
        self.transport, self.emit, self.status_changed = transport, emit, status_changed
        self.model_factory = model_factory
        self.status = TouchpadStatus()
        self.task = self.first = None
        self._entered = self._stop_requested = False
        self.accepting = False
        self.start_pending = False
        self.error = None
        self.generation = self.sequence = self.ordinal = 0
        self.session = ""
        self.queue = asyncio.Queue(maxsize=64)
        self.engine = Engine(self._event)

    def _status(self, stage, message=""):
        self.status = TouchpadStatus(stage, message, self.session, self.generation)
        self.status_changed(self.status)

    def _clear(self):
        self.accepting = False
        self.start_pending = False
        self.engine.clear()
        while not self.queue.empty():
            self.queue.get_nowait()

    def invalidate(self, message):
        if self.task is not None and not self.task.done():
            self.error = RingError("stream_failed", message)
            self._clear()
            self._status("FAILED", message)
            if not self._stop_requested:
                self._stop_requested = True
                if self._entered:
                    self.task.cancel()

    def receive(self, data):
        if self.task is None or self.task.done() or data[:1] != b"\x3c":
            return
        if data[:2] == b"\x3c\x11" and self.accepting:
            self.invalidate("Unexpected stream status; restart required")
        elif data[:2] in (b"\x3c\x02", b"\x3c\x13"):
            if not self.accepting:
                if self.start_pending:
                    self.invalidate("Feature arrived before START acknowledgement")
                return
            if len(data) > 512 or self.queue.full():
                self.invalidate("Input queue capacity exceeded")
            else:
                self.queue.put_nowait((data, time.monotonic_ns()))

    async def start(self):
        if not self.transport.ready or self.transport.closing:
            raise RingError("not_ready")
        if self.task is not None and not self.task.done():
            raise RingError("busy", "Touchpad is already starting/running/stopping")
        if self.transport.client.mtu_size < 67:
            raise RingError("not_ready", "Raw2 status requires BLE MTU >= 67")
        self._clear()
        self.error = None
        self._entered = self._stop_requested = False
        self.session = str(uuid.uuid4())
        self.generation = self.sequence = self.ordinal = 0
        self.first = asyncio.get_running_loop().create_future()
        self._status("STARTING", "Loading paired Raw2 model")
        self.task = asyncio.create_task(self._run(), name="ring-touchpad")
        try:
            await asyncio.shield(self.first)
        except BaseException:
            await self.stop()
            raise

    async def stop(self):
        task = self.task
        if task is not None and not task.done():
            self._clear()
            if self.error is None:
                self._status("STOPPING")
            if not self._stop_requested:
                self._stop_requested = True
                if self._entered:
                    task.cancel()
            await asyncio.shield(task)

    def _started(self, state):
        if state.result or state.state != 1 or state.deadline:
            raise RingError("device_error",
                            f"Ring refused Raw2 START: result={state.result}, state={state.state}, deadline={state.deadline}",
                            device_code=state.result, response=state, phase="ack")
        self.tracker = p.Tracker(state)
        self.accepting = True  # publish synchronously before the next notification

    async def _stop_device(self, *, timeout=3.0):
        state = await self.transport.request(p.STOP, (0x3C, 0x11), p.status, shutdown=True, timeout=timeout)
        if not state.stopped:
            raise RingError("device_error", "Ring did not acknowledge STOP", device_code=state.result)

    async def _initial_stop(self):
        info = self.transport.device_info
        if info is None or info.hardware_revision != 1:
            await self._stop_device()
            return
        epoch = self.transport.generation
        loop = asyncio.get_running_loop()
        deadline = loop.time()+self.INITIAL_STOP_BUDGET
        last_error = None
        while True:
            if not self.transport.ready or self.transport.closing or self.transport.generation != epoch:
                raise RingError("disconnected", "Connection changed while waiting for Ringo initialization")
            remaining = deadline-loop.time()
            if remaining <= 0:
                raise RingError("timeout", "Ringo initial STOP acknowledgement deadline expired; check pairing/security and notification readiness",
                                phase="ack") from last_error
            try:
                await self._stop_device(timeout=min(self.INITIAL_STOP_ATTEMPT, remaining))
                return
            except RingError as exc:
                # Retry only after a completed BLE write with no business ACK.
                # Device rejection, bad protocol/model identity and write errors
                # must remain visible; none indicate a transient security delay.
                if exc.reason != "timeout" or exc.phase != "ack":
                    raise
                last_error = exc
                self._status("STARTING", "Waiting for Ringo protected STOP acknowledgement")
                await asyncio.sleep(min(self.INITIAL_STOP_RETRY_DELAY, max(0, deadline-loop.time())))

    async def _run(self):
        self._entered = True
        ticker = None
        attempted = False
        try:
            if self._stop_requested:
                raise asyncio.CancelledError()
            factory = self.model_factory
            if factory is None:
                from .model import TouchpadModel
                factory = TouchpadModel
            model = await asyncio.wait_for(asyncio.to_thread(factory), 10)
            await self._initial_stop()
            self._status("STARTING_STREAM", "Waiting for START acknowledgement")
            attempted = True
            self.start_pending = True
            try:
                await self.transport.request(p.START, (0x3C, 0x11), p.status, hook=self._started)
            finally:
                self.start_pending = False
            ticker = asyncio.create_task(self._tick())
            while True:
                data, arrival = await asyncio.wait_for(self.queue.get(), 3 if self.ordinal == 0 else 1)
                batch = p.tokens(data)
                resets = self.tracker.accept_all(batch)
                for token, reset in zip(batch, resets):
                    if time.monotonic_ns()-arrival > 200_000_000:
                        raise RingError("stream_failed", "Input backlog exceeds 200 ms")
                    if reset:
                        if self.ordinal:
                            self.generation += 1
                        self.ordinal = 0
                        self.engine.clear()
                    # One in-flight step per model. A cancelled/timed-out model is discarded.
                    def infer(token=token, reset=reset):
                        if reset:
                            model.reset_for_epoch()
                        return model.step(token.features)
                    output = await asyncio.wait_for(asyncio.to_thread(infer), .2)
                    if not self.accepting:
                        raise RingError("stream_failed", "Stream invalidated during inference")
                    if reset:
                        self._status("RUNNING")
                    self.engine.publish(self.ordinal*2+1, arrival, output.reshape(-1).tolist(), time.monotonic_ns())
                    self.ordinal += 1
                    self.engine.round()
                    if not self.first.done():
                        self.first.set_result(None)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.error = exc if isinstance(exc, RingError) else RingError("stream_failed", str(exc) or "Input/inference timeout")
        finally:
            self._stop_requested = True
            self._clear()
            if ticker is not None:
                ticker.cancel()
                await asyncio.gather(ticker, return_exceptions=True)
            if attempted and self.transport.ready:
                try:
                    await self._stop_device()
                except Exception as exc:
                    if self.error is None:
                        self.error = RingError("stop_unconfirmed", str(exc))
            if self.error:
                self._status("FAILED", str(self.error))
            else:
                self._status("STOPPED")
            if not self.first.done():
                self.first.set_exception(self.error or RingError("cancelled", "Touchpad start cancelled"))
                self.first.exception()

    async def _tick(self):
        try:
            while True:
                self.engine.round()
                now = time.monotonic_ns()
                delay = max(0, min(.005, (self.engine.next_wake(now)-now)/1e9))
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.invalidate(str(exc))

    def _meta(self, source):
        self.sequence += 1
        return EventMeta(self.session, self.generation, self.sequence, source)

    def _event(self, kind, now, **data):
        if not self.accepting or self.error:
            return
        if kind == "motion":
            event = Motion(self._meta(data["source"]), data["frame"], data["dx"], data["dy"], data["mode"], data["id"])
        elif kind == "contact":
            event = ContactUpdate(self._meta(data["source"]), data["id"], data["phase"], data["mode"], data["range"])
        else:
            preview = kind == "preview"
            contacts = (data["contact"],) if preview else data["contacts"]
            event = Click(self._meta(contacts[-1].up), data["id"], "LEFT" if preview else data["button"], len(contacts), preview)
        self.emit(event)
