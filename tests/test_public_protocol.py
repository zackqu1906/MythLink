import math
import hashlib
import json
from pathlib import Path
import random
import struct
import unittest

from ring_python_sdk.public_protocol import *


def block(samples=4, predictor=0, index=0, payload=b"\x11\x01"):
    return struct.pack("<hBBH", predictor, index, 0, samples) + payload


def packet(payload, seq=1, fragment=0, count=1, uptime=123):
    return b"\x20\x03" + struct.pack("<HHHI", seq, fragment, count, uptime) + payload


class ProtocolTests(unittest.TestCase):
    def test_device_info_real_8f56_fixture(self):
        packet = bytes.fromhex("2a02010101021c000801010101030201010103030101010004010101030501010103060101020007010101000800000000")
        info = parse_device_info(packet)
        self.assertEqual(info.firmware_version, "1.2.28")
        self.assertEqual(info.hardware_revision, 1)
        self.assertEqual(len(info.components), 8)

    def test_device_info_version_suffix_and_validation(self):
        header = bytes.fromhex("2a0201010102850000")
        suffix = b"1.2.133-public"
        self.assertEqual(parse_device_info(header + bytes([len(suffix)]) + suffix).firmware_version, suffix.decode())
        for packet in (header[:-1], header + b"\x00", header + b"\x02x", header + b"\x01\x00"):
            self.assertIsNone(parse_device_info(packet))

    def test_existing_sdk_adpcm_parity_vectors(self):
        # Golden hashes produced with the independently implemented decoder in
        # the user's existing ring-python-sdk-main/audio/adpcm.py, not this port.
        rows = json.loads((Path(__file__).parent / "fixtures/adpcm_vectors.json").read_text())
        for row in rows:
            pcm = decode_adpcm(bytes.fromhex(row["block_hex"]))
            self.assertEqual(len(pcm), row["samples"] * 2)
            self.assertEqual(hashlib.sha256(pcm).hexdigest(), row["pcm_sha256"])

    def test_compact_all_classes(self):
        for cid in GESTURE_IDS[1:]:
            e = parse_gesture(b"\x26\x07" + struct.pack("<HBIf", 65535, cid, 0xffffffff, .75))
            self.assertEqual((e.sequence, e.class_id, e.center_uptime_ms, e.confidence),
                             (65535, cid, 0xffffffff, .75))
            self.assertEqual(e.name, GESTURE_NAMES[cid])

    def test_compact_unknown_forward_compatible(self):
        e = parse_gesture(b"\x26\x07" + struct.pack("<HBIf", 1, 222, 10, 1))
        self.assertEqual(e.name, "unknown_222")

    def test_compact_rejects_bad_confidence_and_length(self):
        for value in (float("nan"), float("inf"), -.1, 1.01):
            self.assertIsNone(parse_gesture(b"\x26\x07" + struct.pack("<HBIf", 1, 1, 1, value)))
        good = b"\x26\x07" + struct.pack("<HBIf", 1, 1, 1, 1)
        self.assertIsNone(parse_gesture(good[:-1]))
        self.assertIsNone(parse_gesture(good + b"\x00"))

    def test_trigger_v2_all_classes(self):
        for cid in GESTURE_IDS[1:]:
            probs = [0.] * 12
            probs[GESTURE_IDS.index(cid)] = 1.
            p = b"\x26\x06" + struct.pack("<HB12fIIf", 9, cid, *probs, 100, 95, .5)
            self.assertEqual(parse_gesture(p), GestureEvent(9, cid, 95, 1.))
            self.assertIsNone(parse_gesture(b"\x26\x05" + p[2:57]))

    def test_trigger_v2_rejects_invalid(self):
        for cid, probs, mass in ((0, [1.] + [0.] * 11, 1), (11, [1.] + [0.] * 11, 1),
                (1, [0.] * 12, 1), (1, [float("nan")] * 12, 1),
                (1, [0., 1.] + [0.] * 10, -1), (1, [0., 1.] + [0.] * 10, float("inf"))):
            p = b"\x26\x06" + struct.pack("<HB12fIIf", 1, cid, *probs, 1, 1, mass)
            self.assertIsNone(parse_gesture(p))

    def test_mic_commands(self):
        self.assertEqual(mic_start_command(), bytes.fromhex("20 00 01 80 80"))
        self.assertEqual(mic_start_command(20, 12), bytes.fromhex("20 00 01 28 18"))
        self.assertEqual(mic_start_command(-20, -24), bytes.fromhex("20 00 01 d8 d0"))
        self.assertEqual(mic_start_command(.5, -.5), bytes.fromhex("20 00 01 01 ff"))

    def test_gain_validation(self):
        for hw, sw in ((20.5, 0), (-20.5, 0), (0, 24.5), (0, -24.5),
                       (.25, 0), (0, float("nan")), (float("inf"), 0)):
            with self.assertRaises(ValueError):
                mic_start_command(hw, sw)

    def test_adpcm_known_nibble_order(self):
        pcm = decode_adpcm(block())
        self.assertEqual(struct.unpack("<4h", pcm), (0, 1, 2, 3))
        self.assertEqual(struct.unpack("<4h", decode_adpcm(block(payload=b"\x19\x09"))), (0, -1, 0, -1))

    def test_adpcm_single_odd_max(self):
        self.assertEqual(decode_adpcm(block(1, -123, payload=b"")), struct.pack("<h", -123))
        self.assertEqual(len(decode_adpcm(block(3, payload=b"\x11"))), 6)
        self.assertEqual(len(decode_adpcm(block(1600, payload=bytes(800)))), 3200)

    def test_adpcm_saturation(self):
        self.assertEqual(struct.unpack("<2h", decode_adpcm(block(2, 32760, 88, b"\x07"))), (32760, 32767))
        self.assertEqual(struct.unpack("<2h", decode_adpcm(block(2, -32760, 88, b"\x0f"))), (-32760, -32768))

    def test_adpcm_invalid_headers(self):
        for p in (b"", block(0, payload=b""), block(1601, payload=bytes(800)),
                  block(index=89), block()[:-1], block() + b"\x00", b"\x00\x00\x00\x01\x01\x00"):
            self.assertIsNone(decode_adpcm(p))

    def test_assembly_out_of_order_duplicate(self):
        a = MicBlockAssembler()
        b = block()
        self.assertIsNone(a.accept(packet(b[4:], fragment=1, count=2), 0))
        self.assertIsNone(a.accept(packet(b[4:], fragment=1, count=2), 1))
        f = a.accept(packet(b[:4], fragment=0, count=2), 2)
        self.assertEqual(f.samples, (0, 1, 2, 3))
        self.assertIsNone(a.accept(packet(b), 3))

    def test_assembly_conflict_retires(self):
        a = MicBlockAssembler()
        a.accept(packet(b"abc", fragment=0, count=2), 0)
        a.accept(packet(b"def", fragment=0, count=2), 1)
        self.assertTrue(a.retired)
        self.assertIsNone(a.accept(packet(block()), 2))

    def test_assembly_timeout_and_reset(self):
        a = MicBlockAssembler()
        a.accept(packet(block()[:4], fragment=0, count=2), 0)
        self.assertIsNone(a.accept(packet(block()[4:], fragment=1, count=2), 501))
        a.reset()
        self.assertIsNotNone(a.accept(packet(block()), 502))

    def test_assembly_changed_metadata(self):
        for kwargs in ({"count": 3}, {"uptime": 124}):
            a = MicBlockAssembler()
            a.accept(packet(b"a", count=2), 0)
            self.assertIsNone(a.accept(packet(b"b", fragment=1, count=kwargs.get("count", 2),
                                             uptime=kwargs.get("uptime", 123)), 1))
            self.assertTrue(a.retired)

    def test_assembly_new_sequence_and_wrap(self):
        a = MicBlockAssembler()
        a.accept(packet(b"a", seq=65535, count=2), 0)
        self.assertIsNotNone(a.accept(packet(block(), seq=0), 1))
        self.assertIsNone(a.accept(packet(block(), seq=65535), 2))
        self.assertIsNone(a.accept(packet(block(), seq=32768), 3))

    def test_assembly_bound(self):
        a = MicBlockAssembler()
        self.assertIsNone(a.accept(packet(b"a", count=807), 0))
        self.assertIsNone(a.accept(packet(bytes(807)), 0))
        a.accept(packet(bytes(500), count=2), 0)
        self.assertIsNone(a.accept(packet(bytes(400), fragment=1, count=2), 1))
        self.assertTrue(a.retired)

    def test_button_and_battery(self):
        for code, name in enumerate(BUTTON_NAMES):
            e = parse_button(b"\x27\x02" + struct.pack("<HBI", 6, code, 123))
            self.assertEqual(e.name, name)
        self.assertIsNone(parse_button(b"\x27\x02" + struct.pack("<HBI", 6, 5, 123)))
        self.assertEqual(parse_battery(b"\x29\x02\x00\x00\x64").percent, 100)
        self.assertIsNone(parse_battery(b"\x29\x02\x00\x00\x65"))

    def test_quaternion_command_and_wrap(self):
        self.assertEqual(quaternion_start_command(), bytes.fromhex("21 00 c8 00 c8 00 d0 07 10 0a 03"))
        p = b"\x21\x0a" + struct.pack("<HBIB8f", 2, 2, 2, 1, 1, 0, 0, 0, 1, 0, 0, 0)
        frames = parse_quaternions(p)
        self.assertEqual([f.uptime_ms for f in frames], [0xfffffffd, 2])
        self.assertEqual(len(parse_quaternions(p[:-1])), 0)
        for rate, count in ((201, 1), (200, 0), (200, 21)):
            with self.assertRaises(ValueError):
                quaternion_start_command(rate, count)

    def test_quaternion_invalid_whole_packet(self):
        for q in ((0, 0, 0, 0), (float("nan"), 0, 0, 0), (2, 0, 0, 0)):
            p = b"\x21\x0a" + struct.pack("<HBIB8f", 2, 2, 2, 1, 1, 0, 0, 0, *q)
            self.assertEqual(parse_quaternions(p), [])

    def test_random_untrusted_packets_never_crash(self):
        rng = random.Random(20260924)
        a = MicBlockAssembler()
        for i in range(4000):
            p = rng.randbytes(rng.randrange(900))
            for prefix in (b"\x20\x03", b"\x26\x07", b"\x26\x06", b"\x27\x02", b"\x21\x0a", b"\x29\x02"):
                value = prefix + p
                parse_gesture(value)
                parse_button(value)
                parse_battery(value)
                parse_quaternions(value)
                a.accept(value, i)
            decode_adpcm(p)


if __name__ == "__main__":
    unittest.main()
