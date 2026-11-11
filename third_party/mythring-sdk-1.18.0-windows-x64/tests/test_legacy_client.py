"""Transport-level compatibility with the captured RingoDF0A identity."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from mythring import GestureException, GestureMode, Ring, UnsupportedFeatureError
from mythring.hid import HidError, HidGesture
from mythring.ota import RingOtaUpdater
from mythring.protocol import CLOSE_IMU, GET_INFO, OPEN_IMU_TOKEN
from test_client import FakeClient, INFO

LEGACY_INFO = bytes.fromhex(
    "2a02010101027a000801010101030200000000030101010004010101030501010103"
    "0601010200070101010008010102000e312e322e3132322e7075626c6963"
)


class LegacyClient(FakeClient):
    identity = LEGACY_INFO

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.info_packet = self.identity


class LegacyClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        LegacyClient.identity = LEGACY_INFO
        LegacyClient.invalid_info = LegacyClient.missing = False
        self.enterContext(patch("bleak.BleakClient", LegacyClient))
        self.enterContext(patch("mythring.client.retrieve_device", AsyncMock(return_value=None)))
        self.health_refresh = self.enterContext(patch("mythring.client.HealthClient.connected", AsyncMock()))
        self.ring = Ring("df0a", write_timeout=0.1)
        await self.ring.connect()
        self.client = LegacyClient.instances[-1]

    async def asyncTearDown(self):
        await self.ring.disconnect()

    async def test_identity_capabilities_and_battery(self):
        self.assertEqual(self.ring.protocol_profile, "legacy_v1")
        self.assertEqual(self.ring.device_info.firmware_version, "1.2.122.public")
        self.assertEqual(self.ring.device_info.firmware_numeric_version, (1, 2, 122, 0))
        self.assertEqual(len(self.ring.device_info.components), 8)
        self.assertTrue(self.ring.capabilities["imu"])
        self.assertTrue(self.ring.capabilities["gesture"])
        for feature in ("health", "touchpad", "touchpad_raw", "hid", "ota", "owner_unbind"):
            self.assertFalse(self.ring.capabilities[feature], feature)
        await asyncio.sleep(0)
        self.health_refresh.assert_not_awaited()
        self.assertEqual(self.client.writes, [GET_INFO])
        self.assertEqual(await self.ring.get_battery_level(), 84)

    async def test_legacy_gesture_and_imu_without_new_health_negotiation(self):
        for mode in (GestureMode.NORMAL, GestureMode.OFF):
            result = await self.ring.gesture.set_mode(mode)
            self.assertFalse(result.acknowledged)
            self.assertIsNone(result.status)
        await self.ring.start_imu_tokens()
        await self.ring.stop_imu_tokens()
        self.assertEqual(self.client.writes, [GET_INFO, b"\x26\x00", b"\x26\x01", OPEN_IMU_TOKEN, CLOSE_IMU])

    async def test_new_commands_rejected_before_transport_write(self):
        for command in (b"\x3a\x00", b"\x3c\x00", b"\x37\x00\xa5", b"\x38\x00",
                        b"\x39\x00", b"\x30\x03", b"\x30\x04", b"\x26\x07"):
            with self.subTest(command=command), self.assertRaises(UnsupportedFeatureError):
                await self.ring.send_command(command)
        self.assertEqual(self.client.writes, [GET_INFO])

    async def test_legacy_short_gesture_packet_does_not_require_current_mtu(self):
        self.client.mtu_size = 23
        result = await self.ring.gesture.set_mode(GestureMode.NORMAL)
        self.assertFalse(result.acknowledged)
        self.assertEqual(self.client.writes, [GET_INFO, b"\x26\x00"])

    async def test_public_unsupported_features(self):
        model_start = self.enterContext(patch.object(self.ring._recognizer, "start", AsyncMock()))
        for operation in (self.ring.start_touchpad, self.ring.start_touchpad_raw, self.ring.stop_touchpad,
                          self.ring.health.get_capabilities, self.ring.owner_unbind):
            with self.subTest(operation=operation.__name__), self.assertRaises(UnsupportedFeatureError):
                await operation()
        with self.assertRaises(GestureException) as gesture:
            await self.ring.gesture.set_mode(GestureMode.LOW_POWER)
        self.assertEqual(gesture.exception.reason, "unsupported")
        with self.assertRaises(HidError) as hid:
                await self.ring.hid.read(HidGesture.LEFT)
        self.assertEqual(hid.exception.reason, "unsupported")
        with self.assertRaises(UnsupportedFeatureError):
            await RingOtaUpdater(self.ring).prepare_target()
        model_start.assert_not_awaited()
        self.assertEqual(self.client.writes, [GET_INFO])

    async def test_legacy_reconnect_restores_transport_not_health_protocol(self):
        for _ in range(2):
            with self.assertRaises(UnsupportedFeatureError):
                await self.ring.health.get_capabilities()
            await self.ring.disconnect()
            self.assertIsNone(self.ring.protocol_profile)
            self.assertEqual(self.ring.capabilities, {})
            await self.ring.connect()
        self.health_refresh.assert_not_awaited()

    async def test_reconnect_current_profile_restores_capabilities_and_ack(self):
        await self.ring.disconnect()
        LegacyClient.identity = INFO
        await self.ring.connect()
        await asyncio.sleep(0)
        self.assertEqual(self.ring.protocol_profile, "r1_v2")
        self.assertTrue(self.ring.capabilities["health"])
        self.assertTrue(self.ring.capabilities["touchpad"])
        self.health_refresh.assert_awaited_once()
        self.assertTrue(self.ring.gesture._acknowledged)

    async def test_hardware_two_with_old_firmware_is_still_legacy(self):
        await self.ring.disconnect()
        identity = bytearray(LEGACY_INFO)
        identity[3] = 2
        LegacyClient.identity = bytes(identity)
        await self.ring.connect()
        self.assertEqual(self.ring.protocol_profile, "legacy_v1")
        self.assertFalse(self.ring.capabilities["ota"])
        self.assertFalse(self.ring.capabilities["touchpad"])
        with self.assertRaises(UnsupportedFeatureError):
            await RingOtaUpdater(self.ring).prepare_target()
        self.assertEqual(LegacyClient.instances[-1].writes, [GET_INFO])
        self.health_refresh.assert_not_awaited()

    async def test_unknown_firmware_has_only_basic_read_capabilities(self):
        await self.ring.disconnect()
        identity = bytearray(LEGACY_INFO)
        identity[3:5] = bytes((2, 3))
        LegacyClient.identity = bytes(identity)
        await self.ring.connect()
        self.assertEqual(self.ring.protocol_profile, "unknown")
        self.assertEqual({key for key, value in self.ring.capabilities.items() if value}, {"battery", "rssi"})
        self.health_refresh.assert_not_awaited()
        with self.assertRaises(GestureException) as gesture:
            await self.ring.gesture.set_mode(GestureMode.NORMAL)
        self.assertEqual(gesture.exception.reason, "unsupported")


if __name__ == "__main__":
    unittest.main()
