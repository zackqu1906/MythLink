"""A cancellable BLE scan whose platform cleanup stays off the UI thread."""
from __future__ import annotations

import asyncio
from concurrent.futures import CancelledError as WorkerCancelledError
import threading

from proximic_ring.ble_runtime import run_ble
from proximic_ring.ring_devices import ring_family
from proximic_ring.windows_ble import paired_r1_devices


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
                return [], ""
            paired = await paired_r1_devices()
            error = ""
            try:
                nearby = await scan_all_devices(self._timeout)
            except Exception as exc:
                if not paired:
                    raise
                nearby, error = [], str(exc) or type(exc).__name__
            known = {device.address.casefold(): device for device in paired}
            rows = []
            for item in nearby:
                cached = known.pop(item.identifier.casefold(), None)
                name = item.name or (cached.name if cached else "")
                rows.append({"name": name or "未命名设备", "identifier": item.identifier,
                             "rssi": item.rssi if item.rssi is not None else "", "_device": item.device,
                             "family": ring_family(name, getattr(item, "pnp_id", None))})
            paired_rows = [{"name": device.name, "identifier": device.address, "rssi": "",
                            "family": "R1", "_device": device, "pairedOnly": True}
                           for device in known.values()]
            return paired_rows + rows, error
        finally:
            with self._lock:
                self._loop = self._task = None

    def run(self) -> None:
        rows, error = [], ""
        try:
            rows, error = run_ble(self._scan())
        except (asyncio.CancelledError, WorkerCancelledError):
            pass
        except Exception as exc:
            error = str(exc)
        self._finished(self, rows, error)
