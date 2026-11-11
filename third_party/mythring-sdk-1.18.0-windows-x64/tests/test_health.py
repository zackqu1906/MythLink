import asyncio
import io
import math
import struct
import unittest
import zlib

from mythring.health import (
    HealthCapabilities, HealthClient, HealthCompactWindow, HealthError,
    HealthFeature, HealthLiveHeart, HealthMonitoringSettings, HealthRecording,
    HealthWindow, decode_compact, decode_delta, read_block, read_blocks, request_packet,
)


def raw_record(kind, payload, sequence=0, schema=4, count=1, period=0, flags=0, uptime=100):
    prefix = struct.pack("<HBBHHIIHH", 0x4852, schema, kind, len(payload), count, sequence, uptime, period, flags)
    return prefix + struct.pack("<I", zlib.crc32(payload, zlib.crc32(prefix))) + payload


def session():
    p = bytearray(48)
    p[:4] = b"\x07\x04\x10\x01"
    struct.pack_into("<QIHHHH", p, 4, 1700000000000, 100, 640, 80, 2048, 512)
    struct.pack_into("<H", p, 36, 18)
    struct.pack_into("<I", p, 44, 1800000)
    return raw_record(1, p)


def recording_bytes():
    return session() + raw_record(7, struct.pack("<B3xI", 1, 0), sequence=1)


def capabilities(flags=0):
    flags |= (1 << 10) | (1 << 9) | 128
    return struct.pack("<IBHHIHHHH8x", flags, 7, 1, 12, 1800000, 180, 640, 80, 18)


def reply(request, payload=b"", error=0):
    return bytes((0x3A, request[1] | 0x80, 1, 0)) + request[4:8] + struct.pack("<h", error) + payload


def live_payload(token, state=3, light=False, value=73.0, preview=math.nan):
    return struct.pack("<IBBHIIffIHH", token, state, int(light), 0, 12000, 0, value, preview, 23, 8, 0)


def compact_packet(payload, tx=7, flags=1, index=0, status=1, error=0):
    prefix = struct.pack("<BBBBIhBB", 0x3A, 0x93, 2, flags, tx, error, index, status) + payload
    return prefix + struct.pack("<I", zlib.crc32(prefix))


