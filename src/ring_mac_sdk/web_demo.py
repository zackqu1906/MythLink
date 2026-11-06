"""Explicit opt-in synthetic UI events. Does not import or access Bluetooth."""
import asyncio
import math
import time
import uuid

from .types import DeviceInfo, EventMeta, Motion, ContactUpdate, ContactRange, Click, TouchpadStatus


class DemoTouchpad:
    def __init__(self, ring):
        self.ring = ring
        self.status = TouchpadStatus()
        self.task = None

    def publish(self, stage):
        self.status = TouchpadStatus(stage, "模拟数据 · 不代表真机识别", self.session, self.generation)
        self.ring.callbacks['on_touchpad_status'](self.status)

    async def start(self):
        self.session, self.generation, self.sequence = str(uuid.uuid4()), 0, 0
        self.publish('STARTING')
        await asyncio.sleep(.1)
        self.publish('RUNNING')
        self.task = asyncio.create_task(self.animate())

    def meta(self):
        self.sequence += 1
        return EventMeta(self.session, self.generation, self.sequence, time.monotonic_ns())

    async def animate(self):
        emit = self.ring.callbacks['on_touchpad']
        cycle = 0
        while True:
            mode = 'SINGLE' if cycle % 2 == 0 else 'TWO_FINGER'
            contact = ContactRange(1, time.monotonic_ns())
            emit(ContactUpdate(self.meta(), cycle, 'BEGIN', mode, contact))
            for frame in range(75):
                angle = frame / 75 * math.tau
                emit(Motion(self.meta(), frame, math.cos(angle)*4, math.sin(angle)*3, mode, cycle))
                await asyncio.sleep(.04)
            emit(ContactUpdate(self.meta(), cycle, 'END', mode, contact))
            emit(Click(self.meta(), cycle, 'LEFT', 1, True))
            await asyncio.sleep(.35)
            emit(Click(self.meta(), cycle, 'LEFT' if cycle % 2 == 0 else 'RIGHT', 1, False))
            await asyncio.sleep(.7)
            cycle += 1
            if cycle % 3 == 0:
                self.generation += 1
                self.publish('RUNNING')

    async def stop(self):
        if self.task:
            self.publish('STOPPING')
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        if hasattr(self, 'session'):
            self.publish('STOPPED')


class DemoRing:
    def __init__(self, device, **callbacks):
        self.callbacks = callbacks
        self.touchpad = DemoTouchpad(self)
        self.device_info = DeviceInfo('模拟固件 · 未连接硬件', 1, True)

    async def connect(self):
        self.callbacks['on_connection']('CONNECTING')
        await asyncio.sleep(.1)
        self.callbacks['on_connection']('CONNECTED')
        return self.device_info

    async def battery_level(self):
        return 80

    async def disconnect(self):
        await self.touchpad.stop()
        self.callbacks['on_connection']('DISCONNECTED')
