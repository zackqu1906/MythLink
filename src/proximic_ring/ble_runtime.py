"""Process-lifetime macOS BLE loop shared by discovery and Ring sessions.

CoreBluetooth can deliver callbacks after a scan or connection task finishes.
Closing an asyncio.run() loop at that point raises inside a PyObjC delegate and
aborts the process. Keep this daemon loop alive until process exit, including
between connections. Session cancellation still awaits its own SDK cleanup.
"""
from __future__ import annotations

import asyncio
import sys
import threading


class _MacBleRuntime:
    def __init__(self):
        self._ready = threading.Event()
        self._loop = None
        self._error = None
        self._thread = threading.Thread(target=self._run, name="MacBleRuntime", daemon=True)
        self._thread.start()

    def _run(self):
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
        except BaseException as exc:
            self._error = exc
            self._ready.set()
            return
        self._loop.call_soon(self._ready.set)
        self._loop.run_forever()

    def run(self, coroutine):
        self._ready.wait()
        if self._error is not None:
            coroutine.close()
            raise RuntimeError("Cannot start macOS BLE runtime") from self._error
        if threading.current_thread() is self._thread:
            coroutine.close()
            raise RuntimeError("BLE runtime must be awaited from a worker thread")
        # .result() waits through coroutine cancellation/finally cleanup. Do not
        # cancel the concurrent Future: that reports done before cleanup ends.
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop).result()


_lock = threading.Lock()
_mac_runtime = None


def run_ble(coroutine):
    """Blocking entry for non-UI workers; preserve Windows COM/thread ownership."""
    if sys.platform != "darwin":
        return asyncio.run(coroutine)
    global _mac_runtime
    with _lock:
        if _mac_runtime is None:
            _mac_runtime = _MacBleRuntime()
        runtime = _mac_runtime
    return runtime.run(coroutine)
