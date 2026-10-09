"""Optional touchpad lifecycle on the existing RingSession connection."""
from __future__ import annotations

import asyncio
import math
from pathlib import Path
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from ring_python_sdk.touchpad import TouchpadEvent, TouchpadStats


class TouchpadMixin:
    async def touchpad_on(self, *, on_event: Callable[[TouchpadEvent], None],
                          on_stats: Callable[[TouchpadStats], None] | None = None,
                          on_stopped: Callable[[Exception | None], None] | None = None,
                          on_pointer_move: Callable[[TouchpadEvent], None] | None = None,
                          model_path: Path | None = None, duration_s: float | None = 90.):
        """Start original-AAR touchpad inference, without moving the OS pointer.

        Call on the session BLE event loop. Callbacks are synchronous on that
        loop; use a Qt signal or your own queue to reach a GUI. Callback errors
        stop this stream. None duration means explicitly unlimited streaming.
        Raw IMU and quaternion modes are mutually exclusive with touchpad.
        Optional on_pointer_move receives the original paced mouse movement;
        on_event keeps the current full trajectory and click/stroke decisions.
        """
        if not callable(on_event) or any(cb is not None and not callable(cb) for cb in (on_stats, on_stopped, on_pointer_move)):
            raise TypeError('Touchpad callbacks must be callable')
        if duration_s is not None and (not math.isfinite(duration_s) or duration_s <= 0):
            raise ValueError('duration_s must be positive and finite, or None')
        async with self._touchpad_lock:
            if self.imu_active or self._imu_starting or self._imu_stopping or self.quaternion_active or self.touchpad_active:
                raise RuntimeError('Stop the current IMU/quaternion/touchpad stream first')
            if self.client is None or not self.client.is_connected or not self.rx_uuid:
                raise RuntimeError('Ring is not connected')
            from ring_python_sdk.touchpad.core import START
            from ring_python_sdk.touchpad.stream import TouchpadStream
            client, rx_uuid = self.client, self.rx_uuid
            stream = TouchpadStream(on_event, on_stats=on_stats, model_path=model_path,
                                    duration_s=duration_s, on_end=self._touchpad_finished,
                                    on_pointer_move=on_pointer_move)
            self.touchpad, self.touchpad_active = stream, True
            self.touchpad_error = None
            self._touchpad_stopped_cb = on_stopped
            attempted = False
            try:
                await stream.prepare()
                if stream.closed or self.touchpad is not stream or self.client is not client or not client.is_connected:
                    raise ConnectionError('Ring disconnected during touchpad startup')
                # Publish queue before START: firmware may notify immediately.
                attempted = True
                await client.write_gatt_char(rx_uuid, START, response=False)
                if self.touchpad is not stream or not client.is_connected:
                    raise ConnectionError('Ring disconnected during touchpad START')
                stream.start()
            except BaseException as exc:
                self.touchpad_error = exc if isinstance(exc, Exception) else RuntimeError('Touchpad startup cancelled')
                try:
                    if self.touchpad is stream:
                        await self._stop_touchpad(send_stop=attempted)
                    else:
                        await stream.close()
                except Exception as cleanup_error:
                    self.emit_live(f'touchpad startup cleanup failed: {cleanup_error}')
                raise

    async def touchpad_off(self):
        """Stop callbacks immediately, stop token production and release MNN."""
        # Gate immediately even if START is still awaiting BLE completion.
        if self.touchpad is not None:self.touchpad.stop_now()
        async with self._touchpad_lock:
            await self._stop_touchpad(send_stop=True)

    def _notify_touchpad_stopped(self, callback, error):
        if callback is not None:
            try:callback(error)
            except Exception as exc:self.emit_live(f'touchpad stopped callback failed: {exc}')

    async def _stop_touchpad(self, *, send_stop):
        stream, self.touchpad = self.touchpad, None
        callback, self._touchpad_stopped_cb = self._touchpad_stopped_cb, None
        if stream is None:
            self.touchpad_active = False
            return
        # Reserve IMU mode until STOP completes, so another mode cannot START
        # just before this STOP and have its new stream accidentally stopped.
        stream.stop_now()
        try:
            if send_stop and self.client is not None and self.client.is_connected:
                await asyncio.wait_for(self.client.write_gatt_char(self.rx_uuid, b'\x21\x01', response=False), 2.)
        except Exception as exc:
            self.touchpad_error = exc
            raise
        finally:
            try:await stream.close()
            finally:self.touchpad_active = False
            self._notify_touchpad_stopped(callback, self.touchpad_error)

    def _touchpad_finished(self, stream):
        if self.touchpad is not stream:return
        async def finish():
            async with self._touchpad_lock:
                if self.touchpad is not stream:return
                self.touchpad_error = stream.error
                if stream.error:self.emit_live(f'touchpad stopped: {stream.error}')
                try:await self._stop_touchpad(send_stop=True)
                except Exception as exc:
                    self.touchpad_error = exc
                    self.emit_live(f'touchpad STOP failed: {exc}')
        self._track_touchpad_cleanup(finish())

    def _track_touchpad_cleanup(self, awaitable):
        task = asyncio.create_task(awaitable)
        self._touchpad_cleanup_tasks.add(task)
        task.add_done_callback(self._touchpad_cleanup_tasks.discard)

    def _drop_touchpad_local(self):
        stream, self.touchpad = self.touchpad, None
        self.touchpad_active = False
        callback, self._touchpad_stopped_cb = self._touchpad_stopped_cb, None
        if stream is not None:
            self.touchpad_error = ConnectionError('Ring link disconnected')
            stream.stop_now()
            self._track_touchpad_cleanup(stream.close())
            self._notify_touchpad_stopped(callback, self.touchpad_error)

    async def _wait_touchpad_cleanup(self):
        tasks = list(self._touchpad_cleanup_tasks)
        if tasks:await asyncio.gather(*tasks, return_exceptions=True)
