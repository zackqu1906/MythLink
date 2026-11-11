import asyncio
import hashlib
from pathlib import Path
import tempfile
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace

from mythring.models import DeviceInfo
from mythring.ota import (FirmwarePackage, FirmwareUpdateState, OtaError, RingOtaUpdater,
                         parse_image_states, verify_firmware)
from test_ota import signed_image


MAC = "01:02:03:04:05:06"


class FakeRing:
    def __init__(self):
        self.is_connected = True
        self._client = object()
        self._generation = 12
        self._device = SimpleNamespace(address="corebluetooth-uuid")
        self.device_info = DeviceInfo("1.0.0.debug")
        self._firmware_maintenance = False

    async def _request(self, command, prefix, timeout, generation):
        assert generation == self._generation and command == b"\x2d\x01"
        return b"\x2d\x02\x01\x01\x02\x03\x04\x05\x06"

    async def disconnect(self):
        if self._client is not None:
            self._generation += 1
            self._client = None
            self.is_connected = False

    async def get_battery_level(self):
        return 40


class FakeScanner:
    starts = stops = 0

    def __init__(self, **kwargs):
        pass

    async def start(self):
        type(self).starts += 1

    async def stop(self):
        type(self).stops += 1


class FakeServer:
    def __init__(self, firmware):
        self.firmware = firmware
        self.upload = bytearray()
        self.pending = False
        self.booted = False
        self.auto_confirm = True
        self.confirmed = False
        self.confirm_writes = 0
        self.resets = 0
        self.opens = self.closes = 0
        self.identity_reads = 0
        self.interrupt_once = False
        self.did_interrupt = False
        self.bad_identity = False
        self.match = True
        self.requests = []
        self.pause_upload = None

    def states(self):
        selected = {"slot": 0 if self.booted else 1, "version": self.firmware.version,
                    "hash": self.firmware.verification_hash(), "active": self.booted,
                    "bootable": True, "confirmed": self.confirmed, "pending": self.pending}
        old = {"slot": 1 if self.booted else 0, "version": "1.0.0", "hash": b"o" * 32,
               "active": not self.booted, "bootable": True, "confirmed": not self.booted, "pending": False}
        return {"images": [old] + ([selected] if len(self.upload) == len(self.firmware.image_bytes) else [])}

    def transport(self, device):
        server = self

        class Session:
            async def __aenter__(self):
                server.opens += 1
                return self

            async def __aexit__(self, *_):
                server.closes += 1

            async def identity(self, address):
                server.identity_reads += 1
                if server.bad_identity or address != MAC:
                    raise OtaError("Wrong R1 hardware address")
                return DeviceInfo("2.1.6.debug" if server.booted else "1.0.0.debug")

            async def request(self, op, group, command, payload=None):
                payload = payload or {}
                server.requests.append((op, group, command, payload))
                if (op, group, command) == (0, 0, 6):
                    return {"buf_size": 128, "buf_count": 3}
                if (op, group, command) == (0, 1, 0):
                    return server.states()
                if (op, group, command) == (2, 1, 1):
                    if server.pause_upload is not None:
                        await server.pause_upload.wait()
                    offset = payload["off"]
                    if offset == 0:
                        assert payload["sha"] == hashlib.sha256(server.firmware.image_bytes).digest()
                        assert payload["len"] == len(server.firmware.image_bytes)
                        if server.upload:
                            return {"off": len(server.upload)}
                    if server.interrupt_once and offset and not server.did_interrupt:
                        server.did_interrupt = True
                        raise ConnectionError("simulated disconnect")
                    assert offset == len(server.upload)
                    server.upload.extend(payload["data"])
                    result = {"off": len(server.upload)}
                    if len(server.upload) == len(server.firmware.image_bytes):
                        assert bytes(server.upload) == server.firmware.image_bytes
                        result["match"] = server.match
                    return result
                if (op, group, command) == (2, 1, 0):
                    assert payload["hash"] == server.firmware.verification_hash()
                    if payload["confirm"]:
                        assert server.booted
                        server.confirm_writes += 1
                        server.confirmed = True
                    else:
                        server.pending = True
                    return server.states()
                if (op, group, command) == (2, 0, 5):
                    server.resets += 1
                    server.booted, server.pending = True, False
                    server.confirmed = server.auto_confirm
                    return {}
                raise AssertionError((op, group, command, payload))

        return Session()


class OtaPolicyTests(unittest.TestCase):
    def setUp(self):
        self.firmware = FirmwarePackage.parse(signed_image()[0])
        self.server = FakeServer(self.firmware)
        self.server.upload.extend(self.firmware.image_bytes)
        self.server.booted = self.server.confirmed = True

    def test_full_verification_and_version_normalization(self):
        images = parse_image_states(self.server.states())
        result = verify_firmware("2.1.6-dev.touchpad.5.debug", self.firmware, images)
        self.assertEqual(result.image_version, "2.1.6+5")
        self.assertEqual(verify_firmware("2.1.6.debug", self.firmware,
                         tuple(replace(image, version="2.1.6.5") if image.active else image for image in images)).hash,
                         self.firmware.verification_hash())

    def test_digest_pending_runtime_and_slot_mismatches(self):
        images = parse_image_states(self.server.states())
        for field in ({"hash": b"x" * 32}, {"confirmed": False}, {"pending": True}, {"slot": 1}, {"version": "1.0.0"}):
            altered = tuple(replace(image, **field) if image.active else image for image in images)
            with self.subTest(field=field), self.assertRaises(OtaError):
                verify_firmware("2.1.6.debug", self.firmware, altered)
        with self.assertRaises(OtaError):
            verify_firmware("2.1.7.debug", self.firmware, images)
        with self.assertRaises(OtaError):
            verify_firmware("2.1.6.debug", replace(self.firmware, runtime_version="2.1.6.release"), images)


class OtaLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "r1.signed.bin"
        self.path.write_bytes(signed_image()[0])
        self.firmware = FirmwarePackage.from_file(self.path)
        self.ring = FakeRing()
        self.server = FakeServer(self.firmware)
        self.states = []
        self.progress = []
        self.updater = RingOtaUpdater(self.ring, on_state=self.states.append,
                                      on_progress=lambda sent, total: self.progress.append((sent, total)),
                                      transport_factory=self.server.transport, scanner_factory=FakeScanner,
                                      swap_delay=0, retry_delay=0.001, recovery_timeout=0.06, upload_timeout=2)

    async def asyncTearDown(self):
        self.updater.cancel()
        self.temp.cleanup()

    async def test_complete_test_only_workflow(self):
        result = await self.updater.update(self.path)
        self.assertEqual(result.hash, self.firmware.verification_hash())
        self.assertEqual(self.server.confirm_writes, 0)
        self.assertEqual(self.server.resets, 1)
        self.assertEqual(self.server.opens, self.server.closes)
        self.assertFalse(self.ring._firmware_maintenance)
        self.assertFalse(self.ring.is_connected)
        self.assertEqual(self.progress[-1], (len(self.firmware.image_bytes), len(self.firmware.image_bytes)))
        self.assertEqual(self.states, [FirmwareUpdateState.VALIDATING, FirmwareUpdateState.UPLOADING,
                                      FirmwareUpdateState.TESTING, FirmwareUpdateState.RESETTING,
                                      FirmwareUpdateState.WAITING_FOR_ADVERTISE, FirmwareUpdateState.SUCCESS])

    async def test_upload_reconnect_resumes_by_full_file_hash(self):
        self.server.interrupt_once = True
        await self.updater.update(self.path)
        self.assertTrue(self.server.did_interrupt)
        starts = [payload for op, group, command, payload in self.server.requests
                  if (op, group, command) == (2, 1, 1) and payload["off"] == 0]
        self.assertEqual(len(starts), 2)
        self.assertEqual(self.server.resets, 1)

    async def test_explicit_confirmation_is_after_identity_and_boot(self):
        self.server.auto_confirm = False
        await self.updater.update(self.path, confirm=True)
        self.assertEqual(self.server.confirm_writes, 1)
        self.assertGreaterEqual(self.server.identity_reads, 2)

    async def test_default_does_not_confirm_and_recovery_is_read_only(self):
        self.server.auto_confirm = False
        with self.assertRaises(asyncio.TimeoutError):
            await self.updater.update(self.path)
        self.assertEqual(self.server.confirm_writes, 0)
        self.server.confirmed = True
        before = len(self.server.requests)
        await self.updater.verify_installed(self.path)
        self.assertTrue(all(op == 0 for op, _, _, _ in self.server.requests[before:]))
        self.assertEqual(self.server.resets, 1)

    async def test_bad_identity_never_uploads(self):
        self.server.bad_identity = True
        with self.assertRaisesRegex(OtaError, "hardware address"):
            await self.updater.update(self.path, confirm=True)
        self.assertEqual(self.server.requests, [])

    async def test_device_hash_failure_does_not_test_or_reset(self):
        self.server.match = False
        with self.assertRaisesRegex(OtaError, "SHA-256"):
            await self.updater.update(self.path)
        self.assertEqual(self.server.resets, 0)
        self.assertFalse(self.server.pending)

    async def test_battery_opt_in_and_expired_target(self):
        with self.assertRaisesRegex(OtaError, "battery"):
            await self.updater.update(self.path, min_battery=50)
        self.assertTrue(self.ring.is_connected)
        target = await self.updater.prepare_target()
        target.created_at = time.monotonic() - 61
        with self.assertRaisesRegex(OtaError, "Expired"):
            await self.updater.update(self.path, target=target)
        self.assertEqual(self.server.opens, 0)

    async def test_cancel_closes_transport_and_emits_one_terminal(self):
        self.server.pause_upload = asyncio.Event()
        task = asyncio.create_task(self.updater.update(self.path))
        for _ in range(100):
            if self.updater.state == FirmwareUpdateState.UPLOADING:
                break
            await asyncio.sleep(0.01)
        self.updater.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.states.count(FirmwareUpdateState.CANCELLED), 1)
        self.assertEqual(self.server.opens, self.server.closes)
        self.assertFalse(self.ring._firmware_maintenance)
        self.assertFalse(self.updater.is_running)


if __name__ == "__main__":
    unittest.main()