class HealthProtocolTests(unittest.TestCase):
    def test_wire_request_and_capabilities(self):
        self.assertEqual(request_packet(21, 9), b"\x3a\x15\x01\x00\x09\x00\x00\x00")
        caps = HealthCapabilities.decode(capabilities((1 << 28) | (1 << 18)))
        self.assertTrue(caps.automatic)
        self.assertTrue(caps.monitoring_settings_supported)
        self.assertTrue(caps.live_heart_supported)
        self.assertEqual(caps.accelerometer_period_ms, 640)

    def test_record_crc_sequence_and_end(self):
        data = recording_bytes()
        blocks = tuple(read_blocks(data, require_end=True))
        self.assertEqual([block.kind for block in blocks], [1, 7])
        self.assertEqual(sum(block.stored_bytes for block in blocks), len(data))
        bad = bytearray(data)
        bad[30] ^= 1
        with self.assertRaisesRegex(ValueError, "CRC"):
            tuple(read_blocks(bad))
        with self.assertRaisesRegex(ValueError, "Discontinuous"):
            tuple(read_blocks(session() + raw_record(7, b"\x01" + bytes(7), sequence=3)))
        with self.assertRaisesRegex(ValueError, "Missing END"):
            tuple(read_blocks(session(), require_end=True))
        with self.assertRaisesRegex(ValueError, "Truncated"):
            tuple(read_blocks(data[:-1]))
        with self.assertRaisesRegex(ValueError, "Discontinuous"):
            tuple(read_blocks(data + raw_record(4, bytes(8), sequence=2)))

    def test_raw_sample_shape_and_delta(self):
        first = struct.pack("<hhh", 10, -10, 32760)
        compressed = first + bytes((2, 1, 0))
        expected = first + struct.pack("<hhh", 11, -11, 32760)
        self.assertEqual(decode_delta(2, 2, compressed), expected)
        block = read_block(io.BytesIO(raw_record(2, compressed, schema=3, count=2, period=640, flags=0x8000)))
        self.assertEqual(block.payload, expected)
        ppg_first = struct.pack("<iiB", 100, -100, 2)
        self.assertEqual(decode_delta(3, 2, ppg_first + b"\x02\x01\x03"), ppg_first + struct.pack("<iiB", 101, -101, 3))
        with self.assertRaisesRegex(ValueError, "Noncanonical"):
            decode_delta(2, 2, first + b"\x80\x00\x00\x00")
        with self.assertRaisesRegex(ValueError, "out of range"):
            decode_delta(2, 2, struct.pack("<hhh", 32767, 0, 0) + b"\x02\x00\x00")
        with self.assertRaisesRegex(ValueError, "Trailing"):
            decode_delta(2, 2, first + b"\x00\x00\x00\x00")

    def test_monitoring_dependency_and_wire(self):
        settings = HealthMonitoringSettings()
        self.assertEqual(settings.encode(), b"\x01\x1f\x00\x00\x00")
        disabled_sleep = settings.with_feature(HealthFeature.SLEEP, False)
        self.assertFalse(disabled_sleep.features & HealthFeature.SPO2)
        with self.assertRaisesRegex(ValueError, "sleep"):
            disabled_sleep.with_feature(HealthFeature.SPO2, True)
        with self.assertRaisesRegex(ValueError, "sleep"):
            HealthMonitoringSettings(features=HealthFeature.SPO2).encode()
        self.assertEqual(HealthMonitoringSettings.decode(struct.pack("<II", 0x11F, 31)), settings)
        with self.assertRaisesRegex(ValueError, "version"):
            HealthMonitoringSettings.decode(struct.pack("<II", 0x21F, 31))

    def test_live_heart_final_preview_and_token(self):
        result = HealthLiveHeart.decode(live_payload(123), 123)
        self.assertEqual(result.value, 73)
        self.assertIsNone(result.preview)
        pending = HealthLiveHeart.decode(live_payload(123, 2, True, math.nan, 74), 123)
        self.assertIsNone(pending.value)
        self.assertEqual(pending.preview, 74)
        with self.assertRaises(ValueError):
            HealthLiveHeart.decode(live_payload(123), 456)
        with self.assertRaisesRegex(ValueError, "final"):
            HealthLiveHeart.decode(live_payload(123, 2, True, 74), 123)
        with self.assertRaisesRegex(ValueError, "preview"):
            HealthLiveHeart.decode(live_payload(123, preview=73), 123)

    def test_credit_window_validates_offsets_and_terminal(self):
        window = HealthWindow(5, 0, 5, 3, 8)
        self.assertIsNone(window.consume(struct.pack("<IIIBBB", 5, 0, 5, 1, 0, 0) + b"abc"))
        self.assertEqual(window.consume(struct.pack("<IIIBBB", 5, 3, 5, 1, 1, 1) + b"de"),
                         struct.pack("<IIIB", 5, 0, 5, 1) + b"abcde")
        with self.assertRaises(ValueError):
            window.consume(struct.pack("<IIIBBB", 5, 3, 5, 1, 1, 1) + b"de")
        with self.assertRaises(ValueError):
            HealthWindow(5, 0, 5, 3).consume(struct.pack("<IIIBBB", 5, 1, 5, 1, 0, 0) + b"abc")

    def test_compact_decode_and_split_fragments(self):
        payload = b"\x01" + bytes(7)
        body = bytes((7, 15, 8, 1, 0, 0, 0)) + payload
        encoded = struct.pack("<IBBHHHII", 0x32304348, 1, 4, 1, 32, len(body), 1, 100) + body
        raw = raw_record(7, payload, sequence=1)
        self.assertEqual(decode_compact(encoded), raw)
        framed = struct.pack("<H", len(encoded) | 0x8000) + encoded
        window = HealthCompactWindow(5, 0, len(raw), 1, 100, 2)
        self.assertIsNone(window.consume(compact_packet(framed[:1], flags=0), 7))
        result = window.consume(compact_packet(framed[1:], index=1), 7)
        self.assertEqual(result, struct.pack("<IIIB", 5, 0, len(raw), 1) + raw)
        bad = bytearray(compact_packet(framed))
        bad[-1] ^= 1
        with self.assertRaisesRegex(ValueError, "CRC"):
            HealthCompactWindow(5, 0, len(raw), 1, 100, 2).consume(bad, 7)
        with self.assertRaises(HealthError) as caught:
            HealthCompactWindow(5, 0, len(raw), 1, 100, 2).consume(compact_packet(b"", error=-16), 7)
        self.assertEqual(caught.exception.device_code, -16)


class HealthClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_connection_negotiation_and_reconnection(self):
        async def send(packet):
            client.accept(reply(packet, capabilities() if packet[1] == 0 else bytes(30)))
        client = HealthClient(send)
        self.assertTrue(client.blocks_sensor_start)
        await client.connected()
        self.assertFalse(client.blocks_sensor_start)
        client.disconnected()
        self.assertTrue(client.blocks_sensor_start)
        with self.assertRaises(HealthError) as caught:
            await client.refresh_status()
        self.assertEqual(caught.exception.device_code, -107)
        client.connection_opened()
        self.assertFalse((await client.get_capabilities()).monitoring_settings_supported)
        await client.connected()
        self.assertFalse(client.blocks_sensor_start)

    async def test_queued_operation_does_not_cross_connection_epoch(self):
        commands = []
        async def send(packet):
            commands.append(packet)
            client.accept(reply(packet, capabilities(1 << 28) if packet[1] == 0 else struct.pack("<II", 0x11F, 31)))
        client = HealthClient(send)
        await client._operations.acquire()
        queued = asyncio.create_task(client.get_monitoring_settings())
        await asyncio.sleep(0)
        client.disconnected()
        client.connection_opened()
        client._operations.release()
        with self.assertRaises(HealthError) as caught:
            await queued
        self.assertEqual(caught.exception.device_code, -107)
        self.assertFalse(commands)
        self.assertEqual(await client.get_monitoring_settings(), HealthMonitoringSettings())

    async def test_unsupported_firmware_unblocks_sensor(self):
        async def send(packet):
            client.accept(reply(packet, error=-95))
        client = HealthClient(send)
        await client.connected()
        self.assertFalse(client.blocks_sensor_start)

    async def test_disconnect_fails_inflight_request(self):
        started = asyncio.Event()
        async def send(packet):
            started.set()
        client = HealthClient(send)
        task = asyncio.create_task(client.get_capabilities())
        await started.wait()
        client.disconnected()
        with self.assertRaises(HealthError) as caught:
            await task
        self.assertEqual(caught.exception.device_code, -107)
        self.assertFalse(client._pending)

    async def test_monitoring_read_write_and_ack_mismatch(self):
        wrong = False
        async def send(packet):
            if packet[1] == 0:
                payload = capabilities(1 << 28)
            elif packet[1] == 21:
                payload = struct.pack("<II", 0x11F, 31)
            else:
                enabled, mask = struct.unpack("<BI", packet[8:])
                payload = struct.pack("<II", (enabled << 8) | mask if not wrong else 0x11F, 31)
            client.accept(reply(packet, payload))
        client = HealthClient(send)
        self.assertEqual(await client.get_monitoring_settings(), HealthMonitoringSettings())
        disabled = HealthMonitoringSettings(enabled=False)
        self.assertEqual(await client.set_monitoring_settings(disabled), disabled)
        wrong = True
        with self.assertRaisesRegex(ValueError, "mismatch"):
            await client.set_monitoring_settings(disabled)

    async def test_download_validated_raw_without_ack_or_delete(self):
        data = recording_bytes()
        commands = []
        async def send(packet):
            commands.append(packet[1])
            if packet[1] == 0:
                payload = capabilities()
            else:
                record_id, offset, total, maximum = struct.unpack("<IIIH", packet[8:])
                self.assertEqual(record_id, 7)
                payload = struct.pack("<IIIB", 7, offset, len(data), 1) + data[offset:offset + min(40, maximum)]
            client.accept(reply(packet, payload))
        client = HealthClient(send)
        progress = []
        result = await client.download_recording(HealthRecording(7, 1, len(data), 1700000000000, 0, 7), lambda received, total: progress.append((received, total)))
        self.assertEqual(result.data, data)
        self.assertEqual(len(result.blocks), 2)
        self.assertEqual(progress[-1], (len(data), len(data)))
        self.assertEqual(set(commands), {0, 5})

    async def test_download_compact_window_uses_negotiated_mtu(self):
        data = recording_bytes()
        commands = []
        async def send(packet):
            commands.append(packet[1])
            if packet[1] == 0:
                client.accept(reply(packet, capabilities((1 << 11) | (1 << 25))))
            elif packet[1] == 5:
                client.accept(reply(packet, struct.pack("<IIIB", 7, 0, len(data), 1)))
            else:
                record_id, offset, total, cap, credits, status = struct.unpack("<IIIHBB", packet[8:])
                self.assertEqual((record_id, offset, total, cap, credits, status), (7, 0, len(data), 109, 8, 1))
                tx = struct.unpack_from("<I", packet, 4)[0]
                framed = struct.pack("<H", len(data)) + data
                client.accept(compact_packet(framed, tx=tx))
        client = HealthClient(send, mtu=lambda: 128)
        result = await client.download_recording(HealthRecording(7, 1, len(data), 1700000000000, 0, 7))
        self.assertEqual(result.data, data)
        self.assertEqual(commands, [0, 5, 19])

    async def test_download_rejects_corrupt_record_and_does_not_ack(self):
        data = bytearray(recording_bytes())
        data[-1] ^= 1
        commands = []
        async def send(packet):
            commands.append(packet[1])
            payload = capabilities() if packet[1] == 0 else struct.pack("<IIIB", 7, 0, len(data), 1) + data
            client.accept(reply(packet, payload))
        client = HealthClient(send)
        with self.assertRaisesRegex(ValueError, "CRC"):
            await client.download_recording(HealthRecording(7, 1, len(data), 1700000000000, 0, 7))
        self.assertEqual(commands, [0, 5])
        self.assertEqual(client._sync_callers, 0)

    async def test_live_heart_final_and_explicit_cancel(self):
        actions = []
        async def send(packet):
            if packet[1] == 0:
                payload = capabilities(1 << 18)
            else:
                action, token = struct.unpack("<BI", packet[8:])
                actions.append(action)
                payload = live_payload(token, 2, True, math.nan, 74) if action == 1 else live_payload(token, 5, False, math.nan)
            client.accept(reply(packet, payload))
        client = HealthClient(send)
        updates = []
        def update(value):
            updates.append(value)
            if value.state == 2:
                self.assertTrue(client.cancel_live_heart())
        final = await client.measure_live_heart(update)
        self.assertEqual(final.state, 5)
        self.assertFalse(final.light)
        self.assertEqual(actions, [1, 2])
        self.assertEqual([value.state for value in updates], [2, 5])
        self.assertFalse(client.cancel_live_heart())

    async def test_live_heart_during_busy_operation_sends_nothing(self):
        commands = []
        async def send(packet):
            commands.append(packet)
        client = HealthClient(send)
        async with client._operations:
            with self.assertRaises(HealthError) as caught:
                await client.measure_live_heart()
        self.assertEqual(caught.exception.device_code, -16)
        self.assertFalse(commands)

    async def test_cancelled_heart_task_cleans_up_before_unlock(self):
        started = asyncio.Event()
        actions = []
        async def send(packet):
            if packet[1] == 0:
                payload = capabilities(1 << 18)
            else:
                action, token = struct.unpack("<BI", packet[8:])
                actions.append(action)
                payload = live_payload(token, 2, True, math.nan) if action == 1 else live_payload(token, 5, False, math.nan)
            client.accept(reply(packet, payload))
        client = HealthClient(send)
        task = asyncio.create_task(client.measure_live_heart(lambda _: started.set()))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(actions, [1, 2])
        self.assertFalse(client._operations.locked())


if __name__ == "__main__":
    unittest.main()
