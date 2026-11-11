import math
import struct
import unittest

from mythring.models import Battery, DeviceInfo, GestureEvent, InfoComponent, RingProduct
from mythring.protocol import is_supported_ble_name, parse_device_info, parse_packet


def gesture_packet(class_id=1, probabilities=None, mass=0.0):
    return (
        b"\x26\x06" + struct.pack("<HB12f", 0xFEFF, class_id, *(probabilities or [0.0] * 12))
        + b"\x00" * 4 + struct.pack("<If", 0xFE123456, mass)
    )


def info_packet(capabilities=(), version=None):
    packet = b"\x2a\x02\x01\x02\x02\x00\x29\x00" + bytes((len(capabilities),))
    packet += b"".join(bytes(item) for item in capabilities)
    return packet if version is None else packet + bytes((len(version),)) + version


DF0A_INFO = bytes.fromhex(
    "2a02010101027a00080101010103020000000003010101000401010103"
    "05010101030601010200070101010008010102000e312e322e3132322e7075626c6963"
)


class PacketParserTests(unittest.TestCase):
    def test_battery_range_and_source_length_policy(self):
        for percent in (0, 50, 100):
            self.assertEqual(parse_packet(b"\x29\x02\x00\x00" + bytes((percent,))), [Battery(percent)])
        self.assertEqual(parse_packet(b"\x29\x02\x00\x00\x65"), [])
        self.assertEqual(parse_packet(b"\x29\x02\x00\x00"), [])
        self.assertEqual(parse_packet(b"\x29\x02\x00\x00\x32\xff"), [Battery(50)])

    def test_final_firmware_gesture_ids_and_unsigned_fields(self):
        for class_id in (1, 2, 3, 4, 6, 7, 8, 9, 12, 13, 0x88, 0x89):
            self.assertEqual(parse_packet(gesture_packet(class_id)), [GestureEvent(0xFEFF, class_id, 0xFE123456)])
        for class_id in (0, 5, 10, 11, 0xFF):
            self.assertEqual(parse_packet(gesture_packet(class_id)), [])

    def test_legacy_probabilities(self):
        probabilities = [0.0] * 12
        probabilities[2] = 0.75
        probabilities[0] = 0.25
        result = parse_packet(gesture_packet(2, probabilities, mass=2.0))
        self.assertEqual(result[0].confidence, 0.75)
        probabilities[2] = 0.7
        self.assertEqual(parse_packet(gesture_packet(2, probabilities)), [])
        self.assertEqual(parse_packet(gesture_packet(0, [1.0] + [0.0] * 11)), [])

    def test_legacy_int8_trigger_preserves_unsigned_ids_and_time_without_probability(self):
        # Independent 16-byte fixture: seq=65534, tap, signed logits, uptime=0xFEDCBA98.
        packet = bytes.fromhex("2603feff0580ff00017f403f98badcfe")
        self.assertEqual(parse_packet(packet), [GestureEvent(65534, 5, 0xFEDCBA98, None)])
        for class_id in (1, 2, 3, 4, 5, 6):
            event = parse_packet(packet[:4] + bytes((class_id,)) + packet[5:])[0]
            self.assertEqual(event.class_id, class_id)
            self.assertIsNone(event.confidence)

    def test_legacy_trigger_rejects_unknown_classes_length_and_inference_packets(self):
        packet = bytes.fromhex("2603feff0580ff00017f403f98badcfe")
        for class_id in (0, 7, 12, 13, 0x88, 255):
            self.assertEqual(parse_packet(packet[:4] + bytes((class_id,)) + packet[5:]), [])
        for invalid in (packet[:5], packet[:-1], packet + b"\x00", b"\x26\x02" + packet[2:]):
            self.assertEqual(parse_packet(invalid), [])

    def test_gesture_rejects_nonfinite_out_of_range_and_invalid_mass(self):
        for value in (math.nan, math.inf, -0.1, 1.1):
            self.assertEqual(parse_packet(gesture_packet(1, [value] + [0.0] * 11)), [])
        for mass in (-1.0, math.nan, math.inf, 0.1):
            self.assertEqual(parse_packet(gesture_packet(mass=mass)), [])
        self.assertEqual(parse_packet(gesture_packet()[:-1]), [])
        self.assertEqual(parse_packet(gesture_packet() + b"\x00"), [])

    def test_token_batch_float16_metadata(self):
        values = (1.0, -2.0, 0.0, 0.5, 65504.0, 2 ** -24)
        packet = b"\x21\x05" + struct.pack("<HBI", 65535, 2, 0xFFFFFFFF) + b"\x01"
        packet += struct.pack("<12e", *values, *reversed(values))
        events = parse_packet(packet)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].values, values)
        self.assertEqual(events[1].values, tuple(reversed(values)))
        self.assertEqual((events[1].sequence, events[1].frame_index, events[1].frame_count, events[1].packet_uptime_ms), (65535, 1, 2, 0xFFFFFFFF))

    def test_token_frame_count_version_and_length(self):
        for count in (0, 21):
            packet = b"\x21\x05\x00\x00" + bytes((count,)) + b"\x00" * 4 + b"\x01" + b"\x00" * (12 * count)
            self.assertEqual(parse_packet(packet), [])
        packet = b"\x21\x05\x00\x00\x01" + b"\x00" * 4 + b"\x01" + b"\x00" * 12
        self.assertEqual(len(parse_packet(packet)), 1)
        for invalid in (packet[:-1], packet + b"\x00", packet[:9] + b"\x02" + packet[10:]):
            self.assertEqual(parse_packet(invalid), [])

    def test_unknown_and_short_packets(self):
        for packet in (b"", b"\x21", b"\x21\x05", b"\x24\x02\x01", b"\x3c\x02"):
            self.assertEqual(parse_packet(packet), [])


