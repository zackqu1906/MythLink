"""Read deployment requirements without loading code or requiring Xcode tools."""
from __future__ import annotations

from pathlib import Path
import struct


_THIN = {b'\xcf\xfa\xed\xfe': ('<', 32), b'\xfe\xed\xfa\xcf': ('>', 32),
         b'\xce\xfa\xed\xfe': ('<', 28), b'\xfe\xed\xfa\xce': ('>', 28)}
_FAT = {b'\xca\xfe\xba\xbe': ('>', False), b'\xbe\xba\xfe\xca': ('<', False),
        b'\xca\xfe\xba\xbf': ('>', True), b'\xbf\xba\xfe\xca': ('<', True)}
_ARCHES = {0x100000c: 'arm64', 0x1000007: 'x86_64'}


def version_tuple(value):
    parts = tuple(int(p) for p in str(value).split('.'))
    return parts + (0,) * max(0, 3 - len(parts))


def _version(raw):
    return f'{raw >> 16}.{(raw >> 8) & 255}.{raw & 255}'


def _read(stream, offset, size):
    stream.seek(offset)
    data = stream.read(size)
    if len(data) != size:
        raise ValueError('Truncated Mach-O header or load command')
    return data


def _slices(stream, offset=0, *, nested=False):
    magic = _read(stream, offset, 4)
    if magic in _FAT and not nested:
        endian, wide = _FAT[magic]
        count, = struct.unpack(endian + 'I', _read(stream, offset + 4, 4))
        if not 0 < count <= 32:
            raise ValueError('Invalid Mach-O architecture count')
        result = []
        for index in range(count):
            data = _read(stream, offset + 8 + index * (32 if wide else 20), 32 if wide else 20)
            start, = struct.unpack_from(endian + ('Q' if wide else 'I'), data, 8)
            result.extend(_slices(stream, start, nested=True))
        return result
    if magic not in _THIN:
        if nested:
            raise ValueError('Invalid Mach-O architecture slice')
        return []
    endian, header_size = _THIN[magic]
    header = _read(stream, offset, header_size)
    cpu, = struct.unpack_from(endian + 'I', header, 4)
    count, size = struct.unpack_from(endian + 'II', header, 16)
    if size > 16 * 1024 * 1024 or count > size // 8:
        raise ValueError('Invalid Mach-O load command count')
    commands = _read(stream, offset + header_size, size)
    record = dict(architecture=_ARCHES.get(cpu, hex(cpu)), minimum_macos=None,
                  dependencies=[], rpaths=[])
    position = 0
    for _ in range(count):
        if position + 8 > size:
            raise ValueError('Truncated Mach-O load commands')
        command, length = struct.unpack_from(endian + 'II', commands, position)
        if length < 8 or position + length > size:
            raise ValueError('Invalid Mach-O load command size')
        data = commands[position:position + length]
        if command == 0x32 and length >= 24:  # LC_BUILD_VERSION
            platform, minimum = struct.unpack_from(endian + 'II', data, 8)
            if platform == 1:
                record['minimum_macos'] = _version(minimum)
        elif command == 0x24 and length >= 16:  # LC_VERSION_MIN_MACOSX
            record['minimum_macos'] = _version(struct.unpack_from(endian + 'I', data, 8)[0])
        elif command in (0xc, 0x80000018, 0x8000001f, 0x20, 0x80000023, 0x8000001c):
            if length < 12:
                raise ValueError('Truncated Mach-O library command')
            start, = struct.unpack_from(endian + 'I', data, 8)
            if not 12 <= start < length:
                raise ValueError('Invalid Mach-O library name offset')
            value = data[start:].split(b'\0', 1)[0].decode('utf-8')
            record['rpaths' if command == 0x8000001c else 'dependencies'].append(value)
        position += length
    return [record]


def inspect_macho(path: Path):
    with Path(path).open('rb') as stream:
        if stream.read(4) not in (*_THIN, *_FAT):
            return []
        return _slices(stream)
