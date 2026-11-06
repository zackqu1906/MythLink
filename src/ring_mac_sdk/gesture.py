from . import protocol as p
from .errors import RingError
from .types import GestureMode, GestureResponse, SwipeDirection, WearingHand


class GestureClient:
    def __init__(self, transport):
        self._transport = transport

    async def _request(self, command):
        info = self._transport.device_info
        if not self._transport.ready or info is None:
            raise RingError("not_ready")
        if not info.swipe_available:
            raise RingError("unsupported", "Current INFO does not advertise Swipe")
        if info.hardware_revision == 1:
            if command[0] != 0x26 or command[1] not in (0, 1):
                raise RingError("unsupported", "Ringo has only legacy Swipe START/STOP; no low-power/hand/direction API")
            await self._transport.send(command)
            return GestureResponse(0x26, command[1], None, GestureMode(command[1]), acknowledged=False)
        if command[:1] == b"\x26" and command[1] != GestureMode.OFF:
            if self._transport.client is not None and self._transport.client.mtu_size < 68:
                raise RingError("not_ready", "Gesture notifications require BLE MTU >= 68")
        key = (command[0], 8 if command[0] == 0x26 else 0x80, command[1])
        response = await self._transport.request(command, key, p.gesture_reply)
        if response.status:
            raise RingError("device_error", f"Gesture rejected: {response.status}",
                            device_code=response.status, response=response)
        return response

    async def set_mode(self, mode: GestureMode):
        return await self._request(bytes((0x26, GestureMode(mode))))

    async def read_hand(self):
        return await self._request(b"\x38\x00")

    async def set_hand(self, hand: WearingHand):
        return await self._request(bytes((0x38, 1, WearingHand(hand))))

    async def read_swipe_direction(self):
        return await self._request(b"\x39\x00")

    async def set_swipe_direction(self, direction: SwipeDirection):
        return await self._request(bytes((0x39, 1, SwipeDirection(direction))))