class DeviceInfoTests(unittest.TestCase):
    def test_fixed_and_text_versions_and_capability(self):
        self.assertEqual(parse_device_info(info_packet()), DeviceInfo("2.0.41.0", firmware_numeric_version=(2, 0, 41, 0)))
        packet = info_packet(((1, 1, 0, 0, 0), (8, 1, 0, 0, 0)), b"2.0.41.debug")
        self.assertEqual(parse_device_info(packet), DeviceInfo(
            "2.0.41.debug", swipe_available=True, firmware_numeric_version=(2, 0, 41, 0),
            components=(InfoComponent(1, 1, 0, 0, 0), InfoComponent(8, 1, 0, 0, 0)),
        ))
        self.assertFalse(parse_device_info(info_packet(((8, 0, 0, 0, 0),))).swipe_available)

    def test_df0a_real_info_preserves_hardware_numeric_version_and_all_components(self):
        info = parse_device_info(DF0A_INFO)
        self.assertEqual(info, DeviceInfo(
            "1.2.122.public", 1, True, 1, (1, 2, 122, 0),
            (
                InfoComponent(1, 1, 1, 1, 3), InfoComponent(2, 0, 0, 0, 0),
                InfoComponent(3, 1, 1, 1, 0), InfoComponent(4, 1, 1, 1, 3),
                InfoComponent(5, 1, 1, 1, 3), InfoComponent(6, 1, 1, 2, 0),
                InfoComponent(7, 1, 1, 1, 0), InfoComponent(8, 1, 1, 2, 0),
            ),
        ))
        self.assertEqual(parse_device_info(DF0A_INFO[:49]).firmware_version, "1.2.122.0")
        # A component model or flags value is data, not an INFO-format version.
        unknown = info_packet(((255, 255, 255, 255, 255),))
        self.assertEqual(parse_device_info(unknown).components, (InfoComponent(255, 255, 255, 255, 255),))

    def test_existing_device_info_positional_constructor_remains_valid(self):
        info = DeviceInfo("2.0.41.0", 2, True)
        self.assertEqual((info.firmware_version, info.hardware_revision, info.swipe_available), ("2.0.41.0", 2, True))
        self.assertEqual((info.format_version, info.firmware_numeric_version, info.components), (1, (0, 0, 0, 0), ()))

    def test_reject_invalid_identity_capabilities_and_version(self):
        packet = info_packet()
        bad_packets = [
            packet[:3] + b"\x00" + packet[4:], packet[:3] + b"\x03" + packet[4:],
            packet[:2] + b"\x02" + packet[3:], packet[:-1],
            info_packet(((8, 1, 0, 0, 0), (8, 0, 0, 0, 0))),
            info_packet(((1, 0, 0, 0, 0),))[:-1],
            info_packet(version=b""), info_packet(version=b"a" * 32),
            info_packet(version=b"a\x00b"), info_packet(version="版本".encode()),
            info_packet(version=b"v1") + b"x", info_packet(version=b"v1")[:-1],
        ]
        for packet in bad_packets:
            self.assertIsNone(parse_device_info(packet), repr(packet))

    def test_hardware_one_has_same_strict_lengths_and_duplicate_id_validation(self):
        duplicate = bytearray(DF0A_INFO)
        duplicate[44] = duplicate[9]
        for packet in (bytes(duplicate), DF0A_INFO[:-1], DF0A_INFO + b"\x00", DF0A_INFO[:48]):
            self.assertIsNone(parse_device_info(packet))

    def test_advertising_names(self):
        for name in ("R1AB09", "r1ab09", "RingoDF0A", "ringodf0a", "小王的Ringo", "A" * 21 + "的Ringo"):
            self.assertTrue(is_supported_ble_name(name), name)
            self.assertEqual(RingProduct.from_ble_name(name), RingProduct.R1)
        for name in (None, "", "R1", "R1ZZZZ", " R1AB09", "Ringo", "RingoDF0", "RingoDF0AZ", "RingoZZZZ", " RingoDF0A", "的Ringo", "A" * 22 + "的Ringo"):
            self.assertFalse(is_supported_ble_name(name), name)
            with self.assertRaises(ValueError):
                RingProduct.from_ble_name(name)


if __name__ == "__main__":
    unittest.main()
