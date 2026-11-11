import asyncio
import struct
import unittest
from types import SimpleNamespace

import cbor2

from mythring.protocol import NOTIFY_CHARACTERISTIC_UUID, WRITE_CHARACTERISTIC_UUID
from mythring.smp import (SMP_CHARACTERISTIC_UUID, SmpCommandError, SmpError, SmpTransport,
                         check_response, decode_response, encode_request)


def response_frame(op=1, group=1, command=0, sequence=0, payload=None):
    data = cbor2.dumps({"images": []} if payload is None else payload)
    return struct.pack(">BBHHBB", op, 0, len(data), group, sequence, command) + data


class SmpCodecTests(unittest.TestCase):
    def test_big_endian_header_and_cbor(self):
        frame = encode_request(2, 0x1234, 5, 17, {"off": 256, "data": b"abc"})
        self.assertEqual(struct.unpack(">BBHHBB", frame[:8]), (2, 0, len(frame) - 8, 0x1234, 17, 5))
        self.assertEqual(cbor2.loads(frame[8:]), {"off": 256, "data": b"abc"})

    def test_response_strict_length_type_and_errors(self):
        self.assertEqual(decode_response(response_frame())[1:4], (1, 0, 0))
        invalid = [b"", response_frame()[:-1], response_frame() + b"x",
                   response_frame(payload=[1]), response_frame(op=0)]
        for frame in invalid:
            with self.subTest(frame=frame), self.assertRaises(SmpError):
                decode_response(frame)
        for payload in ({"rc": 8}, {"err": {"group": 1, "rc": 2}}):
            with self.assertRaises(SmpCommandError):
                check_response(payload)
        self.assertEqual(check_response({}), {})


class FakeBleak:
    def __init__(self, device, *, timeout, disconnected_callback, respond=True):
        self.is_connected = False
        self.disconnected_callback = disconnected_callback
        self.notifications = {}
        self.frames = []
        self.writes = []
        self.buffer = bytearray()
        self.respond = respond
        self.characters = {
            uuid: SimpleNamespace(uuid=uuid, properties=["write-without-response", "notify"], max_write_without_response_size=20)
            for uuid in (SMP_CHARACTERISTIC_UUID, WRITE_CHARACTERISTIC_UUID, NOTIFY_CHARACTERISTIC_UUID)
        }
        self.services = SimpleNamespace(get_characteristic=self.characters.get)

    async def connect(self):
        self.is_connected = True

    async def disconnect(self):
        self.is_connected = False
        self.disconnected_callback(self)

    async def start_notify(self, characteristic, callback):
        self.notifications[characteristic.uuid] = callback

    async def write_gatt_char(self, characteristic, packet, *, response):
        self.writes.append((characteristic.uuid, packet, response))
        if characteristic.uuid != SMP_CHARACTERISTIC_UUID:
            if packet == b"\x2a\x01":
                version = b"2.1.6.debug"
                reply = b"\x2a\x02\x01\x02\x00\x00\x00\x00\x00" + bytes((len(version),)) + version
            else:
                reply = b"\x2d\x02\x01\x01\x02\x03\x04\x05\x06"
            self.notifications[NOTIFY_CHARACTERISTIC_UUID](None, reply)
            return
        self.buffer.extend(packet)
        if len(self.buffer) < 8 or len(self.buffer) < 8 + struct.unpack_from(">H", self.buffer, 2)[0]:
            return
        op, _, length, group, sequence, command = struct.unpack_from(">BBHHBB", self.buffer)
        self.frames.append(bytes(self.buffer))
        self.buffer.clear()
        if not self.respond:
            return
        # An unrelated sequence must not satisfy the outstanding request.
        callback = self.notifications[SMP_CHARACTERISTIC_UUID]
        callback(None, response_frame(op + 1, group, command, (sequence + 1) & 255, {"wrong": True}))
        valid = response_frame(op + 1, group, command, sequence, {"ok": True})
        for offset in range(0, len(valid), 3):
            callback(None, valid[offset:offset + 3])


class SmpTransportTests(unittest.IsolatedAsyncioTestCase):
    def factory(self, *args, **kwargs):
        self.client = FakeBleak(*args, **kwargs, respond=getattr(self, "respond", True))
        return self.client

    async def test_fragmentation_reassembly_and_identity(self):
        async with SmpTransport("device", client_factory=self.factory) as transport:
            result = await transport.request(2, 1, 1, {"data": b"x" * 100})
            self.assertEqual(result, {"ok": True})
            self.assertGreater(len(self.client.writes), 5)
            self.assertTrue(all(len(packet) <= 20 and not acknowledged for _, packet, acknowledged in self.client.writes))
            info = await transport.identity("01:02:03:04:05:06")
            self.assertEqual(info.firmware_version, "2.1.6.debug")
        self.assertFalse(self.client.is_connected)

    async def test_timeout_closes_session_and_cannot_match_late_ack(self):
        self.respond = False
        transport = SmpTransport("device", client_factory=self.factory, timeout=0.01)
        await transport.connect()
        with self.assertRaises(asyncio.TimeoutError):
            await transport.request(0, 1, 0)
        self.assertFalse(self.client.is_connected)
        with self.assertRaises(ConnectionError):
            await transport.request(0, 1, 0)

    async def test_cancel_closes_transport(self):
        self.respond = False
        transport = SmpTransport("device", client_factory=self.factory)
        await transport.connect()
        task = asyncio.create_task(transport.request(0, 1, 0))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.client.is_connected)


if __name__ == "__main__":
    unittest.main()
