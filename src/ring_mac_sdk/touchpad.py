import asyncio
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

_MODEL_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="RingModel")

from . import protocol as p
from .errors import RingError
from .interaction import Engine
from .diagnostics import Reporter
from .delivery import Delivery
from .types import Click, ContactUpdate, EventMeta, Motion, TouchpadStatus


class Touchpad:
    # Only the idempotent initialization STOP may be retried while Ringo's
    # owner-encrypted session becomes ready. START and cleanup STOP never retry.
    INITIAL_STOP_BUDGET = 12.0
    INITIAL_STOP_ATTEMPT = 3.0
    INITIAL_STOP_RETRY_DELAY = .25

    def __init__(self, transport, emit, status_changed, model_factory=None, diagnostics_changed=None):
        self.transport, self.emit, self.status_changed = transport, emit, status_changed
        self.diagnostics_changed = diagnostics_changed
        self.reporter = Reporter("")
        self.delivery = Delivery(self._report, self.reporter.healthy)
        self.query_task = None
        self.model_pending = None
        self.model_timeout = 3.0
        self.first_input_timeout = 3.0
        self.input_timeout = 1.0
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

    @property
    def diagnostics(self):
        return self.reporter.snapshot(self.generation)

    def _report(self, issue, message, severity="WARNING", **counts):
        self.reporter.record(issue, message, severity, **counts)
        self._flush_diagnostics()

    def _flush_diagnostics(self):
        reporter, delivery = self.reporter, self.delivery
        if reporter.due():
            async def notify():
                if reporter is self.reporter and reporter.due():
                    value = reporter.snapshot(self.generation, take=True)
                    await delivery.invoke(self.diagnostics_changed, value, True)
            delivery.offer(notify, diagnostic=True)
        delivery.flush_log()

    def _status(self, stage, message=""):
        self.status = TouchpadStatus(stage, message, self.session, self.generation)
        value, delivery, reporter = self.status, self.delivery, self.reporter
        async def notify():
            if value.session_id == self.session:
                if stage in ('STOPPED', 'FAILED'):
                    await delivery.invoke(self.diagnostics_changed, reporter.snapshot(value.generation, take=True), True)
                await delivery.invoke(self.status_changed, value, stage in ('STOPPED', 'FAILED'))
        if stage in ('STOPPED', 'FAILED'):
            delivery.finish(notify)
        elif not delivery.offer(notify):
            self.invalidate('Callback delivery capacity exceeded', 'DELIVERY_OVERFLOW')

    async def _model_call(self, function, timeout):
        self.model_pending = asyncio.ensure_future(asyncio.get_running_loop().run_in_executor(_MODEL_EXECUTOR, function))
        self.model_pending.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        try:
            return await asyncio.wait_for(asyncio.shield(self.model_pending), timeout)
        except TimeoutError:
            self._report('STARTUP_TIMEOUT' if timeout == 20 else 'MODEL_TIMEOUT', 'Model call timed out', 'ERROR')
            raise RingError('model_timeout', 'Model call timed out; wait for native call to finish before restart')

    async def _inspect_device(self):
        reporter = self.reporter
        try:
            state = await self.transport.request(b'\x3c\x03', (0x3c, 0x11), p.status)
            if state.session != self.tracker.session:
                self.invalidate('Device STATUS session mismatch')
                return
            device = dict(state=state.state, result=state.result, session=state.session,
                          sent_tokens=state.sent, dropped_tokens=state.dropped)
            reporter.device = device
            detail_error = None
            try:
                raw = await self.transport.request(b'\x3c\x04', (0x3c, 0x12), p.diagnostics)
                if raw['session'] != state.session:
                    raise RingError('protocol', 'Device diagnostics session mismatch')
                reporter.device.update(raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                detail_error = exc
            if not self.accepting or reporter is not self.reporter:
                return
            if state.state != 1 or state.result:
                if state.state == 0 and state.result == 0:
                    self._report('DEVICE_STOPPED', 'Device stream stopped', 'INFO')
                    await self.stop()
                else:
                    self.invalidate('Device stream fault: '+reporter.device.get('fault_name', 'unconfirmed'), 'DEVICE_STOPPED')
            elif detail_error:
                self._report('DEVICE_QUERY_FAILED', 'Detailed diagnostics not confirmed: '+str(detail_error))
            else:
                reporter.recover('DEVICE_QUERY_FAILED')
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if reporter is self.reporter and self.accepting:
                self._report('DEVICE_QUERY_FAILED', 'Device query not confirmed: '+str(exc))

    def _clear(self):
        self.accepting = False
        self.start_pending = False
        self.engine.clear()
        while not self.queue.empty():
            self.queue.get_nowait()

    def invalidate(self, message, issue="SDK_FAILURE"):
        if self.task is not None and not self.task.done():
            self._report(issue, message, "ERROR")
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
            try:
                state = p.status(data)
                if state.session != self.tracker.session:
                    raise RingError('protocol', 'STATUS session mismatch')
                if state.state != 1 or state.result:
                    self.invalidate('Unexpected device stop/fault', 'DEVICE_STOPPED')
            except Exception as exc:
                self.invalidate(str(exc))
        elif data[:2] in (b"\x3c\x02", b"\x3c\x13"):
            if not self.accepting:
                if self.start_pending:
                    self.invalidate("Feature arrived before START acknowledgement")
                return
            if len(data) > 512:
                self.invalidate("Input packet exceeds 512 bytes")
                return
            try:
                skipped = 0
                if self.queue.full():
                    oldest, _, lost = self.queue.get_nowait()
                    skipped = lost+len(p.tokens(oldest))
                    if not self.queue.empty():
                        head = self.queue._queue[0]
                        self.queue._queue[0] = (head[0], head[1], head[2]+skipped)
                        skipped = 0
                    self._report('INPUT_DROPPED', 'Input queue full; discarded oldest packet', input_dropped_packets=1)
                self.queue.put_nowait((data, time.monotonic_ns(), skipped))
            except Exception as exc:
                self.invalidate(str(exc))

    async def start(self):
        if not self.transport.ready or self.transport.closing:
            raise RingError("not_ready")
        if self.task is not None and not self.task.done():
            raise RingError("busy", "Touchpad is already starting/running/stopping")
        if self.transport.client.mtu_size < 67:
            raise RingError("not_ready", "Raw2 status requires BLE MTU >= 67")
        if self.model_pending is not None and not self.model_pending.done():
            raise RingError('busy', 'Previous native model call is still running')
        if self.delivery.task is not None and not self.delivery.task.done():
            raise RingError('busy', 'Previous callback delivery is still running')
        self._clear()
        self.error = None
        self._entered = self._stop_requested = False
        self.session = str(uuid.uuid4())
        self.generation = self.sequence = self.ordinal = 0
        self.reporter = Reporter(self.session)
        self.delivery = Delivery(self._report, self.reporter.healthy)
        self.engine = Engine(self._event)
        self.seen_motion = self.seen_clicks = 0
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
            startup_deadline = asyncio.get_running_loop().time()+20
            model = await self._model_call(factory, 20)
            async with asyncio.timeout_at(startup_deadline):
                await self._initial_stop()
            self._status("STARTING_STREAM", "Waiting for START acknowledgement")
            attempted = True
            self.start_pending = True
            try:
                async with asyncio.timeout_at(startup_deadline):
                    await self.transport.request(p.START, (0x3C, 0x11), p.status, hook=self._started)
            finally:
                self.start_pending = False
            ticker = asyncio.create_task(self._tick())
            self.last_input = time.monotonic()
            self.last_query = 0.
            self.input_timed_out = False
            while True:
                try:
                    data, arrival, skipped = await asyncio.wait_for(self.queue.get(), .05)
                except TimeoutError:
                    limit = self.first_input_timeout if self.ordinal == 0 else self.input_timeout
                    if time.monotonic()-self.last_input > limit:
                        if not self.input_timed_out:
                            self.input_timed_out = True
                            self._report('INPUT_TIMEOUT', 'Input quiet; continuing to wait')
                        if (self.query_task is None or self.query_task.done()) and time.monotonic()-self.last_query >= 3:
                            self.last_query = time.monotonic()
                            self.query_task = asyncio.create_task(self._inspect_device())
                    continue
                batch = p.tokens(data)
                resets, clocks = self.tracker.accept_recoverable(batch, skipped)
                if clocks:
                    self._report('CLOCK_ANOMALY', 'Device clock anomaly; continuing', clock_anomalies=clocks)
                else:
                    self.reporter.healthy('CLOCK_ANOMALY')
                if not skipped:
                    self.reporter.healthy('INPUT_DROPPED')
                self.last_input = arrival/1e9
                counted_late = False
                for token, reset in zip(batch, resets):
                    age_ms = max(0, (time.monotonic_ns()-arrival)/1e6)
                    self.reporter.counts['max_input_age_ms'] = max(age_ms, self.reporter.counts.get('max_input_age_ms', 0))
                    if age_ms > 200 and not counted_late:
                        counted_late = True
                        self._report('INPUT_BACKLOG', 'Input older than 200 ms; continuing', input_late_packets=1)
                    elif age_ms <= 200:
                        self.reporter.healthy('INPUT_BACKLOG')
                        self.reporter.healthy('INPUT_TIMEOUT')
                        self.input_timed_out = False
                    if reset:
                        if self.ordinal:
                            self.generation += 1
                            self.delivery.discard()
                            self.reporter.counts['recoveries'] = self.reporter.counts.get('recoveries', 0)+1
                        self.ordinal = 0
                        self.engine.clear()
                    # One in-flight step per model. A cancelled/timed-out model is discarded.
                    def infer(token=token, reset=reset):
                        if reset:
                            model.reset_for_epoch()
                        return model.step(token.features)
                    output = await self._model_call(infer, self.model_timeout)
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
            if not self.reporter.fatal:
                self._report("STARTUP_TIMEOUT" if isinstance(exc, TimeoutError) else "SDK_FAILURE", str(exc), "ERROR")
            self.error = exc if isinstance(exc, RingError) else RingError("stream_failed", str(exc) or "Input/inference timeout")
        finally:
            self._stop_requested = True
            self._clear()
            if ticker is not None:
                ticker.cancel()
                await asyncio.gather(ticker, return_exceptions=True)
            if self.query_task and self.query_task is not asyncio.current_task():
                self.query_task.cancel()
                await asyncio.gather(self.query_task, return_exceptions=True)
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
                motion, clicks = self.engine.late_motion_frames, self.engine.clicks.late_candidates
                if motion != self.seen_motion or clicks != self.seen_clicks:
                    self._report('OUTPUT_LATE', 'Late motion/click candidate retained',
                                 late_motion_frames=motion-self.seen_motion, late_click_candidates=clicks-self.seen_clicks)
                    self.seen_motion, self.seen_clicks = motion, clicks
                elif self.accepting and time.monotonic()-getattr(self, 'last_input', 0) <= .2:
                    self.reporter.healthy('OUTPUT_LATE')
                self._flush_diagnostics()
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
        delivery = self.delivery
        async def dispatch():
            if (self.accepting and self.error is None and self.status.stage == 'RUNNING'
                    and (event.meta.session_id, event.meta.generation) == (self.session, self.generation)):
                await delivery.invoke(self.emit, event)
        if not delivery.offer(dispatch):
            self.invalidate('Callback delivery capacity exceeded', 'DELIVERY_OVERFLOW')
