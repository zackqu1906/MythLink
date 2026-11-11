import asyncio
import logging

from . import protocol as p
from .errors import RingError
from .gesture import GestureClient
from .touchpad import Touchpad
from .transport import Transport

log = logging.getLogger(__name__)


class Ring:
    """Use APIs on one asyncio loop. Synchronous touchpad callbacks run off-loop;
    async touchpad callbacks run on the owner loop and must yield promptly.
    Gesture/connection callbacks remain synchronous on the owner loop.

    disconnect() releases this app's data channel; it never unbinds the owner.
    """
    def __init__(self, device, *, on_gesture=None, on_touchpad=None,
                 on_touchpad_status=None, on_connection=None, on_touchpad_diagnostics=None, _client_factory=None, _model_factory=None):
        self.device_info = None
        self.link_state = "DISCONNECTED"
        self._callbacks = dict(gesture=on_gesture, touchpad=on_touchpad,
                               touchpad_status=on_touchpad_status, connection=on_connection)
        self._lifecycle = asyncio.Lock()
        self._transport = Transport(device, self._receive, self._disconnected, _client_factory)
        self.gesture = GestureClient(self._transport)
        self.touchpad = Touchpad(self._transport, on_touchpad,
                                 on_touchpad_status, _model_factory, on_touchpad_diagnostics)

    def _emit(self, name, event):
        callback = self._callbacks[name]
        if callback:
            try:
                callback(event)
            except Exception:
                log.exception("Ring %s callback failed", name)

    def _state(self, state):
        self.link_state = state
        self._emit("connection", state)

    async def connect(self):
        async with self._lifecycle:
            if self._transport.client is not None:
                raise RingError("busy", "Disconnect the existing session before connecting again")
            self._state("CONNECTING")
            try:
                self.device_info = await self._transport.connect()
                self._state("CONNECTED")
                return self.device_info
            except BaseException:
                self._disconnected()
                raise

    async def disconnect(self):
        async with self._lifecycle:
            self._transport.closing = True  # block new API calls; allow final STOP
            try:
                await self.touchpad.stop()
            finally:
                await self._transport.close()
                self._disconnected()

    def _disconnected(self):
        self.device_info = None
        self.touchpad.invalidate("BLE connection ended")
        if self.link_state != "DISCONNECTED":
            self._state("DISCONNECTED")

    def _receive(self, data):
        self.touchpad.receive(data)
        public_ringo = (self.device_info is not None and self.device_info.hardware_revision == 1
                        and data[:2] == b"\x26\x07")
        if self._transport.ready and (data[:2] == b"\x26\x06" or public_ringo):
            try:
                event = p.ringo_gesture(data) if public_ringo else p.gesture(data)
            except Exception:
                log.warning("Ignored malformed gesture packet")
            else:
                self._emit("gesture", event)

    async def battery_level(self):
        return await self._transport.request(p.BATTERY, (0x29, 2), p.battery)

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *_):
        await self.disconnect()
