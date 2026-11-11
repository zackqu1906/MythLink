import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from mythring import catalog
from mythring.models import DeviceInfo
from mythring.ota import FirmwarePackage, FirmwarePackageError, OtaError, RingOtaUpdater


def ring(code=2, version="2.1.5.public", ota=True):
    return SimpleNamespace(is_connected=True,
        device_info=DeviceInfo(version, code), capabilities={"ota": ota})


class FirmwareCatalogTests(unittest.TestCase):
    def test_attachments_survive_distribution_and_preserve_distinct_identity(self):
        targets = catalog.list_firmware_targets()
        self.assertEqual({item["product_id"] for item in targets}, {"ringo", "r1"})
        self.assertIn("ringo-touchpad-dev15", {item["target_id"] for item in targets})
        self.assertEqual(len({item["image_sha256"] for item in targets}), len(targets))
        for target in targets:
            path = catalog.bundled_firmware_path(target["target_id"])
            self.assertTrue(path.is_file())
            package = FirmwarePackage.from_file(path)
            self.assertEqual(package.runtime_version, target["expected_info_version"])
            self.assertEqual(package.version, target["image_version"])
        # Callers cannot mutate a shared cached catalog.
        targets[0]["product_id"] = "changed"
        self.assertNotEqual(catalog.list_firmware_targets()[0]["product_id"], "changed")

    def test_no_device_cross_product_and_missing_ota_evidence_are_distinct(self):
        self.assertEqual(catalog.evaluate_target(None, "r1-hz6-release")["reason_code"], "not_connected")
        self.assertEqual(catalog.evaluate_target(ring(), "ringo-touchpad-dev9")["reason_code"], "product_mismatch")
        result = catalog.evaluate_target(ring(), "r1-hz6-release")
        self.assertEqual(result["reason_code"], "target_constraints_unverified")
        self.assertFalse(result["can_update"])
        self.assertTrue(result["can_verify"])
        result = catalog.evaluate_target(ring(1, ota=False), "ringo-touchpad-dev9")
        self.assertEqual(result["reason_code"], "sdk_ota_not_supported")
        self.assertFalse(result["can_update"])

    def test_equal_text_is_not_image_verification(self):
        result = catalog.evaluate_target(ring(version="2.1.6-dev.hz.6.release"), "r1-hz6-release")
        self.assertEqual(result["status"], "same_version")
        self.assertTrue(result["same_version"])
        self.assertFalse(result["can_update"])
        self.assertTrue(result["can_verify"])

    def test_known_cross_product_image_is_rejected_even_without_zip_filename(self):
        for target in ("ringo-touchpad-dev9", "ringo-touchpad-dev15"):
            package = FirmwarePackage.from_file(catalog.bundled_firmware_path(target))
            with self.subTest(target=target), self.assertRaisesRegex(FirmwarePackageError, "product mismatch"):
                catalog.check_known_package_product(package, ring().device_info)
            catalog.check_known_package_product(package, ring(1).device_info)

    def test_tampered_attachment_and_escaping_filename_are_rejected(self):
        data = catalog._catalog()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = data["targets"][0]
            (root / target["package_file"]).write_bytes(b"not the original firmware")
            (root / "catalog.json").write_text(json.dumps(data))
            with patch.object(catalog, "_DIRECTORY", root):
                with self.assertRaisesRegex(FirmwarePackageError, "differs from its catalog"):
                    catalog.bundled_firmware_path(target["target_id"])
                data["targets"][0]["package_file"] = "../outside.zip"
                (root / "catalog.json").write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    catalog.list_firmware_targets()

    def test_unknown_target_is_not_interpreted_as_a_path(self):
        with self.assertRaisesRegex(ValueError, "Unknown firmware target"):
            catalog.get_firmware_target("../anything.zip")


class TargetUpdaterTests(unittest.IsolatedAsyncioTestCase):
    async def test_unverified_target_rejected_before_device_io(self):
        updater = RingOtaUpdater(ring())
        with self.assertRaises(OtaError):
            await updater.update_target("r1-hz6-release")
        self.assertFalse(updater.is_running)

    async def test_cross_product_rejected_before_readback_or_update(self):
        updater = RingOtaUpdater(ring())
        with self.assertRaises(OtaError):
            await updater.verify_target("ringo-touchpad-dev9")
        with self.assertRaises(OtaError):
            await updater.update_target("ringo-touchpad-dev9")

    async def test_legacy_path_also_rejects_cross_product_before_device_io(self):
        updater = RingOtaUpdater(ring())
        updater.prepare_target = AsyncMock()
        with self.assertRaisesRegex(FirmwarePackageError, "product mismatch"):
            await updater.update(catalog.bundled_firmware_path("ringo-touchpad-dev9"))
        updater.prepare_target.assert_not_awaited()

    async def test_dev15_renamed_or_extracted_image_is_rejected_at_all_r1_entry_points(self):
        path = catalog.bundled_firmware_path("ringo-touchpad-dev15")
        with tempfile.TemporaryDirectory() as temporary:
            for name, data in (("renamed.zip", path.read_bytes()),
                               ("extracted.bin", FirmwarePackage.from_file(path).image_bytes)):
                copy = Path(temporary) / name
                copy.write_bytes(data)
                for connected in (True, False):
                    device = ring()
                    device.is_connected = connected
                    device.disconnect = AsyncMock()
                    updater = RingOtaUpdater(device)
                    for operation in (updater.update, updater.verify_installed, updater.confirm_installed):
                        with self.subTest(name=name, connected=connected, operation=operation.__name__):
                            with self.assertRaisesRegex(FirmwarePackageError, "product mismatch"):
                                await operation(copy)
                    device.disconnect.assert_not_awaited()

    async def test_failed_capture_stop_prevents_maintenance_and_upload(self):
        device = ring()
        device.audio = SimpleNamespace(active=True, stop=AsyncMock(side_effect=OSError("STOP rejected")))
        device.disconnect = AsyncMock()
        updater = RingOtaUpdater(device)
        permit = SimpleNamespace(consume=Mock())
        updater.prepare_target = AsyncMock(return_value=permit)
        updater._upload_and_test = AsyncMock()
        with self.assertRaisesRegex(OSError, "STOP rejected"):
            await updater.update(catalog.bundled_firmware_path("r1-hz6-release"))
        permit.consume.assert_not_called()
        device.disconnect.assert_not_awaited()
        updater._upload_and_test.assert_not_awaited()
        self.assertIsNone(device._firmware_update_owner)


if __name__ == "__main__":
    unittest.main()
