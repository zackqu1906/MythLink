import hashlib
import io
import json
import struct
import unittest
import zipfile

from mythring.ota import FirmwarePackage, FirmwarePackageError, OtaUnsupportedError


def signed_image(*, flags=0, load_addr=0, protected=False):
    body = b"firmware payload"
    protected_tlvs = struct.pack("<HHHHI", 0x6908, 12, 0x50, 4, 7) if protected else b""
    header = struct.pack("<IIHHIIBBHI4x", 0x96F3B83D, load_addr, 32, len(protected_tlvs), len(body), flags,
                         2, 1, 6, 5)
    image = header + body + protected_tlvs
    digest = hashlib.sha256(image).digest()
    return image + struct.pack("<HHHH", 0x6907, 40, 0x10, 32) + digest, digest


def zip_package(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries:
            archive.writestr(name, value)
    return output.getvalue()


class FirmwarePackageTests(unittest.TestCase):
    def test_header_digest_and_plaintext_integrity(self):
        for protected in (False, True):
            with self.subTest(protected=protected):
                image, digest = signed_image(protected=protected)
                firmware = FirmwarePackage.parse(image)
                self.assertEqual(firmware.version, "2.1.6+5")
                self.assertEqual(firmware.verification_hash(), digest)
                self.assertTrue(firmware.verify_integrity())

    def test_zip_runtime_version_and_info_fallback(self):
        image, _ = signed_image()
        metadata = {"displayVersion": "2.1.6-dev.touchpad.5", "variant": "debug", "infoVersion": "fallback"}
        package = FirmwarePackage.parse(zip_package([
            ("firmware/r1.signed.bin", image), ("build-info.json", json.dumps(metadata)), ("README.txt", "ignored")]))
        self.assertEqual(package.runtime_version, "2.1.6-dev.touchpad.5.debug")
        metadata["variant"] = "unknown"
        package = FirmwarePackage.parse(zip_package([("r1.signed.bin", image), ("build-info.json", json.dumps(metadata))]))
        self.assertEqual(package.runtime_version, "fallback")

    def test_bad_image_layouts(self):
        image, _ = signed_image()
        for malformed in (b"", b"x" * 32, image[:40], signed_image(flags=0x100)[0], signed_image(load_addr=0x20000)[0]):
            with self.subTest(malformed=malformed), self.assertRaises(FirmwarePackageError):
                FirmwarePackage.parse(malformed)

    def test_malformed_and_ambiguous_zip(self):
        image, _ = signed_image()
        bad = [b"PKxx", zip_package([("other.bin", image)]),
               zip_package([("a.signed.bin", image), ("b.signed.bin", image)]),
               zip_package([("a.signed.bin", image), ("x/build-info.json", "{}"), ("y/build-info.json", "{}")]),
               zip_package([("a.signed.bin", image), ("build-info.json", "[]")]),
               zip_package([("a.signed.bin", image), ("build-info.json", "x" * 65537)])]
        for malformed in bad:
            with self.subTest(length=len(malformed)), self.assertRaises(FirmwarePackageError):
                FirmwarePackage.parse(malformed)

    def test_digest_corruption_and_missing_tlv(self):
        image, _ = signed_image()
        damaged = bytearray(image)
        damaged[32] ^= 1
        with self.assertRaisesRegex(FirmwarePackageError, "digest mismatch"):
            FirmwarePackage.parse(damaged).verify_integrity()
        with self.assertRaisesRegex(FirmwarePackageError, "Missing MCUboot TLV"):
            FirmwarePackage.parse(image[:-40]).verification_hash()

    def test_encrypted_digest_is_identification_only(self):
        image, digest = signed_image(flags=0x04)
        package = FirmwarePackage.parse(image)
        self.assertEqual(package.verification_hash(), digest)
        with self.assertRaises(OtaUnsupportedError):
            package.verify_integrity()


if __name__ == "__main__":
    unittest.main()
