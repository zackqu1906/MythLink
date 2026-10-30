"""Legacy Ringo device plugin."""
import asyncio
from bleak import BleakClient
from .nus import NusRing
from .ringo_protocol import IMU_START, decode_packet, firmware_version, imu_start_error


class Ringo(NusRing):
    label = "Ringo"

    info_timeout = 3.0
    imu_timeout = 3.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._info_ready = asyncio.Event()
        self._imu_ready = None
        self._runtime_status = {}
        self._battery_status = {}

    async def open_imu(self):
        self._info_ready.clear()
        self.firmware_version_text = ""
        await self.write(b"\x2a\x01")
        try:
            await asyncio.wait_for(self._info_ready.wait(), self.info_timeout)
        except TimeoutError:
            # Older firmware may lack INFO; it can still provide an IMU stream.
            self.state()
        self._runtime_status = {}
        self._battery_status = {}
        ready = asyncio.get_running_loop().create_future()
        self._imu_ready = ready
        try:
            await self.write(b"\x2c\x00\x00")  # NORMAL, unmute sensor notifications.
            await self.write(IMU_START)
            await self.write(b"\x2c\x03")
            await self.write(b"\x29\x01")
            try:
                await asyncio.wait_for(ready, self.imu_timeout)
            except TimeoutError as exc:
                raise RuntimeError(imu_start_error(self._runtime_status, self._battery_status)) from exc
            if self._battery:
                self._battery.cancel()
            self._battery = asyncio.create_task(self._poll_battery())
        finally:
            self._imu_ready = None
            if not ready.done():
                ready.cancel()

    def decode_imu(self, data):
        frames = super().decode_imu(data)
        if frames and self._imu_ready and not self._imu_ready.done():
            self._imu_ready.set_result(True)
        return frames

    def _lost(self, client):
        if client is self.client and self._imu_ready and not self._imu_ready.done():
            self._imu_ready.set_exception(ConnectionError('等待 IMU 数据期间设备断开'))
        super()._lost(client)

    packet_callback = None
    command_callback = None

    async def write(self, data):
        await super().write(data)
        if self.command_callback:
            self.command_callback(bytes(data))

    def _notify(self, client, data):
        if client is not self.client or not self.connected:
            return
        try:
            if data[:2] == b"\x2a\x02":
                self.firmware_version_text = firmware_version(data)
                self._info_ready.set()
                self.state()
            elif data[:2] in (b"\x2c\x04", b"\x29\x02"):
                event = decode_packet(data)
                if event['kind'] == 'runtime':
                    self._runtime_status = event
                else:
                    self._battery_status = event
        except (ValueError, IndexError):
            self.invalid_packets += 1
        if self.packet_callback:
            self.packet_callback(bytes(data))
        super()._notify(client, data)

    async def disconnect(self):
        try:
            if self.client and self.client.is_connected:
                await self.write(b"\x20\x01")
        finally:
            await super().disconnect()

    def make_client(self):
        return BleakClient(self.device, disconnected_callback=self._lost)


def create(device, name, sample, state, reconnected):
    return Ringo(device, name, sample, state, reconnected)
