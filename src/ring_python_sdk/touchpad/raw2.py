"""Provider Raw2 receiver on the product's existing BLE connection.

The vendor package stays unchanged. Only 0x3c packets are routed here; audio,
firmware gestures and all other sensors retain their existing session owner.
"""
import asyncio
from dataclasses import dataclass, replace
import logging
import sys
import threading
import time

from ring_python_sdk.sdk_provider import (Click, ContactUpdate, Engine, EventMeta, Motion, RingError, Touchpad,
                                          clock_ns, delta, host_seconds)

from .core import ClickDetector
from .events import TouchpadClick, TouchpadClickVerdict, TouchpadContact, TouchpadMove, TouchpadStats
from .events import Raw2PointerClick, Raw2PointerContact, Raw2PointerMove, Raw2Reset

log = logging.getLogger(__name__)

# Host adaptations validated on Windows only. macOS keeps the provider's own
# behavior: no start budget, no timer change, a firmware stop ends quietly.
WINDOWS = sys.platform == 'win32'
# The SDK waits indefinitely for the first token, so the Windows host sets the budget.
RAW2_START_TIMEOUT_S = 30.


class FrameEngine(Engine):
    """Observe every model frame for existing stroke geometry, even zero moves."""
    def __init__(self, sink, frame):
        self.frame = frame
        super().__init__(sink)

    def clear(self):
        super().clear()
        self.stroke_clicks = ClickDetector()

    def recognize(self, recognition, now):
        super().recognize(recognition, now)
        self.frame(recognition, self.stroke_clicks)


@dataclass(frozen=True)
class ClickMeta(EventMeta):
    emitted_ns: int
    first_down_ns: int


class StrokeTouchpad(Touchpad):
    """Extend the paired receiver with the full frames and click timing the product needs."""
    # A disconnected Mac may create a new asyncio loop before an old native call returns.
    _model_running = threading.Lock()

    def __init__(self, stream, model_factory):
        super().__init__(stream.transport, stream._pointer_event, stream._status,
                         model_factory, stream._diagnostics)
        self.stream = stream
        self.output_after_ns = 0

    async def _model_call(self, function, timeout):
        if not self._model_running.acquire(blocking=False):
            raise RingError('busy', 'Previous Raw2 native model call is still running')

        def run():
            try:
                return function()
            finally:
                self._model_running.release()

        return await super()._model_call(run, timeout)

    def _event(self, kind, now, **data):
        self.click_timing = None
        if kind in ('preview', 'click'):
            first = data['contact'] if kind == 'preview' else data['contacts'][0]
            self.click_timing = (now, first.down)
        super()._event(kind, now, **data)

    def _meta(self, source):
        meta = super()._meta(source)
        if self.click_timing is None:
            return meta
        return ClickMeta(meta.session_id, meta.generation, meta.sequence, source, *self.click_timing)

    def _report(self, issue, message, severity='WARNING', **counts):
        if issue == 'INPUT_TIMEOUT' and self.status.stage == 'RUNNING':
            self.output_after_ns = clock_ns()
            self.engine.stroke_clicks = ClickDetector()
            self.stream._queue_interrupt()
        super()._report(issue, message, severity, **counts)

    async def _run(self):
        # start() creates a fresh interaction engine for each SDK session.
        self.engine = FrameEngine(self._event, self.stream._queue_frame)
        await super()._run()


