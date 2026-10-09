"""Bounded BLE queue; MNN runs on one dedicated executor, callbacks on BLE loop."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import time
from .events import TouchpadStats
from .processor import TouchpadProcessor


class TouchpadStream:
    def __init__(self, on_event, *, on_stats=None, model_path=None, duration_s=90., on_end=None,
                 on_pointer_move=None):
        self.on_event, self.on_stats, self.on_end = on_event, on_stats, on_end
        self.on_pointer_move = on_pointer_move
        self.model_path, self.duration_s = model_path, duration_s
        self.queue = asyncio.Queue(maxsize=64)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='ring-touchpad')
        self.processor = None
        self.task = None
        self.closed = False
        self.error = None
        self.stats = TouchpadStats()
        self.overflow = False
        self.overflows = 0
        self.started = None

    async def prepare(self):
        def prepare():
            processor = TouchpadProcessor(self.model_path)
            if self.on_pointer_move is not None:
                processor.enable_pointer_moves()
            return processor
        self.processor = await asyncio.get_running_loop().run_in_executor(
            self.executor, prepare)

    def start(self):
        self.started = time.monotonic()
        self.task = asyncio.create_task(self._run(), name='ring-touchpad')

    def submit(self, packet):
        if self.closed:
            return
        if self.queue.full():
            while not self.queue.empty():self.queue.get_nowait()
            self.overflow = True
            self.overflows += 1
        self.queue.put_nowait((bytes(packet), time.monotonic()))

    def stop_now(self):
        """Synchronous gate; no new event callbacks after this call."""
        self.closed = True
        if self.task and not self.task.done():self.task.cancel()

    async def close(self):
        self.stop_now()
        try:
            if self.task and self.task is not asyncio.current_task():
                try:await self.task
                except asyncio.CancelledError:pass
        finally:
            # An in-flight native inference cannot be cancelled. Wait off-loop.
            await asyncio.to_thread(self.executor.shutdown, wait=True, cancel_futures=True)
            self.processor = None

    async def _run(self):
        loop = asyncio.get_running_loop()
        stats_at = self.started
        try:
            while not self.closed:
                now = time.monotonic()
                if self.duration_s is not None and now-self.started >= self.duration_s:break
                items = []
                for _ in range(4):
                    if self.queue.empty():break
                    items.append(self.queue.get_nowait())
                reset, self.overflow = self.overflow, False
                def advance():
                    batch_start = time.monotonic()
                    cpu_start = time.thread_time()
                    queue_age = max((batch_start-arrival for _, arrival in items), default=0.)
                    if reset:self.processor.reset()
                    for packet, arrival in items:self.processor.feed(packet, arrival=arrival)
                    events = self.processor.poll()
                    pointer_events = self.processor.poll_pointer() if self.on_pointer_move is not None else []
                    stats = replace(self.processor.stats,
                                    inference_batch_ms=(time.monotonic()-batch_start)*1000,
                                    inference_thread_cpu_ms=(time.thread_time()-cpu_start)*1000,
                                    inference_batch_packets=len(items),
                                    packet_queue_age_ms=max(0.,queue_age)*1000)
                    return events, pointer_events, stats, self.processor.last_valid
                events, pointer_events, stats, last_valid = await loop.run_in_executor(self.executor, advance)
                if not items:
                    stats = replace(stats, inference_batch_ms=self.stats.inference_batch_ms,
                                    inference_thread_cpu_ms=self.stats.inference_thread_cpu_ms,
                                    inference_batch_packets=self.stats.inference_batch_packets,
                                    packet_queue_age_ms=self.stats.packet_queue_age_ms)
                self.stats = replace(stats, queue_overflows=self.overflows)
                now = time.monotonic()
                if self.closed:break
                if self.duration_s is not None and now-self.started >= self.duration_s:break
                if now-(self.started if last_valid is None else last_valid)>8:
                    raise TimeoutError('No valid touchpad tokens for 8 seconds')
                for event in pointer_events:
                    if self.closed:break
                    if time.monotonic()-event.timestamp <= .1:self.on_pointer_move(event)
                for event in events:
                    if self.closed:break
                    if time.monotonic()-event.timestamp <= .1:self.on_event(event)
                if now-stats_at >= .5 and not self.closed:
                    if self.on_stats:self.on_stats(self.stats)
                    stats_at = now
                await asyncio.sleep(.001)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = exc
        finally:
            self.closed = True
            if self.on_end:self.on_end(self)
