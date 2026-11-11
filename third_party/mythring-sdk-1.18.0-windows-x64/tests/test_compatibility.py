"""Identity provenance, conservative support explanations and public gates."""

import json
import struct
import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, patch

from mythring import ProtocolError, Ring, UnsupportedFeatureError
from mythring.models import DeviceInfo
from mythring.protocol import GET_INFO, OPEN_IMU_TOKEN, parse_device_info
from mythring.touchpad import TouchpadStage
from test_client import FakeClient


def identity(version="2.1.6-dev.touchpad.9.debug", *, hardware=1,
             numeric=(2, 1, 6, 0), microphone=True, swipe=True):
    components = bytes((1, 1, 1, 1, 0, 3, int(microphone), 1, 1, 0,
                        8, int(swipe), 1, 2, 0))
    packet = bytes((0x2A, 2, 1, hardware, *numeric, 3)) + components
    if version is not None:
        encoded = version.encode("ascii")
        packet += bytes((len(encoded),)) + encoded
    return packet


def ring_for(packet):
    ring = Ring("offline-identity")
    ring.device_info = parse_device_info(packet)
    return ring


class CompatibilityTests(unittest.TestCase):
    def test_identity_separates_product_from_unknown_board_and_keeps_old_values(self):
        info = parse_device_info(identity())
        self.assertEqual((info.product_id, info.hardware_revision, info.legacy_hardware_code),
                         ("ringo", 1, 1))
        self.assertIsNone(info.board_revision)
        self.assertEqual(info.firmware_text_version, "2.1.6-dev.touchpad.9.debug")
        self.assertEqual(info.firmware_version_source, "info_text")
        r1 = parse_device_info(identity(hardware=2))
        self.assertEqual((r1.product_id, r1.legacy_hardware_code, r1.board_revision), ("r1", 2, None))
        self.assertIsNone(parse_device_info(identity(hardware=3)))

    def test_numeric_fallback_is_not_reported_as_original_text(self):
        info = parse_device_info(identity(None, hardware=2))
        self.assertEqual(info.firmware_version, "2.1.6.0")
        self.assertEqual(info.firmware_numeric_version, (2, 1, 6, 0))
        self.assertIsNone(info.firmware_text_version)
        self.assertEqual(info.firmware_version_source, "info_numeric")

    def test_old_constructor_and_equality_do_not_invent_observed_metadata(self):
        old = DeviceInfo("1.2.133.public", 1, True)
        self.assertIsNone(old.product_id)
        self.assertIsNone(old.board_revision)
        self.assertIsNone(old.firmware_text_version)
        self.assertEqual(old, DeviceInfo("1.2.133.public", 1, True, product_id="ringo"))
        self.assertEqual(hash(old), hash(DeviceInfo("1.2.133.public", 1, True, product_id="ringo")))

    def test_exact_rule_explains_support_without_claiming_readiness(self):
        ring = ring_for(identity())
        support = ring.feature_support("touchpad")
        self.assertEqual((support.state, support.reason_code, support.rule_id, support.source),
                         ("supported", "documented_protocol", "ringo.touchpad.dev9", "exact_rule"))
        self.assertEqual(ring.touchpad.stage, TouchpadStage.STOPPED)
        self.assertEqual(ring.feature_support("health").reason_code, "sdk_not_implemented")
        audio = ring.feature_support("audio")
        self.assertEqual((audio.state, audio.reason_code), ("supported", "device_protocol_verified"))
        self.assertIn("continuous throughput remains unverified", audio.reason)

    def test_legacy_bool_gates_stay_open_but_exact_support_is_unknown(self):
        ring = ring_for(identity("2.9.0.experimental", hardware=2, numeric=(2, 9, 0, 0)))
        self.assertEqual(ring.protocol_profile, "r1_v2")
        self.assertTrue(ring.capabilities["touchpad"])
        support = ring.feature_support("touchpad")
        self.assertEqual((support.state, support.reason_code, support.source),
                         ("unknown", "firmware_unverified", "legacy_profile"))
        self.assertEqual(ring.feature_support("audio").reason_code, "hardware_not_supported")

    def test_unknown_firmware_is_distinct_from_missing_component(self):
        unknown = ring_for(identity("3.0.0", hardware=2, numeric=(3, 0, 0, 0)))
        self.assertEqual(unknown.protocol_profile, "unknown")
        self.assertEqual(unknown.feature_support("touchpad").state, "unknown")
        absent = ring_for(identity("1.2.122.public", numeric=(1, 2, 122, 0), swipe=False))
        self.assertEqual(absent.feature_support("gesture").reason_code, "component_not_available")

    def test_text_numeric_conflict_does_not_claim_supported_or_change_legacy_gate(self):
        ring = ring_for(identity(numeric=(1, 9, 0, 0)))
        self.assertTrue(ring.capabilities["touchpad"])
        self.assertEqual(ring.feature_support("touchpad").reason_code, "identity_conflict")
        self.assertEqual(ring.feature_support("touchpad").state, "unknown")

    def test_dev15_requires_matching_numeric_version_to_negotiate(self):
        ring = ring_for(identity("2.1.6-dev.touchpad.15.debug", numeric=(2, 1, 5, 0)))
        self.assertFalse(ring.capabilities["touchpad_raw"])
        self.assertEqual(ring.feature_support("touchpad").reason_code, "identity_conflict")

    def test_audio_requires_exact_known_firmware_and_microphone(self):
        for version, numeric in (("1.2.133.public", (1, 2, 133, 0)),
                                 ("2.1.6-dev.touchpad.9.debug", (2, 1, 6, 0))):
            ring = ring_for(identity(version, numeric=numeric))
            self.assertTrue(ring.capabilities["audio"])
            self.assertEqual(ring.feature_support("audio").state, "supported")
            missing = ring_for(identity(version, numeric=numeric, microphone=False))
            self.assertFalse(missing.capabilities["audio"])
            self.assertEqual(missing.feature_support("audio").reason_code, "component_not_available")
            r1 = ring_for(identity(version, numeric=numeric, hardware=2))
            self.assertFalse(r1.capabilities["audio"])
            self.assertEqual(r1.feature_support("audio").reason_code, "hardware_not_supported")
        for packet in (identity("1.2.122.public", numeric=(1, 2, 122, 0)),
                       identity("1.2.134.public", numeric=(1, 2, 134, 0)),
                       identity("1.2.133.public", numeric=(1, 2, 132, 0)),
                       identity("2.1.6-dev.touchpad.10.debug"),
                       identity("2.1.6-dev.touchpad.9.debug", numeric=(2, 1, 5, 0)),
                       identity(None)):
            ring = ring_for(packet)
            self.assertFalse(ring.capabilities["audio"])
            self.assertEqual(ring.feature_support("audio").state, "unknown")

    def test_snapshot_is_immutable_independent_and_json_serializable(self):
        ring = ring_for(identity())
        snapshot = ring.compatibility
        exported = json.loads(json.dumps(snapshot.to_dict()))
        self.assertEqual(exported["features"]["touchpad"]["state"], "supported")
        self.assertEqual(exported["firmware_numeric_version"], [2, 1, 6, 0])
        exported["features"]["touchpad"]["state"] = "unsupported"
        self.assertEqual(snapshot.feature_support("touchpad").state, "supported")
        with self.assertRaises(FrozenInstanceError):
            snapshot.product_id = "r1"
        ring.device_info = None
        self.assertEqual(snapshot.product_id, "ringo")
        self.assertEqual(ring.feature_support("touchpad").reason_code, "not_connected")
        self.assertEqual(ring.feature_support("not-a-feature").reason_code, "unknown_feature")
        self.assertIsNone(json.loads(json.dumps(ring.compatibility.to_dict()))["product_id"])


