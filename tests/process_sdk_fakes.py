"""Spawn-importable offline devices; never connect BLE or emit system input."""
import asyncio
import os
from pathlib import Path
import struct
import sys
from types import SimpleNamespace

from ring_python_sdk.sdk_provider import protocol as p


def status(state=1, result=0):
    packet = bytearray(64)
    packet[:4] = bytes((0x3c, 0x11, 2, state))
    struct.pack_into('<iII', packet, 4, result, 42, 4)
    struct.pack_into('<IHH4B', packet, 44, p.MODEL_ID, 200, 100, 8, 255, 1, 1)
    return bytes(packet)


def token(sequence):
    return bytes((0x3c, 2, 2, int(sequence == 0))) + struct.pack('<4I8b',
        42, 4, sequence, 100+10*sequence, 32, 64, -32, 16, -16, 32, -64, 0)


class Client:
    def __init__(self, *_args, disconnected_callback, **_kwargs):
        self.target = str(_args[0]) if _args else ''
        self.is_connected = False
        self.mtu_size = 247
        self.callback = None
        self.disconnected_callback = disconnected_callback
        self.stream = None
        self.mic = None
        self.fault = False
        self.starts = 0
        self.services = SimpleNamespace(get_service=lambda _: object(),
            get_characteristic=lambda _: SimpleNamespace(properties=['write', 'notify']))

    async def connect(self):
        assert not {'proximic_ring', 'PySide6', 'torch'} & sys.modules.keys()
        if self.target.startswith('wait_connect:'):
            Path(self.target.partition(':')[2]).touch()
            await asyncio.Event().wait()
        self.is_connected = True

    async def disconnect(self):
        if self.stream is not None:
            self.stream.cancel()
            await asyncio.gather(self.stream, return_exceptions=True)
        if self.mic is not None:
            self.mic.cancel()
            await asyncio.gather(self.mic, return_exceptions=True)
        self.is_connected = False
        self.disconnected_callback(self)

    async def start_notify(self, _uuid, callback):
        self.callback = callback

    async def write_gatt_char(self, _uuid, command, *, response):
        assert response
        if command == p.INFO:
            self.callback(None, bytes.fromhex('2a02010102010600010801010000'))
        elif command == p.START:
            self.starts += 1
            if self.target == 'busy_start':
                self.callback(None, status(0, -16))
                return
            self.callback(None, status())
            if self.target == 'no_tokens':
                return
            self.callback(None, token(0))
            async def stream():
                sequence = 1
                while True:
                    await asyncio.sleep(.01)
                    self.callback(None, token(sequence))
                    sequence += 1
            self.stream = asyncio.create_task(stream())
        elif command == p.STOP:
            if self.stream is not None:
                self.stream.cancel()
                await asyncio.gather(self.stream, return_exceptions=True)
                self.stream = None
            self.callback(None, status(0))
        elif command == p.STATUS:
            self.callback(None, status(2 if self.fault else 1, -116 if self.fault else 0))
        elif command == b'\x3c\x04':
            self.callback(None, b'\x3c\x12\x02\x00' + struct.pack('<15I',
                42, 0, 2, 0, 43, 3, 3, 555494, 640, 0, 128, 0, 43, 0, 3 if self.fault else 0))
        elif command == p.BATTERY:
            self.callback(None, bytes((0x29, 2, 0, 0, self.starts)))
        elif command == b'\xff\x01':
            self.fault = True
            if self.stream is not None:
                self.stream.cancel()
                await asyncio.gather(self.stream, return_exceptions=True)
                self.stream = None
        elif command == b'\xff\x03':
            os._exit(17)
        elif command[:2] == b'\x20\x00':
            async def audio():
                sequence = 1
                while True:
                    await asyncio.sleep(.1)
                    block = struct.pack('<hBBH', 100, 0, 0, 1600) + bytes(800)
                    self.callback(None, b'\x20\x03' + struct.pack('<HHHI', sequence, 0, 1, sequence*100) + block)
                    sequence += 1
            self.mic = asyncio.create_task(audio())
        elif command[:2] == b'\x20\x01':
            if self.mic is not None:
                self.mic.cancel()
                await asyncio.gather(self.mic, return_exceptions=True)
                self.mic = None
        elif command == b'\x26\x00':
            asyncio.get_running_loop().call_later(.02, self.callback, None,
                b'\x26\x07' + struct.pack('<HBIf', 1, 5, 1234, .75))
        elif command == b'\x26\x01' or command[:1] in (b'\x31',):
            pass
        else:
            raise AssertionError(command.hex())


class Model:
    def __init__(self):
        assert not {'proximic_ring', 'PySide6', 'torch'} & sys.modules.keys()

    def reset_for_epoch(self):
        pass

    def step(self, features):
        return SimpleNamespace(reshape=lambda _: SimpleNamespace(tolist=lambda: [.1, .2, -8., -8.]*2))


class CrashModel(Model):
    def step(self, features):
        os._exit(19)
