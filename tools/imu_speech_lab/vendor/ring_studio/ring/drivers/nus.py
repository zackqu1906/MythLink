"""Shared NUS lifecycle; product-specific drivers supply stream startup."""
import asyncio
import time
from bleak import BleakClient
from .nus_protocol import SERVICE, RX, TX, START, decode_imu


class NusRing:
    label = "NUS"

    def make_client(self):
        return BleakClient(self.device, disconnected_callback=self._lost)

    def __init__(self, device, name, sample, state, reconnected):
        self.device, self.address, self.name = device, str(device.address), name
        self.sample, self.state, self.reconnected = sample, state, reconnected
        self.client = None
        self.connected = self.is_reconnecting = False
        self.battery_level = -1
        self.firmware_version_text = ""
        self.auto_reconnect = True
        self._retry = self._battery = self._pending_time = None
        self._write_lock = asyncio.Lock()
        self._time_lock = asyncio.Lock()
        self.clear_time_calibration()
        self.reset_time_calib_data()
        self._gesture_lock = asyncio.Lock()
        self._gesture_status = None
        self.gesture_callback = None
        self.invalid_packets = 0
        self._time_unsafe = False
        self._firmware_info = None
        self._firmware_info_lock = asyncio.Lock()

    async def read_firmware_identity(self):
        """Read fresh hardware identity before allowing a firmware update."""
        async with self._firmware_info_lock:
            future = asyncio.get_running_loop().create_future()
            self._firmware_info = future
            try:
                await self.write(b"\x2a\x01")
                data = await asyncio.wait_for(future, 3)
                if (len(data) < 9 or data[2] != 1 or data[3] not in (1, 2)
                        or len(data) < 9 + 5 * data[8]):
                    raise RuntimeError("无法确认戒指硬件类型，不能烧录固件")
                return dict(family="ringo" if data[3] == 1 else "r1",
                            version=tuple(data[4:8]))
            finally:
                self._firmware_info = None
                if not future.done():
                    future.cancel()

    async def set_gestures(self, enabled):
        async with self._gesture_lock:
            await self.write(bytes((0x26, 0 if enabled else 1)))
            future = asyncio.get_running_loop().create_future()
            self._gesture_status = future
            try:
                await self.write(b"\x2c\x03")
                try:
                    status = await asyncio.wait_for(future, 3)
                except TimeoutError as exc:
                    raise RuntimeError("无法确认设备识别状态，请检查固件是否支持运行态查询") from exc
                if len(status) != 29 or status[2] != 1:
                    raise RuntimeError("设备识别状态应答无效")
                active = bool(int.from_bytes(status[4:6], "little") & (1 << 4))
                if active != enabled:
                    message = "识别未启动，请检查充电状态、资源占用或固件自动控制" if enabled else "识别仍在运行，固件可能由 HID 自动控制"
                    raise RuntimeError(message)
            finally:
                if not future.done():
                    future.cancel()
                self._gesture_status = None

    def set_auto_reconnect(self, enabled):
        self.auto_reconnect = enabled

    async def write(self, data):
        async with self._write_lock:
            client = self.client
            if client is None or not client.is_connected:
                raise ConnectionError(f"{self.label} 已断开")
            await client.write_gatt_char(RX, data, response=False)

    async def connect(self):
        self.auto_reconnect = True
        self.firmware_version_text = ""
        client = self.make_client()
        self.client = client
        try:
            await client.connect()
            service = client.services.get_service(SERVICE)
            if service is None or not service.get_characteristic(RX) or not service.get_characteristic(TX):
                raise RuntimeError(f"{self.label} NUS 服务不完整")
            await client.start_notify(TX, lambda _, data: self._notify(client, data))
            self.clear_time_calibration()
            if not client.is_connected:
                raise ConnectionError(f"{self.label} 在连接过程中断开")
            self._time_unsafe = False
            self.connected = True
            self.state()
        except BaseException:
            self.client = None
            await client.disconnect()
            raise

    decode_imu = staticmethod(decode_imu)

    async def open_imu(self):
        await self.write(START)
        if self._battery:
            self._battery.cancel()
        self._battery = asyncio.create_task(self._poll_battery())

    async def _poll_battery(self):
        try:
            while self.connected:
                await self.write(b"\x29\x01")
                await asyncio.sleep(30)
        except (ConnectionError, OSError):
            pass
        except Exception:
            # Battery polling is auxiliary; a failed read must not stop IMU.
            self.battery_level = -1
            self.state()

    def _notify(self, client, data):
        if client is not self.client or not self.connected or len(data) < 2:
            return
        if data[:2] == b"\x2a\x02" and self._firmware_info and not self._firmware_info.done():
            self._firmware_info.set_result(bytes(data))
        if data[:2] == b"\x2c\x04" and self._gesture_status and not self._gesture_status.done():
            self._gesture_status.set_result(bytes(data))
        if data[0] == 0x26 and self.gesture_callback:
            self.gesture_callback(bytes(data))
        elif data[0] == 0x21:
            try:
                frames = self.decode_imu(data)
            except (ValueError, IndexError):
                self.invalid_packets += 1
                return
            for timestamp, values in frames:
                self.sample(timestamp, values)
        elif data[:2] == b"\x29\x02" and len(data) >= 5:
            self.battery_level = min(100, int(data[4]))
            self.state()
        elif data[:2] == b"\x23\x02" and len(data) in (18, 19):
            pending = self._pending_time
            if pending and not pending.done():
                pending.set_result((time.perf_counter(), int.from_bytes(data[11:19], "little")/1000))

    def _lost(self, client):
        if client is not self.client:
            return
        self.connected = False
        self.firmware_version_text = ""
        if self._gesture_status and not self._gesture_status.done():
            self._gesture_status.set_exception(ConnectionError("设备在识别启停期间断开"))
        self.clear_time_calibration()
        if self._battery:
            self._battery.cancel()
        if self._pending_time and not self._pending_time.done():
            self._pending_time.set_exception(ConnectionError(f"同步期间 {self.label} 断开"))
        if self.auto_reconnect and (self._retry is None or self._retry.done()):
            self.is_reconnecting = True
            self._retry = asyncio.create_task(self._recover())
        self.state()

    async def _recover(self):
        try:
            while self.auto_reconnect:
                await asyncio.sleep(2)
                try:
                    await asyncio.wait_for(self.connect(), 15)
                    await self.reconnected(self.address)
                    return
                except Exception:
                    client, self.client = self.client, None
                    self.connected = False
                    if client:
                        await client.disconnect()
                    self.state()
        finally:
            self.is_reconnecting = False
            self.state()

    async def disconnect(self):
        self.auto_reconnect = False
        tasks = [t for t in (self._retry, self._battery) if t and t is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._retry = self._battery = None
        async with self._write_lock:
            client, self.client = self.client, None
            self.connected = self.is_reconnecting = False
            self.firmware_version_text = ""
            self.clear_time_calibration()
            if client:
                try:
                    if client.is_connected:
                        await client.write_gatt_char(RX, b"\x21\x01", response=False)
                        await client.stop_notify(TX)
                finally:
                    await client.disconnect()
        self.state()

    def reset_time_calib_data(self):
        self.start_calib_timestamps, self.end_calib_timestamps = [], []
        self.ring_timestamps, self.end_indices = [], []

    def clear_time_calibration(self):
        self._clock = None

    def set_time_calibration(self, intercept, slope):
        self._clock = (intercept, slope)

    def to_host_timestamp(self, timestamp):
        return self._clock[0]+self._clock[1]*timestamp if self._clock else 0.0

    async def calib_time(self, index):
        # TIME has no request ID: never overlap requests. Abort on timeout,
        # rather than misattribute a late reply to the next sample in this run.
        async with self._time_lock:
            if self._time_unsafe:
                raise RuntimeError(f"上次同步超时，请重连 {self.label} 后再同步")
            async with self._write_lock:
                if not self.connected:
                    raise ConnectionError(f"{self.label} 已断开")
                future = asyncio.get_running_loop().create_future()
                self._pending_time = future
                self.start_calib_timestamps.append(time.perf_counter())
                try:
                    await self.client.write_gatt_char(RX, b"\x23\x01", response=False)
                    end, uptime = await asyncio.wait_for(future, .5)
                    self.end_calib_timestamps.append(end)
                    self.ring_timestamps.append(uptime)
                    self.end_indices.append(index)
                except BaseException:
                    self._time_unsafe = True
                    raise
                finally:
                    if not future.done():
                        future.cancel()
                    self._pending_time = None
