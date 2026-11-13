"""Bounded MCUboot image parsing; the ring's bootloader verifies the signature."""
from __future__ import annotations

import hashlib
import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .types import OtaError, OtaPhase

MAX_IMAGE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class LoadedOtaImage:
    data: bytes
    version: str
    hash_bytes: bytes
    source_name: str

    @property
    def hash_hex(self):
        return self.hash_bytes.hex()


def _fail(message):
    raise OtaError(OtaPhase.PREFLIGHT, message)


def _read_input(path):
    if not path.is_file() or path.stat().st_size > MAX_IMAGE_BYTES * 2:
        _fail(f"image missing or too large: {path}")
    if path.suffix.lower() != '.zip':
        if not path.name.lower().endswith('.signed.bin'):
            _fail('image must be a DFU .zip or MCUboot .signed.bin')
        return path.read_bytes(), path.name
    with zipfile.ZipFile(path) as archive:
        matches = [i for i in archive.infolist() if not i.is_dir()
                   and i.filename.lower().endswith('.signed.bin')]
        if len(matches) != 1:
            _fail('DFU zip must contain exactly one *.signed.bin')
        item = matches[0]
        member = PurePosixPath(item.filename)
        if member.is_absolute() or '..' in member.parts or item.file_size > MAX_IMAGE_BYTES:
            _fail('invalid signed image archive member')
        return archive.read(item), item.filename


def _inspect(data, source_name):
    if not 32 <= len(data) <= MAX_IMAGE_BYTES:
        _fail('invalid MCUboot image size')
    magic, load, header_size, protected_size, size, flags, major, minor, revision, build = struct.unpack_from('<IIHHIIBBHI', data)
    if magic != 0x96F3B83D or header_size < 32 or header_size + size > len(data):
        _fail('invalid MCUboot header')
    if load != 0 or flags & 0x100:
        _fail('image is not compatible with the Ringo legacy flash layout')
    if flags & ~0x100:
        _fail('unsupported MCUboot flags (non-bootable, encrypted or compressed image)')
    offset = header_size + size
    if protected_size:
        if offset + protected_size > len(data) or protected_size < 4:
            _fail('invalid protected TLV size')
        p_magic, p_size = struct.unpack_from('<HH', data, offset)
        if p_magic != 0x6908 or p_size != protected_size:
            _fail('invalid protected TLV header')
        offset += protected_size
    covered = data[:offset]
    if offset + 4 > len(data):
        _fail('missing MCUboot TLVs')
    tlv_magic, tlv_size = struct.unpack_from('<HH', data, offset)
    end = offset + tlv_size
    if tlv_magic != 0x6907 or tlv_size < 4 or end > len(data):
        _fail('invalid MCUboot TLV area')
    offset += 4
    digest = None
    signed = False
    while offset < end:
        if offset + 4 > end:
            _fail('truncated TLV header')
        kind, length = struct.unpack_from('<HH', data, offset)
        offset += 4
        if offset + length > end:
            _fail('truncated TLV value')
        value = data[offset:offset + length]
        offset += length
        if kind in (0x10, 0x11, 0x12):
            algorithm = {0x10: 'sha256', 0x11: 'sha384', 0x12: 'sha512'}[kind]
            if value != hashlib.new(algorithm, covered).digest():
                _fail('MCUboot image hash mismatch')
            if digest is not None:
                _fail('ambiguous MCUboot hash TLVs')
            digest = value
        if kind in (0x20, 0x22, 0x23, 0x24) and length:
            signed = True
    if digest is None or not signed:
        _fail('MCUboot image requires a hash and signature TLV')
    return LoadedOtaImage(data, f'{major}.{minor}.{revision}+{build}', digest, source_name)


def load_ota_image(path: str | Path) -> LoadedOtaImage:
    try:
        data, name = _read_input(Path(path).expanduser())
        return _inspect(data, name)
    except OtaError:
        raise
    except (OSError, ValueError, struct.error, zipfile.BadZipFile, RuntimeError) as exc:
        _fail(f'invalid firmware image: {exc}')