class Raw2Stream:
    protocol = 'raw2'
    _callback_running = threading.Lock()

    def __init__(self, session, on_event, *, on_pointer_move=None, on_stats=None,
                 on_end=None, on_discarded_event=None, duration_s=None, model_factory=None):
        self.on_event, self.pointer = on_event, on_pointer_move
        self.on_stats, self.on_end = on_stats, on_end
        self.on_discarded_event = on_discarded_event
        self.duration_s = duration_s
        self.closed = False
        self.error = None
        self.stats = TouchpadStats()
        self._last_stats = self._last_data = 0.
        self._watcher = None
        self._key = None
        self._packets = 0
        self.token_packets = 0
        self.transport = session._sdk_transport
        if self.transport is None or not self.transport.ready:
            raise RingError('not_ready', 'Raw2 requires the validated Ring SDK BLE connection')
        # Scope cleanup evidence to this stream, on the shared connection.
        self.transport.stop_confirmed = False
        self._timer_release = None
        if model_factory is None:
            # None on macOS: the provider receiver loads its own model there.
            from .raw2_model import default_model_factory
            model_factory = default_model_factory()
        self.receiver = StrokeTouchpad(self, model_factory)

    def _deliver(self, callback, value):
        # RingCallbacks is shared by all connections. A stuck application callback
        # must release the actual worker before another session starts.
        with self._callback_running:
            if (not self.closed and self.receiver.accepting
                    and self._key == (self.receiver.session, self.receiver.generation)):
                callback(value)

    def submit(self, packet):
        if not self.closed:
            self._last_data = time.monotonic()
            self._packets += 1
            if packet[:2] in (b'\x3c\x02', b'\x3c\x13'):
                self.token_packets += 1
                if self.token_packets == 1:
                    log.info('[RAW2_INPUT] first_packet bytes=%d accepting=%s',
                             len(packet), self.receiver.accepting)
            # ACKs were already matched by the owning SDK transport before
            # the product demux. Do not feed packets back into Transport.
            self.receiver.receive(packet)

    async def _status(self, status):
        log.info('[RAW2_STATE] stage=%s generation=%d token_packets=%d stop_confirmed=%s message=%s',
                 status.stage, status.generation, self.token_packets, self.transport.stop_confirmed, status.message)
        if status.stage == 'RUNNING' and not self.closed:
            key = (status.session_id, status.generation)
            if key != self._key:
                self._key = key
                await self.receiver.delivery.invoke(self._reset, (key, time.monotonic()))

    def _queue_interrupt(self):
        receiver = self.receiver
        key = (receiver.session, receiver.generation)
        interrupted_at = host_seconds(receiver.output_after_ns)

        self._enqueue(self._reset, (key, interrupted_at))

    def _reset(self, value):
        key, timestamp = value
        self.stats = replace(self.stats, resets=self.stats.resets + int(self.stats.ready is True), ready=True)
        self._deliver(self.on_event, Raw2Reset('reset', 0, timestamp, session_id=key[0], generation=key[1]))
        self._report(force=True)

    async def _diagnostics(self, value):
        if value.issue:
            level = getattr(logging, value.severity)
            log.log(level, '[RAW2_DIAGNOSTICS] generation=%d issue=%s active=%s recovered=%s counters=%s message=%s',
                    value.generation, value.issue, value.active_issues, value.recovered_issues,
                    value.counters, value.message)

    def _report(self, *, force=False):
        now = time.monotonic()
        if self.on_stats and (force or now - self._last_stats >= .5):
            self._last_stats = now
            self._deliver(self.on_stats, replace(self.stats, packets=self._packets, last_data_age_s=max(0., now-self._last_data)))

    def _pointer_event(self, event):
        if (self.closed or self.pointer is None or self._key != (event.meta.session_id, event.meta.generation)
                or self.receiver.generation != event.meta.generation
                or event.meta.source_time_ns < self.receiver.output_after_ns):
            return
        meta = dict(session_id=event.meta.session_id, generation=event.meta.generation,
                    sequence=event.meta.sequence)
        if isinstance(event, Motion):
            value = Raw2PointerMove(event.dx, event.dy, 0., event.frame_id+1,
                                 host_seconds(event.meta.source_time_ns), mode=event.mode,
                                 episode_id=event.episode_id, **meta)
        elif isinstance(event, ContactUpdate):
            if event.range.start_time_ns < self.receiver.output_after_ns:
                return
            value = Raw2PointerContact(event.phase, event.mode, event.episode_id,
                self.receiver.ordinal*2, host_seconds(event.meta.source_time_ns),
                host_seconds(event.range.start_time_ns),
                None if event.range.end_time_ns is None else host_seconds(event.range.end_time_ns),
                start_step=event.range.start_frame+1,
                end_step=None if event.range.end_frame_exclusive is None else event.range.end_frame_exclusive+1,
                **meta)
        elif isinstance(event, Click):
            # Pairing intentionally waits before confirming a single click.
            # Expiry starts at formal emission; retain source time for diagnostics.
            if event.meta.first_down_ns < self.receiver.output_after_ns:
                return
            value = Raw2PointerClick(self.receiver.ordinal*2, host_seconds(event.meta.emitted_ns),
                                  button=event.button.lower(), count=event.count,
                                  click_id=event.click_id, source_timestamp=host_seconds(event.meta.source_time_ns),
                                  is_preview=event.is_preview, **meta)
        else:
            return
        self._deliver(self.pointer, value)

    def _queue_frame(self, frame, detector):
        self._enqueue(self._frame, (self.receiver.generation, frame, detector))

    def _enqueue(self, callback, value):
        receiver = self.receiver
        key = (receiver.session, receiver.generation)
        # Stroke and pointer callbacks share the SDK FIFO and its generation/stop fence.
        async def dispatch():
            if not self.closed and receiver.accepting and self._key == key:
                await receiver.delivery.invoke(callback, value)
        if not receiver.delivery.offer(dispatch):
            receiver.invalidate('Callback delivery capacity exceeded', 'DELIVERY_OVERFLOW')

    def _frame(self, value):
        generation, frame, detector = value
        if (self.closed or generation != self.receiver.generation
                or frame.source < self.receiver.output_after_ns):
            return
        step = frame.frame+1
        # Stroke recognition keeps its full 200 Hz geometry and its own tap
        # verdicts. It never feeds those legacy clicks into Raw2 mouse output.
        clicks = detector.probability(step, frame.touch)
        clicks += detector.movement(step, (frame.x, frame.y))
        stamp = host_seconds(frame.source)
        edges, verdicts = tuple(detector.contact_edges), tuple(detector.contact_verdicts)
        detector.contact_edges.clear()
        detector.contact_verdicts.clear()
        for down, edge, confirmed in edges:
            self._deliver(self.on_event, TouchpadContact('down' if down else 'up', edge, stamp, confirmed))
        dx, dy = delta(frame.x, frame.y)
        self._deliver(self.on_event, TouchpadMove(dx, dy, frame.touch, step, stamp))
        for click in clicks:
            self._deliver(self.on_event, TouchpadClick(click['step'], stamp))
        for start, end, is_click in verdicts:
            self._deliver(self.on_event, TouchpadClickVerdict(start, end, is_click, stamp))
        self.stats = replace(self.stats, packets=self._packets, tokens=self.stats.tokens + int(step % 2 == 0),
                             contact_probability=frame.touch)
        self._report()

    async def prepare(self):
        if self._callback_running.locked():
            raise RingError('busy', 'Previous Raw2 application callback is still running')
        if WINDOWS and self._timer_release is None:
            from .raw2_model import hold_windows_timer
            self._timer_release = hold_windows_timer()
        try:
            async with asyncio.timeout(RAW2_START_TIMEOUT_S if WINDOWS else None):
                await self.receiver.start()
        except TimeoutError:
            seconds = f'{RAW2_START_TIMEOUT_S:g}'
            # The tracker exists once the ring acknowledged START.
            if hasattr(self.receiver, 'tracker') and self.token_packets == 0:
                self.error = RingError('input_timeout', f'戒指已确认触摸板 START，但 {seconds} 秒内未收到首个 Raw2 数据包')
            else:
                self.error = RingError('timeout', f'触摸板启动超过 {seconds} 秒')
            raise self.error from None
        if self.closed:
            await self.receiver.stop()
            raise self.receiver.error or RingError('cancelled', 'Touchpad start cancelled')

    def start(self):
        self._watcher = asyncio.create_task(self._watch(), name='raw2-session-watch')

    async def _watch(self):
        try:
            if self.duration_s is None:
                await asyncio.shield(self.receiver.task)
            else:
                try:
                    await asyncio.wait_for(asyncio.shield(self.receiver.task), self.duration_s)
                except TimeoutError:
                    await self.receiver.stop()
            self.error = self.receiver.error
            if (WINDOWS and self.error is None and not self.closed
                    and 'DEVICE_STOPPED' in self.receiver.reporter.totals):
                # Firmware ended the stream by itself: not a user stop.
                self.error = RingError('device_error', '戒指固件已停止触摸板数据流，请重新开启触摸板')
        finally:
            if not self.closed and self.on_end:
                self.on_end(self)

    def stop_now(self):
        self.closed = True
        self.receiver._clear()
        # Never cancel the receiver a second time while its finally block is
        # awaiting the device STOP ACK. touchpad_off/close can both call here.
        if not self.receiver._stop_requested:
            self.receiver._stop_requested = True
            if self.receiver.task and not self.receiver.task.done() and self.receiver._entered:
                self.receiver.task.cancel()

    async def close(self):
        self.stop_now()
        try:
            await self.receiver.stop()
        finally:
            release, self._timer_release = self._timer_release, None
            if release is not None:
                release()
        self.error = self.error or self.receiver.error
        if self._watcher and self._watcher is not asyncio.current_task():
            await asyncio.gather(self._watcher, return_exceptions=True)
