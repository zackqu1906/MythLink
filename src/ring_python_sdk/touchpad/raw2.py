"""Provider Raw2 receiver on the product's existing BLE connection.

The vendor package stays unchanged. Only 0x3c packets are routed here; audio,
firmware gestures and all other sensors retain their existing session owner.
"""
import asyncio
from dataclasses import replace
import logging
import time

from ring_mac_sdk.errors import RingError
from ring_mac_sdk.interaction import Engine, delta
from ring_mac_sdk.touchpad import Touchpad
from ring_mac_sdk.types import Click, ContactUpdate, Motion

from .core import ClickDetector
from .events import TouchpadClick, TouchpadClickVerdict, TouchpadContact, TouchpadMove, TouchpadStats
from .events import Raw2PointerClick, Raw2PointerContact, Raw2PointerMove, Raw2Reset

log = logging.getLogger(__name__)


class InputQueue(asyncio.Queue):
    """Expose the wait stage without changing the provider's time budgets."""
    def __init__(self, stream):
        super().__init__(maxsize=64)
        self.stream = stream

    async def get(self):
        self.stream.phase = ('first_token' if self.stream.receiver.ordinal == 0 else 'stream_input')
        item = await super().get()
        self.stream.phase = 'inference'
        return item


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
        self.frame(recognition, now, self.stroke_clicks)


class Raw2Stream:
    protocol = 'raw2'

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
        self.phase = 'model_load'
        self.token_packets = 0
        self.transport = session._sdk_transport
        if self.transport is None or not self.transport.ready:
            raise RingError('not_ready', 'Raw2 requires the validated ring_mac_sdk BLE connection')
        # Scope cleanup evidence to this stream, on the shared connection.
        self.transport.stop_confirmed = False
        self.receiver = Touchpad(self.transport, self._pointer_event, self._status, model_factory)
        self.receiver.queue = InputQueue(self)
        self.receiver.engine = FrameEngine(self.receiver._event, self._frame)

    def submit(self, packet):
        if not self.closed:
            self._last_data = time.monotonic()
            self.stats = replace(self.stats, packets=self.stats.packets + 1)
            if packet[:2] in (b'\x3c\x02', b'\x3c\x13'):
                self.token_packets += 1
                if self.token_packets == 1:
                    log.info('[RAW2_INPUT] first_packet bytes=%d accepting=%s',
                             len(packet), self.receiver.accepting)
            # ACKs were already matched by the owning SDK transport before
            # the product demux. Do not feed packets back into Transport.
            self.receiver.receive(packet)

    def _status(self, status):
        if status.stage == 'STARTING_STREAM':
            self.phase = 'start_ack'
        if status.stage == 'FAILED' and str(self.receiver.error) == 'Input/inference timeout':
            messages = {
                'first_token': ('input_timeout', '戒指已确认触摸板 START，但 3 秒内未收到首个 Raw2 数据包'),
                'stream_input': ('input_timeout', '触摸板 Raw2 数据断流超过 1 秒'),
                'inference': ('inference_timeout', '触摸板模型推理或调度超过 200 毫秒'),
                'model_load': ('model_timeout', '触摸板模型加载超过 10 秒'),
            }
            reason, message = messages.get(self.phase, ('stream_failed', str(self.receiver.error)))
            self.receiver.error = RingError(reason, message)
        log.info('[RAW2_STATE] stage=%s phase=%s token_packets=%d inferred_tokens=%d stop_confirmed=%s error=%s',
                 status.stage, self.phase, self.token_packets, self.receiver.ordinal,
                 self.transport.stop_confirmed, self.receiver.error)
        if status.stage == 'RUNNING':
            key = (status.session_id, status.generation)
            if key != self._key:
                self.stats = replace(self.stats, resets=self.stats.resets + int(self._key is not None),
                                     ready=True)
                self._key = key
                self.on_event(Raw2Reset('reset', 0, time.monotonic(),
                                             session_id=key[0], generation=key[1]))
            self._report(force=True)

    def _report(self, *, force=False):
        now = time.monotonic()
        if self.on_stats and (force or now - self._last_stats >= .5):
            self._last_stats = now
            self.on_stats(replace(self.stats, last_data_age_s=max(0., now-self._last_data)))

    def _pointer_event(self, event):
        if self.closed or self.pointer is None or self._key != (event.meta.session_id, event.meta.generation):
            return
        meta = dict(session_id=event.meta.session_id, generation=event.meta.generation,
                    sequence=event.meta.sequence)
        if isinstance(event, Motion):
            value = Raw2PointerMove(event.dx, event.dy, 0., event.frame_id+1,
                                 event.meta.source_time_ns/1e9, mode=event.mode,
                                 episode_id=event.episode_id, **meta)
        elif isinstance(event, ContactUpdate):
            value = Raw2PointerContact(event.phase, event.mode, event.episode_id,
                self.receiver.ordinal*2, event.meta.source_time_ns/1e9,
                event.range.start_time_ns/1e9,
                None if event.range.end_time_ns is None else event.range.end_time_ns/1e9, **meta)
        elif isinstance(event, Click):
            # Pairing intentionally waits before confirming a single click.
            # Expiry starts at formal emission; retain source time for diagnostics.
            value = Raw2PointerClick(self.receiver.ordinal*2, time.monotonic(),
                                  button=event.button.lower(), count=event.count,
                                  click_id=event.click_id, source_timestamp=event.meta.source_time_ns/1e9,
                                  is_preview=event.is_preview, **meta)
        else:
            return
        self.pointer(value)

    def _frame(self, frame, now, detector):
        if self.closed:
            return
        step = frame.frame+1
        if now-frame.source > 200_000_000:
            detector.reset()
            self.on_event(TouchpadContact('reset', step, time.monotonic()))
            return
        # Stroke recognition keeps its full 200 Hz geometry and its own tap
        # verdicts. It never feeds those legacy clicks into Raw2 mouse output.
        clicks = detector.probability(step, frame.touch)
        clicks += detector.movement(step, (frame.x, frame.y))
        stamp = frame.source/1e9
        for down, edge, confirmed in detector.contact_edges:
            self.on_event(TouchpadContact('down' if down else 'up', edge, stamp, confirmed))
        detector.contact_edges.clear()
        dx, dy = delta(frame.x, frame.y)
        self.on_event(TouchpadMove(dx, dy, frame.touch, step, stamp))
        for click in clicks:
            self.on_event(TouchpadClick(click['step'], stamp))
        for start, end, is_click in detector.contact_verdicts:
            self.on_event(TouchpadClickVerdict(start, end, is_click, stamp))
        detector.contact_verdicts.clear()
        self.stats = replace(self.stats, tokens=self.stats.tokens + int(step % 2 == 0),
                             contact_probability=frame.touch)
        self._report()

    async def prepare(self):
        await self.receiver.start()
        if self.closed:
            await self.receiver.stop()
            raise RuntimeError('Touchpad startup cancelled')

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
        await self.receiver.stop()
        self.error = self.error or self.receiver.error
        if self._watcher and self._watcher is not asyncio.current_task():
            await asyncio.gather(self._watcher, return_exceptions=True)
