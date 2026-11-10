"""Bounded serial callback delivery, outside the BLE/model event loop."""
import asyncio
import inspect
import logging
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

_CALLBACK_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="RingCallbacks")

log = logging.getLogger(__name__)

class Delivery:
    def __init__(self, report, healthy, capacity=256):
        self.pending = deque()
        self.notice = None
        self.task = None
        self.report, self.healthy, self.capacity = report, healthy, capacity
        self.closed = False
        self.errors = self.suppressed = 0
        self.last_log = 0

    def offer(self, action, diagnostic=False):
        if self.closed:
            return True
        if diagnostic:
            self.notice = action
        elif len(self.pending) >= self.capacity:
            return False
        else:
            self.pending.append(action)
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run())
        return True

    def discard(self):
        self.pending.clear()

    def finish(self, action):
        self.pending.clear()
        self.notice = None
        self.pending.append(action)
        self.closed = True
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run())

    async def invoke(self, callback, value, diagnostic=False):
        if callback is None:
            return
        start = time.monotonic()
        try:
            if inspect.iscoroutinefunction(callback):
                await callback(value)
            else:
                result = await asyncio.get_running_loop().run_in_executor(_CALLBACK_EXECUTOR, callback, value)
                if inspect.isawaitable(result):
                    await result
        except Exception:
            self.errors += 1
            if self.errors == 1:
                log.exception('Touchpad callback failed')
                self.last_log = time.monotonic()
            else:
                self.suppressed += 1
            if not diagnostic:
                self.report('CALLBACK_EXCEPTION', 'Application callback exception isolated', callback_exceptions=1)
        else:
            if not diagnostic:
                self.healthy('CALLBACK_EXCEPTION')
        if not diagnostic:
            if time.monotonic()-start > .05:
                self.report('CALLBACK_SLOW', 'Application callback exceeded 50 ms')
            else:
                self.healthy('CALLBACK_SLOW')
        self.flush_log()

    def flush_log(self, force=False):
        if self.suppressed and (force or time.monotonic()-self.last_log >= 1):
            log.error('%s additional callback exceptions (stacks suppressed)', self.suppressed)
            self.suppressed = 0
            self.last_log = time.monotonic()

    async def run(self):
        while self.notice is not None or self.pending:
            if self.notice is not None:
                action, self.notice = self.notice, None
            else:
                action = self.pending.popleft()
            await action()
        if self.closed:
            self.flush_log(True)
