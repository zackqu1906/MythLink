"""Ringo dev9 uses the existing SDK API without enabling R1-only commands."""
import asyncio
import struct
import time
import unittest
from unittest.mock import AsyncMock, patch

from mythring import GestureException, GestureMode, Ring, ProtocolError, UnsupportedFeatureError
from mythring.protocol import GET_BATTERY, GET_INFO, parse_packet
from mythring.touchpad import START, STOP, DIAGNOSTICS, TouchpadStage
from test_client import FakeClient, INFO
from test_legacy_client import LEGACY_INFO
from test_touchpad import status_packet, token_packet
from test_touchpad_inference import ConstantModel


def ringo_info(version=b"2.1.6-dev.touchpad.9.debug", hardware=1, microphone=False):
    components = bytes((8, 1, 1, 2, 0)) + (bytes((3, 1, 1, 1, 0)) if microphone else b"")
    return bytes((0x2A, 2, 1, hardware, 2, 1, 6, 0, 2 if microphone else 1)) + components + bytes((len(version),)) + version


class RingoClient(FakeClient):
    identity = ringo_info()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.info_packet = self.identity
        self.initial_stop = asyncio.Event()
        self.release_stop = None

    async def write_gatt_char(self, characteristic, packet, response):
        await super().write_gatt_char(characteristic, packet, response)
        if packet == STOP:
            self.initial_stop.set()
            if self.release_stop:
                await self.release_stop.wait()
            self.send(status_packet(state=0))
        elif packet == START:
            self.send(status_packet())
            self.send(token_packet())
        elif packet == DIAGNOSTICS:
            diagnostic = bytearray(64)
            diagnostic[:3] = bytes((0x3C, 0x12, 2))
            struct.pack_into("<I", diagnostic, 60, 3)
            self.send(diagnostic)


class RingoClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        RingoClient.identity = ringo_info()
        RingoClient.invalid_info = RingoClient.missing = False
        self.enterContext(patch("bleak.BleakClient", RingoClient))
        self.enterContext(patch("mythring.client.retrieve_device", AsyncMock(return_value=None)))
        self.health = self.enterContext(patch("mythring.client.HealthClient.connected", AsyncMock()))
        self.ring = Ring("ringo-dev9")
        await self.ring.connect()
        self.client = RingoClient.instances[-1]

    async def asyncTearDown(self):
        await self.ring.disconnect()

    async def test_profile_and_public_capabilities(self):
        self.assertEqual(self.ring.protocol_profile, "ringo_touchpad_v2")
        self.assertEqual({k for k, v in self.ring.capabilities.items() if v},
                         {"battery", "rssi", "gesture", "touchpad", "touchpad_raw"})
        await asyncio.sleep(0)
        self.health.assert_not_awaited()
        self.assertEqual(await self.ring.get_battery_level(), 84)
        result = await self.ring.gesture.set_mode(GestureMode.NORMAL)
        self.assertFalse(result.acknowledged)
        for operation in (self.ring.start_imu_tokens, self.ring.health.get_capabilities,
                          self.ring.owner_unbind):
            with self.assertRaises(UnsupportedFeatureError):
                await operation()
        with self.assertRaises(GestureException):
            await self.ring.gesture.set_mode(GestureMode.LOW_POWER)

    async def test_touchpad_raw_through_existing_api(self):
        tokens = []
        self.ring.on("touchpad_tokens", tokens.append)
        await self.ring.start_touchpad_raw()
        self.assertEqual(self.ring.touchpad.stage, TouchpadStage.RUNNING)
        self.assertEqual(tokens[0][0].sequence, 0)
        self.assertEqual(self.client.writes, [GET_INFO, GET_INFO, STOP, START])
        await self.ring.stop_touchpad()
        self.assertEqual(self.ring.touchpad.stage, TouchpadStage.STOPPED)

    async def test_dev15_negotiates_raw2_without_enabling_dev9_audio_or_ota(self):
        await self.ring.disconnect()
        RingoClient.identity = ringo_info(b"2.1.6-dev.touchpad.15.debug", microphone=True)
        await self.ring.connect()
        self.assertEqual(self.ring.protocol_profile, "ringo_touchpad_v2")
        self.assertEqual(self.ring.feature_support("touchpad").reason_code, "firmware_model_match")
        self.assertEqual(self.ring.feature_support("audio").state, "unknown")
        self.assertFalse(self.ring.capabilities["audio"])
        self.assertFalse(self.ring.capabilities["ota"])
        await self.ring.start_touchpad_raw()
        self.assertEqual(self.ring.touchpad.stage, TouchpadStage.RUNNING)
        await self.ring.stop_touchpad()
        self.assertEqual(RingoClient.instances[-1].writes, [GET_INFO, GET_INFO, STOP, START, STOP])

    async def test_stream_failure_publishes_decoded_diagnostics(self):
        errors, diagnostics = [], []
        self.ring.on("error", errors.append)
        self.ring.on("touchpad_diagnostics", diagnostics.append)
        await self.ring.start_touchpad_raw()
        self.client.send(token_packet(2, uptime=120, reset=False))
        await self.ring.touchpad._cleanup
        self.assertEqual(len(errors), 1)
        self.assertEqual(len(diagnostics), 1)
        snapshot = diagnostics[0].to_dict()
        self.assertEqual(snapshot["error_type"], "TouchpadSequenceError")
        self.assertEqual(snapshot["diagnostics"]["fault_name"], "feature_tx_age_exceeded")
        self.assertEqual(self.client.writes[-2:], [STOP, DIAGNOSTICS])

    async def test_audio_waits_for_failed_touchpad_cleanup_stop_confirmation(self):
        await self.ring.disconnect()
        RingoClient.identity = ringo_info(microphone=True)
        await self.ring.connect()
        self.client = RingoClient.instances[-1]
        await self.ring.start_touchpad_raw()
        self.client.release_stop = asyncio.Event()
        self.client.send(token_packet(2, uptime=120, reset=False))
        self.assertEqual(self.ring.touchpad.stage, TouchpadStage.FAILED)
        with self.assertRaisesRegex(ProtocolError, "Stop IMU and touchpad"):
            await self.ring.start_audio()
        self.assertNotIn(b"\x20\x00\x01\x80\x80", self.client.writes)
        self.client.release_stop.set()
        await self.ring.touchpad._cleanup
        await self.ring._prepare_audio()

    async def test_processing_warning_keeps_ring_stream_and_events_running(self):
        warnings, errors, events = [], [], []
        warned, received = asyncio.Event(), asyncio.Event()
        self.ring._recognizer.model_factory = ConstantModel

        def on_warning(warning):
            warnings.append(warning)
            warned.set()

        def on_event(event):
            events.append(event)
            received.set()

        self.ring.on("warning", on_warning)
        self.ring.on("error", errors.append)
        self.ring.on("touchpad_event", on_event)
        await self.ring.start_touchpad()
        await asyncio.wait_for(received.wait(), 1)
        received.clear()
        count = len(events)
        engine = self.ring._recognizer._engine
        engine.clock = lambda: time.monotonic_ns() + 250_000_000
        self.client.send(token_packet(1, uptime=110, reset=False))
        await asyncio.wait_for(warned.wait(), 1)
        self.assertEqual(self.ring.touchpad.stage, TouchpadStage.RUNNING)
        self.assertTrue(self.ring._recognizer.running)
        self.assertEqual(errors, [])
        self.assertEqual(len(events), count)
        engine.clock = time.monotonic_ns
        self.client.send(token_packet(2, uptime=120, reset=False))
        await asyncio.wait_for(received.wait(), 1)
        self.assertEqual(events[-1].meta.interaction_generation, 2)
        self.assertEqual(len(warnings), 1)

    async def test_battery_waits_for_complete_ringo_start(self):
        self.client.release_stop = asyncio.Event()
        starting = asyncio.create_task(self.ring.start_touchpad_raw())
        await self.client.initial_stop.wait()
        battery = asyncio.create_task(self.ring.get_battery_level())
        await asyncio.sleep(0)
        self.assertNotIn(GET_BATTERY, self.client.writes)
        with self.assertRaises(GestureException):
            await self.ring.gesture.set_mode(GestureMode.NORMAL)
        self.client.release_stop.set()
        await starting
        self.assertEqual(await battery, 84)
        self.assertLess(self.client.writes.index(START), self.client.writes.index(GET_BATTERY))

    async def test_reconnect_to_old_profiles_restores_old_features(self):
        for identity, profile, health in ((LEGACY_INFO, "legacy_v1", False),
                                          (INFO, "r1_v2", True)):
            await self.ring.disconnect()
            RingoClient.identity = identity
            await self.ring.connect()
            self.assertEqual(self.ring.protocol_profile, profile)
            self.assertEqual(self.ring.capabilities["health"], health)

    async def test_unverified_ringo_versions_do_not_enable_touchpad(self):
        for version in (b"2.1.6-dev.touchpad.8.debug", b"2.1.6-dev.touchpad.9",
                        b"2.1.6-dev.hz.6.release"):
            await self.ring.disconnect()
            RingoClient.identity = ringo_info(version)
            await self.ring.connect()
            self.assertEqual(self.ring.protocol_profile, "legacy_v1")
            with self.assertRaises(UnsupportedFeatureError):
                await self.ring.start_touchpad_raw()

    async def test_public_gesture_reaches_existing_callback(self):
        events = []
        self.ring.on("gesture", events.append)
        self.client.send(b"\x26\x07" + struct.pack("<HBI f", 12, 7, 1234, .75))
        self.assertEqual((events[0].sequence, events[0].class_id,
                          events[0].center_uptime_ms, events[0].confidence), (12, 7, 1234, .75))


class RingoGestureTests(unittest.TestCase):
    def test_invalid_public_gesture_is_ignored(self):
        valid = b"\x26\x07" + struct.pack("<HBI f", 12, 7, 1234, .75)
        for packet in (valid[:-1], valid + b"\0", valid[:4] + b"\0" + valid[5:]):
            self.assertEqual(parse_packet(packet), [])
        for confidence in (float("nan"), float("inf"), -.1, 1.1):
            self.assertEqual(parse_packet(valid[:9] + struct.pack("<f", confidence)), [])


if __name__ == "__main__":
    unittest.main()