class IdentityClient(FakeClient):
    identity_packet = identity("1.2.133.public", numeric=(1, 2, 133, 0))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.info_packet = self.identity_packet

    async def write_gatt_char(self, characteristic, packet, response):
        await super().write_gatt_char(characteristic, packet, response)
        if packet[:2] == b"\x20\x00":
            self.send(b"\x20\x03" + struct.pack("<HHHI", 7, 0, 1, 123) +
                      struct.pack("<hBBH", 0, 0, 0, 4) + b"\x11\x01")


class CompatibilityTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        IdentityClient.identity_packet = identity("1.2.133.public", numeric=(1, 2, 133, 0))
        IdentityClient.invalid_info = IdentityClient.missing = False
        self.enterContext(patch("bleak.BleakClient", IdentityClient))
        self.enterContext(patch("mythring.client.retrieve_device", AsyncMock(return_value=None)))
        self.enterContext(patch("mythring.client.HealthClient.connected", AsyncMock()))
        self.ring = Ring("compatibility-transport")
        await self.ring.connect()
        self.client = IdentityClient.instances[-1]

    async def asyncTearDown(self):
        await self.ring.disconnect()

    async def test_dev9_uses_existing_explicit_audio_stream_without_auto_start(self):
        await self.ring.disconnect()
        IdentityClient.identity_packet = identity()
        await self.ring.connect()
        client = IdentityClient.instances[-1]
        self.assertEqual(self.ring.protocol_profile, "ringo_touchpad_v2")
        self.assertEqual(self.ring.feature_support("audio").reason_code, "device_protocol_verified")
        self.assertEqual(client.writes, [GET_INFO])
        first = await self.ring.start_audio()
        self.assertEqual((first.sample_count, first.sample_rate), (4, 16000))
        await self.ring.stop_audio()
        self.assertEqual(client.writes, [GET_INFO, b"\x20\x00\x01\x80\x80", b"\x20\x01"])
        self.assertFalse(self.ring.audio.active)

    async def test_queries_do_not_send_commands_and_reconnect_rebuilds_identity(self):
        self.assertEqual(self.ring.feature_support("audio").state, "supported")
        json.dumps(self.ring.compatibility.to_dict())
        self.assertEqual(self.client.writes, [GET_INFO])
        await self.ring.disconnect()
        self.assertEqual(self.ring.capabilities, {})
        IdentityClient.identity_packet = identity("2.1.6-dev.hz.6.release", hardware=2)
        await self.ring.connect()
        self.assertEqual(self.ring.device_info.product_id, "r1")
        self.assertEqual(self.ring.feature_support("audio").reason_code, "hardware_not_supported")
        self.assertEqual(self.ring.feature_support("health").state, "supported")
        with self.assertRaises(UnsupportedFeatureError):
            await self.ring.start_audio()
        self.assertEqual(IdentityClient.instances[-1].writes, [GET_INFO])

    async def test_audio_preparation_rejects_live_imu_and_touchpad(self):
        await self.ring.start_imu_tokens()
        self.assertTrue(self.ring._imu_active)
        with self.assertRaises(ProtocolError):
            await self.ring._prepare_audio()
        with self.assertRaises(ProtocolError):
            await self.ring.send_command(b"\x20\x00")
        self.assertEqual(self.client.writes, [GET_INFO, OPEN_IMU_TOKEN])
        await self.ring.stop_imu_tokens()
        self.assertFalse(self.ring._imu_active)
        self.ring.touchpad.stage = TouchpadStage.RUNNING
        with self.assertRaises(ProtocolError):
            await self.ring._prepare_audio()
        self.ring.touchpad.stage = TouchpadStage.STOPPED
        await self.ring._prepare_audio()

    async def test_active_audio_rejects_sensor_starts_but_allows_stop(self):
        with patch.object(type(self.ring.audio), "active", new=property(lambda _: True)):
            with self.assertRaises(ProtocolError):
                await self.ring.start_imu_tokens()
            await self.ring.stop_imu_tokens()
        self.assertEqual(self.client.writes, [GET_INFO, b"\x21\x01"])

    async def test_explicit_audio_wrappers_deliver_pcm_and_stop_without_autostart(self):
        frames = []
        self.ring.on("audio_frame", frames.append)
        self.assertFalse(self.ring.audio.active)
        self.assertEqual(self.client.writes, [GET_INFO])
        first = await self.ring.start_audio()
        self.assertTrue(self.ring.audio.active)
        self.assertEqual(first.samples, (0, 1, 2, 3))
        self.assertEqual(frames, [first])
        with self.assertRaises(ProtocolError):
            await self.ring.start_imu_tokens()
        await self.ring.stop_audio()
        self.assertFalse(self.ring.audio.active)
        self.assertEqual(self.client.writes[-1], b"\x20\x01")
        await self.ring.start_imu_tokens()
        await self.ring.disconnect()
        self.assertFalse(self.ring._imu_active)
        self.assertFalse(self.ring.audio.active)

    async def test_raw_microphone_commands_cannot_bypass_stream_state(self):
        for command in (b"\x20\x00\x01\x80\x80", b"\x20\x01"):
            with self.assertRaisesRegex(ProtocolError, "start_audio.*stop_audio"):
                await self.ring.send_command(command)
        self.assertEqual(self.client.writes, [GET_INFO])
        self.assertFalse(self.ring.audio.active)
        await self.ring.start_audio()
        written = list(self.client.writes)
        with self.assertRaises(ProtocolError):
            await self.ring.send_command(b"\x20\x01")
        self.assertTrue(self.ring.audio.active)
        with self.assertRaises(ProtocolError):
            await self.ring.start_imu_tokens()
        self.assertEqual(self.client.writes, written)
        await self.ring.stop_audio()
        await self.ring.start_imu_tokens()

    async def test_update_preparation_before_model_load_does_not_start_model(self):
        self.ring.device_info = parse_device_info(identity())
        self.ring._firmware_update_owner = object()
        start = self.enterContext(patch.object(self.ring._recognizer, "start", AsyncMock()))
        try:
            with self.assertRaises(ProtocolError):
                await self.ring.start_touchpad()
            start.assert_not_awaited()
            self.assertEqual(self.client.writes, [GET_INFO])
        finally:
            self.ring._firmware_update_owner = None

    async def test_update_started_during_model_load_cleans_up_without_wire_start(self):
        self.ring.device_info = parse_device_info(identity())

        async def model_loaded():
            self.ring._recognizer._active = True
            self.ring._firmware_update_owner = object()

        self.enterContext(patch.object(self.ring._recognizer, "start", AsyncMock(side_effect=model_loaded)))
        wire_start = self.enterContext(patch.object(self.ring.touchpad, "start", AsyncMock()))
        try:
            with self.assertRaises(ProtocolError):
                await self.ring.start_touchpad()
            self.assertFalse(self.ring._recognizer.running)
            wire_start.assert_not_awaited()
            self.assertEqual(self.client.writes, [GET_INFO])
        finally:
            self.ring._firmware_update_owner = None

    async def test_update_preparation_blocks_new_capture_but_allows_cleanup(self):
        await self.ring.start_imu_tokens()
        self.ring._firmware_update_owner = object()
        try:
            await self.ring.stop_imu_tokens()
            with self.assertRaises(ProtocolError):
                await self.ring.start_imu_tokens()
            with self.assertRaises(ProtocolError):
                await self.ring.start_audio()
            self.assertFalse(self.ring.audio.active)
        finally:
            self.ring._firmware_update_owner = None

    async def test_health_ownership_blocks_sensor_starts_but_allows_stops(self):
        self.ring.device_info = parse_device_info(identity("2.1.6-dev.hz.6.release", hardware=2))
        self.ring.health.connection_checked = True
        await self.ring.start_imu_tokens()
        self.assertTrue(self.ring._imu_active)
        self.ring.health.connection_checked = False
        for kind in (0x21, 0x24, 0x28):
            with self.assertRaises(ProtocolError):
                await self.ring.send_command(bytes((kind, 0)))
        await self.ring.stop_imu_tokens()
        self.assertFalse(self.ring._imu_active)
        for kind in (0x24, 0x28):
            await self.ring.send_command(bytes((kind, 1)))
        self.assertEqual(self.client.writes,
                         [GET_INFO, OPEN_IMU_TOKEN, b"\x21\x01", b"\x24\x01", b"\x28\x01"])


if __name__ == "__main__":
    unittest.main()
