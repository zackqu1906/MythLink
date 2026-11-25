"""Adapt the R1-PC SDK to the app's shared gesture and pointer session."""

import asyncio
import time

from ring_python_sdk_r1 import (
    Click, ConnectionState, ContactUpdate, GestureMode, Motion, Ring, RingDevice, TouchpadStage,
)
from ring_python_sdk.touchpad.events import Raw2PointerClick, Raw2PointerContact, Raw2PointerMove, Raw2Reset


class R1Session:
    mic_active = False
    battery_mv = None  # R1-PC publishes percentages without millivolts.

    def __init__(self, *, on_pairing=None):
        self.battery_pct = self.charge_status = None
        self.swipe_active = False
        self._touchpad_stopped = None
        self._touchpad_event = None
        self._touchpad_timeout = None

        def state_changed(state):
            if state == ConnectionState.PAIRING and on_pairing is not None:
                on_pairing()

        # Use the SDK's per-user storage so a binding created by R1-PC can be reused.
        self.ring = Ring(on_state=state_changed, on_battery=self._battery_updated,
                         on_touchpad_status=self._touchpad_status)

    @property
    def client(self):
        return self

    @property
    def is_connected(self):
        return self.ring.connected

    @property
    def target_address(self):
        return self.ring.address or ""

    @property
    def device_info(self):
        return self.ring.info

    async def connect(self, target=None):
        await self.ring.connect(target, auto_reconnect=False)
        return True

    connect_target = connect

    async def connect_device(self, device):
        return await self.connect(device if isinstance(device, RingDevice) else device.address)

    async def disconnect(self):
        await self.ring.close()

    def _battery_updated(self, status):
        self.battery_pct, self.charge_status = status.percent, status.charging_state

    async def query_battery(self):
        await self.ring.read_battery()

    async def swipe_on(self, *, on_trigger, reset_device=False, **_):
        if reset_device:
            await self.ring.set_gesture_mode(GestureMode.OFF)
        self.ring.on_gesture = on_trigger
        await self.ring.set_gesture_mode(GestureMode.NORMAL)
        self.swipe_active = True

    async def swipe_off(self):
        self.ring.on_gesture = None
        self.swipe_active = False
        if self.ring.connected:
            await self.ring.set_gesture_mode(GestureMode.OFF)

    @property
    def touchpad_active(self):
        return self._touchpad_stopped is not None

    def _touchpad_status(self, status):
        if status.stage in {TouchpadStage.FAILED, TouchpadStage.STOPPED}:
            self._finish_touchpad(status.error)
        elif (status.stage == TouchpadStage.STARTING and status.session_id is not None
              and self._touchpad_event is not None):
            self._touchpad_event(Raw2Reset('reset', 0, time.monotonic(), session_id=str(status.session_id),
                                          generation=status.generation))

    def _finish_touchpad(self, error):
        if self._touchpad_timeout is not None:
            self._touchpad_timeout.cancel()
            self._touchpad_timeout = None
        callback, self._touchpad_stopped = self._touchpad_stopped, None
        self._touchpad_event = None
        self.ring.on_touchpad = None
        if callback is not None:
            callback(error)

    def _pointer_event(self, event, callback):
        status, meta = self.ring.touchpad.status, event.meta
        if (status.stage != TouchpadStage.RUNNING or status.session_id != meta.session_id
                or status.generation != meta.generation):
            return
        identity = dict(session_id=str(meta.session_id), generation=meta.generation, sequence=meta.sequence)
        now = time.monotonic()
        clock_offset = now - time.perf_counter_ns() / 1e9
        timestamp = meta.source_time_ns / 1e9 + clock_offset
        if isinstance(event, Motion):
            callback(Raw2PointerMove(event.dx, event.dy, 0., event.frame_id + 1, timestamp,
                                     mode=event.mode, episode_id=event.episode_id, **identity))
        elif isinstance(event, ContactUpdate):
            sample = event.range
            callback(Raw2PointerContact(event.phase, event.mode, event.episode_id, sample.start_frame + 1,
                                        timestamp, sample.start_time_ns / 1e9 + clock_offset,
                                        None if sample.end_time_ns is None else sample.end_time_ns / 1e9 + clock_offset,
                                        start_step=sample.start_frame + 1,
                                        end_step=None if sample.end_frame_exclusive is None else sample.end_frame_exclusive + 1,
                                        **identity))
        elif isinstance(event, Click):
            callback(Raw2PointerClick(meta.sequence, now, button=event.button.lower(),
                                      count=event.count, click_id=event.click_id, source_timestamp=timestamp,
                                      is_preview=event.is_preview, **identity))

    async def touchpad_on(self, *, on_event, on_pointer_move=None, on_stopped, duration_s=None, **_):
        if self.touchpad_active:
            raise RuntimeError("触摸板正在运行，请先停止")
        callback = on_pointer_move or on_event
        self._touchpad_stopped = on_stopped
        self._touchpad_event = on_event
        self.ring.on_touchpad = lambda event: self._pointer_event(event, callback)
        try:
            await self.ring.touchpad.start()
        except BaseException:
            self._finish_touchpad(None)
            raise
        if duration_s is not None:
            self._touchpad_timeout = asyncio.get_running_loop().call_later(
                duration_s, lambda: asyncio.create_task(self.touchpad_off()))

    async def touchpad_off(self):
        try:
            await self.ring.touchpad.stop()
        finally:
            self._finish_touchpad(None)
