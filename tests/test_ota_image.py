from __future__ import annotations

import struct
import zipfile
from hashlib import sha512
from pathlib import Path

import pytest

from ring_python_sdk.ota.image import load_ota_image
from ring_python_sdk.ota.types import OtaError, OtaPhase


def make_signed_image(*, load_addr: int = 0, flags: int = 0) -> bytes:
    body = b"ringo-test-image"
    header = struct.pack(
        "<IIHHIIBBHI4x",
        0x96F3B83D,
        load_addr,
        32,
        0,
        len(body),
        flags,
        1,
        2,
        3,
        4,
    )
    digest = sha512(header + body).digest()
    signature = b"\x5a" * 64
    tlv_total = 4 + 4 + len(digest) + 4 + len(signature)
    tlv = struct.pack("<HH", 0x6907, tlv_total)
    tlv += struct.pack("<BxH", 0x12, len(digest)) + digest
    tlv += struct.pack("<BxH", 0x24, len(signature)) + signature
    return header + body + tlv


def test_load_signed_bin_extracts_version_hash_and_layout(tmp_path: Path) -> None:
    path = tmp_path / "ringo.signed.bin"
    path.write_bytes(make_signed_image())

    image = load_ota_image(path)

    assert image.version == "1.2.3+4"
    assert image.data == path.read_bytes()
    assert image.hash_bytes == sha512(make_signed_image()[:32 + len(b"ringo-test-image")]).digest()
    assert image.source_name == "ringo.signed.bin"


def test_load_zip_requires_exactly_one_signed_bin(tmp_path: Path) -> None:
    good = tmp_path / "good.zip"
    with zipfile.ZipFile(good, "w") as archive:
        archive.writestr("nested/ring.signed.bin", make_signed_image())
    assert load_ota_image(good).source_name == "nested/ring.signed.bin"

    bad = tmp_path / "ambiguous.zip"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("one.signed.bin", make_signed_image())
        archive.writestr("two.signed.bin", make_signed_image())
    with pytest.raises(OtaError, match="exactly one") as raised:
        load_ota_image(bad)
    assert raised.value.phase is OtaPhase.PREFLIGHT


@pytest.mark.parametrize(
    ("load_addr", "flags"),
    [(0xE000, 0), (0, 0x100)],
)
def test_load_image_rejects_non_legacy_layout(
    tmp_path: Path, load_addr: int, flags: int
) -> None:
    path = tmp_path / "bad.signed.bin"
    path.write_bytes(make_signed_image(load_addr=load_addr, flags=flags))
    with pytest.raises(OtaError, match="legacy"):
        load_ota_image(path)
