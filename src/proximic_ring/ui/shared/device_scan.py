"""A cancellable BLE scan whose platform cleanup stays off the UI thread."""
from __future__ import annotations

import asyncio
import threading


class DeviceScanWorker(threading.Thread):
    def __init__(self, finished, timeout: float = 5.0):
        super().__init__(name="ProxiMicBleScan", daemon=True)
        self._finished = finished
        self._timeout = timeout
        self._cancelled = threading.Event()
        self._lock = threading.Lock()
        self._loop = None
        self._task = None

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def cancel(self) -> None:
        with self._lock:
            if self.cancelled:
                return
            self._cancelled.set()
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._task.cancel)

    async def _scan(self):
        from ring_python_sdk.ble import scan_all_devices

        with self._lock:
            self._loop = asyncio.get_running_loop()
            self._task = asyncio.current_task()
        try:
            if self.cancelled:
                return []
            return await scan_all_devices(self._timeout)
        finally:
            with self._lock:
                self._loop = self._task = None

    def run(self) -> None:
        rows, error = [], ""
        try:
            rows = [
                {"name": item.name or "未命名设备", "identifier": item.identifier,
                 "rssi": item.rssi if item.rssi is not None else "", "_device": item.device}
                for item in asyncio.run(self._scan())
            ]
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            error = str(exc)
        self._finished(self, rows, error)
