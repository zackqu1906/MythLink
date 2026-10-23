"""Per-click receipts, written off the BLE, pointer and native delivery threads."""
from __future__ import annotations

import atexit
import json
import logging
import os
import queue
import sys
import threading
import time
import uuid


_records = queue.SimpleQueue()
_writer = None
_start_lock = threading.Lock()


def _write_records():
    while True:
        item = _records.get()
        if isinstance(item, threading.Event):
            item.set()
            continue
        record, native = item
        try:
            message = '[TOUCHPAD_CLICK] ' + json.dumps(record, ensure_ascii=False, allow_nan=False)
            if native:
                # NativeAccessChannel already collects this private stderr pipe.
                sys.stderr.write(message + '\n')
                sys.stderr.flush()
            else:
                logging.getLogger(__name__).info(message)
        except Exception:
            pass  # An unavailable diagnostic sink must never stop input.


def start_click_diagnostics():
    global _writer
    if _writer is None:
        with _start_lock:
            if _writer is None:
                _writer = threading.Thread(target=_write_records, name='TouchpadClickLog', daemon=True)
                _writer.start()


def click_record(record, *, native=False):
    start_click_diagnostics()
    _records.put(({**record, 'process': os.getpid(), 'recorded_at': time.monotonic()}, native))


def flush_click_diagnostics(timeout=1.):
    """A bounded drain for orderly shutdown and diagnostic tests, never input."""
    if _writer is None:
        return True
    barrier = threading.Event()
    _records.put(barrier)
    return barrier.wait(timeout)


atexit.register(flush_click_diagnostics)


class ClickTrace:
    def __init__(self, event, *, mode='pointer'):
        self.submitted_at = time.monotonic()
        self.event_timestamp = event.timestamp
        self.fields = dict(click_id=uuid.uuid4().hex, step=event.step, mode=mode,
                           event_timestamp=event.timestamp, submitted_at=self.submitted_at)
        self.finished = False

    def record(self, stage, **fields):
        if stage == 'queued':
            self.fields['queued'] = True
        click_record({**self.fields, 'stage': stage, **fields})

    def dequeued(self, *, queue_depth):
        now = time.monotonic()
        self.fields.update(queue_ms=round((now-self.submitted_at)*1000, 3),
                           event_age_ms=round((now-self.event_timestamp)*1000, 3))
        self.record('dequeued', queue_depth=queue_depth)

    def finish(self, reason, *, outcome='discarded', **fields):
        if self.finished:
            return
        self.finished = True
        now = time.monotonic()
        self.fields.setdefault('queue_ms', round((now-self.submitted_at)*1000, 3)
                               if self.fields.get('queued') else 0.)
        self.record('result', outcome=outcome, reason=reason,
                    clicks_posted=fields.pop('clicks_posted', 0),
                    down_posts=fields.pop('down_posts', 0),
                    up_posts=fields.pop('up_posts', 0),
                    final_event_age_ms=round((now-self.event_timestamp)*1000, 3),
                    total_ms=round((now-self.submitted_at)*1000, 3), **fields)
