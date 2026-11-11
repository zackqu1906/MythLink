import asyncio
import hashlib
import struct
import unittest

from mythring.audio import AudioClient, MicBlockAssembler, decode_adpcm, mic_start_command, MIC_STOP
from mythring.errors import NotConnectedError, ProtocolError, RequestTimeoutError, UnsupportedFeatureError


def block(samples=4, predictor=0, index=0, payload=b"\x11\x01"):
    return struct.pack("<hBBH", predictor, index, 0, samples) + payload


def packet(payload=None, seq=1, fragment=0, count=1, uptime=123):
    return b"\x20\x03" + struct.pack("<HHHI", seq, fragment, count, uptime) + (block() if payload is None else payload)


class AudioProtocolTests(unittest.TestCase):
    def test_public_gain_command(self):
        self.assertEqual(mic_start_command(), bytes.fromhex("20 00 01 80 80"))
        self.assertEqual(mic_start_command(20, 12), bytes.fromhex("20 00 01 28 18"))
        self.assertEqual(mic_start_command(-20, -24), bytes.fromhex("20 00 01 d8 d0"))
        self.assertEqual(mic_start_command(.5, -.5), bytes.fromhex("20 00 01 01 ff"))
        for hardware, software in ((20.5, 0), (-20.5, 0), (0, 24.5), (.25, 0), (0, float("nan"))):
            with self.assertRaises(ValueError):
                mic_start_command(hardware, software)

    def test_independent_decoder_golden_vectors(self):
        # Previously produced by the independent Ringo decoder; source:
        # ProximicVoice/tests/fixtures/adpcm_vectors.json (not this implementation).
        vectors = (
            ("6a2000000100", "36e8cdcd7f90da2c26d814ae0b2f96bd2fba60458821bf7871f19dee5481d3c8"),
            ("639000001400b037bc213aba7ed6a75b", "77168de9474fa8f0080aba1513af9c343e8e65e9936a343cd96ff0fefbbbd7c0"),
            ("e7890a00140024b3a0d45055dcf6f869", "f73b916343972c0bd0b17b7fb51dca9009b4ef1aca40bb3a0940b4ef13442e0f"),
            ("1b4c5800140043cd1fc0d6521e8724ab", "6f1200201f3313e5f556c2eba8f45e0c68de866da0c8d6cdc6a5423105278236"),
        )
        for encoded, expected in vectors:
            self.assertEqual(hashlib.sha256(decode_adpcm(bytes.fromhex(encoded))).hexdigest(), expected)

    def test_decoder_nibble_order_and_saturation(self):
        self.assertEqual(struct.unpack("<4h", decode_adpcm(block())), (0, 1, 2, 3))
        self.assertEqual(struct.unpack("<4h", decode_adpcm(block(payload=b"\x19\x09"))), (0, -1, 0, -1))
        self.assertEqual(struct.unpack("<2h", decode_adpcm(block(2, 32760, 88, b"\x07"))), (32760, 32767))
        self.assertEqual(struct.unpack("<2h", decode_adpcm(block(2, -32760, 88, b"\x0f"))), (-32760, -32768))
        self.assertEqual(len(decode_adpcm(block(1600, payload=bytes(800)))), 3200)

    def test_malformed_audio_is_not_pcm(self):
        for value in (b"", block(0, payload=b""), block(1601, payload=bytes(800)),
                      block(index=89), block()[:-1], block() + b"\x00", b"\x00\x00\x00\x01\x01\x00"):
            self.assertIsNone(decode_adpcm(value))

    def test_out_of_order_fragment_duplicate_and_frame_fields(self):
        assembler = MicBlockAssembler()
        encoded = block()
        self.assertIsNone(assembler.accept(packet(encoded[4:], fragment=1, count=2), 0))
        self.assertIsNone(assembler.accept(packet(encoded[4:], fragment=1, count=2), 1))
        frame = assembler.accept(packet(encoded[:4], count=2), 2)
        self.assertEqual((frame.sequence, frame.uptime_ms, frame.sample_rate, frame.sample_count), (1, 123, 16000, 4))
        self.assertEqual(frame.samples, (0, 1, 2, 3))
        self.assertIsNone(assembler.accept(packet(), 3))
        self.assertEqual(assembler.parts, [])

    def test_conflicting_metadata_or_payload_retires_whole_block(self):
        for second in (packet(b"b", count=2), packet(b"b", fragment=1, count=3),
                       packet(b"b", fragment=1, count=2, uptime=124)):
            assembler = MicBlockAssembler()
            assembler.accept(packet(b"a", count=2), 0)
            self.assertIsNone(assembler.accept(second, 1))
            self.assertTrue(assembler.retired)
            self.assertEqual(assembler.parts, [])
            self.assertIsNone(assembler.accept(packet(), 2))

    def test_missing_block_timeout_wrap_and_old_fragment(self):
        assembler = MicBlockAssembler()
        encoded = block()
        assembler.accept(packet(encoded[:4], seq=65534, count=2), 0)
        frame = assembler.accept(packet(seq=0), 1)
        self.assertEqual(frame.samples, (0, 1, 2, 3))
        self.assertIsNone(assembler.accept(packet(encoded[4:], seq=65534, fragment=1, count=2), 2))
        self.assertIsNone(assembler.accept(packet(seq=32768), 3))
        assembler.accept(packet(encoded[:4], seq=1, count=2), 4)
        self.assertIsNone(assembler.accept(packet(encoded[4:], seq=1, fragment=1, count=2), 505))
        self.assertEqual(assembler.parts, [])
        assembler.reset()
        self.assertIsNotNone(assembler.accept(packet(seq=0), 506))

    def test_assembly_memory_is_bounded(self):
        assembler = MicBlockAssembler()
        for value in (packet(b"a", count=807), packet(bytes(807)), b"\x20\x03", packet(b"", count=1)):
            self.assertIsNone(assembler.accept(value, 0))
        assembler.accept(packet(bytes(500), count=2), 1)
        self.assertIsNone(assembler.accept(packet(bytes(400), fragment=1, count=2), 2))
        self.assertTrue(assembler.retired)
        self.assertEqual(assembler.parts, [])


class AudioClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.writes, self.frames = [], []
        self.started = asyncio.Event()

        async def send(command):
            self.writes.append(command)
            if command[:2] == b"\x20\x00":
                self.started.set()

        self.audio = AudioClient(send, on_frame=self.frames.append, timeout=.05)
        self.audio.connected(supported=True, profile="legacy_v1")

    async def test_explicit_start_requires_first_complete_frame(self):
        self.assertTrue(self.audio.accept(packet()))
        self.assertEqual(self.frames, [])
        task = asyncio.create_task(self.audio.start())
        await self.started.wait()
        self.assertFalse(task.done())
        self.audio.accept(packet())
        frame = await task
        self.assertIs(self.frames[0], frame)
        self.assertTrue(self.audio.active)
        await self.audio.stop()
        self.assertFalse(self.audio.active)
        self.audio.accept(packet(seq=2))
        self.assertEqual(len(self.frames), 1)
        self.assertEqual(self.writes, [mic_start_command(), MIC_STOP])

    async def test_unsupported_and_prepare_reject_before_writes(self):
        self.audio.connected(supported=False, profile="ringo_touchpad_v2")
        with self.assertRaises(UnsupportedFeatureError):
            await self.audio.start()
        self.assertEqual(self.writes, [])
        self.audio.connected(supported=True)

        async def busy():
            raise ProtocolError("Touchpad owns sensors")

        self.audio._prepare = busy
        with self.assertRaises(ProtocolError):
            await self.audio.start()
        self.assertEqual(self.writes, [])

    async def test_duplicate_start_and_stop_while_waiting(self):
        task = asyncio.create_task(self.audio.start())
        await self.started.wait()
        with self.assertRaises(ProtocolError):
            await self.audio.start()
        await self.audio.stop()
        with self.assertRaises(ProtocolError):
            await task
        self.assertEqual(self.writes, [mic_start_command(), MIC_STOP])
        self.assertFalse(self.audio.active)

    async def test_timeout_does_not_report_recording_success(self):
        with self.assertRaises(RequestTimeoutError):
            await self.audio.start()
        self.assertFalse(self.audio.active)
        self.assertEqual(self.writes[-1], MIC_STOP)

    async def test_cancellation_sends_stop(self):
        task = asyncio.create_task(self.audio.start())
        await self.started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.audio.active)
        self.assertEqual(self.writes[-1], MIC_STOP)

    async def test_failed_stop_keeps_ownership_without_emitting_more_audio(self):
        task = asyncio.create_task(self.audio.start())
        await self.started.wait()
        self.audio.accept(packet())
        await task
        original_send = self.audio._send

        async def rejected_stop(command):
            if command == MIC_STOP:
                raise ConnectionError("ATT write rejected while GATT stays connected")
            await original_send(command)

        self.audio._send = rejected_stop
        with self.assertRaises(ConnectionError):
            await self.audio.stop()
        self.assertTrue(self.audio.active)
        self.audio.accept(packet(seq=2))
        self.assertEqual(len(self.frames), 1)
        with self.assertRaises(ProtocolError):
            await self.audio.start()
        self.audio._send = original_send
        await self.audio.stop()
        self.assertFalse(self.audio.active)

    async def test_start_cleanup_failure_keeps_ownership_until_disconnect(self):
        original_send = self.audio._send

        async def rejected_stop(command):
            if command == MIC_STOP:
                raise ConnectionError("STOP not submitted")
            await original_send(command)

        self.audio._send = rejected_stop
        with self.assertLogs("mythring.audio", "WARNING"):
            with self.assertRaises(RequestTimeoutError):
                await self.audio.start()
        self.assertTrue(self.audio.active)
        self.audio.accept(packet())
        self.assertEqual(self.frames, [])
        with self.assertRaises(ProtocolError):
            await self.audio.start()
        self.audio.disconnected()
        self.assertFalse(self.audio.active)

    async def test_failed_stop_during_start_does_not_retry_or_release_ownership(self):
        task = asyncio.create_task(self.audio.start())
        await self.started.wait()
        stop_attempts = []

        async def rejected_stop(command):
            stop_attempts.append(command)
            await asyncio.sleep(0)
            raise ConnectionError("STOP not submitted")

        self.audio._send = rejected_stop
        with self.assertRaises(ConnectionError):
            await self.audio.stop()
        with self.assertRaises(ProtocolError):
            await task
        self.assertEqual(stop_attempts, [MIC_STOP])
        self.assertTrue(self.audio.active)
        self.audio.accept(packet())
        self.assertEqual(self.frames, [])

    async def test_disconnect_during_start_never_stops_a_new_connection(self):
        task = asyncio.create_task(self.audio.start())
        await self.started.wait()
        self.audio.disconnected()
        self.audio.connected(supported=True)
        with self.assertRaises(NotConnectedError):
            await task
        self.assertFalse(self.audio.active)
        self.assertEqual(self.writes, [mic_start_command()])

    async def test_no_reuse_of_partial_audio_after_disconnect(self):
        task = asyncio.create_task(self.audio.start())
        await self.started.wait()
        self.audio.accept(packet(block()[:4], count=2))
        self.audio.disconnected()
        with self.assertRaises(NotConnectedError):
            await task
        self.audio.connected(supported=True)
        self.started.clear()
        task = asyncio.create_task(self.audio.start())
        await self.started.wait()
        self.audio.accept(packet(block()[4:], fragment=1, count=2))
        self.assertFalse(task.done())
        self.audio.accept(packet(seq=2))
        self.assertEqual((await task).sequence, 2)
        await self.audio.stop()


if __name__ == "__main__":
    unittest.main()
