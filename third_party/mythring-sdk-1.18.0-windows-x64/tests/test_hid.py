import asyncio
import struct
import unittest

from mythring.hid import HidClient, HidError, HidGesture, HidKey, HidMapping, parse_mapping


def reply(gesture=HidGesture.LEFT, key=HidKey.ENTER, error=0):
    return struct.pack("<BBBHh", 0x30, 5, gesture, key, error)


class HidProtocolTests(unittest.TestCase):
    def test_valid_ids_and_signed_error(self):
        for gesture in HidGesture:
            for key in HidKey:
                self.assertEqual(parse_mapping(reply(gesture, key, -95)), HidMapping(gesture, key, -95))

    def test_rejects_unknown_fields_and_malformed_frames(self):
        for packet in (
            b"", b"\x30\x05", reply()[:-1], reply() + b"\x00",
            reply(gesture=0xFF), reply(key=0xFFFF), b"\x30\x04" + reply()[2:],
        ):
            self.assertIsNone(parse_mapping(packet), repr(packet))


class HidClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sent = asyncio.Queue()

        async def send(packet):
            await self.sent.put(packet)

        self.client = HidClient(send, timeout=0.1)
        self.client.connected(True)

    async def test_save_and_readback_are_one_serialized_operation(self):
        saving = asyncio.create_task(self.client.save(HidGesture.LEFT, HidKey.F9))
        self.assertEqual(await self.sent.get(), b"\x30\x03\x03\x94\x00")
        reading = asyncio.create_task(self.client.read(HidGesture.RIGHT))
        await asyncio.sleep(0)
        self.assertTrue(self.sent.empty())
        self.client.accept(reply(key=HidKey.F9))
        self.assertEqual(await self.sent.get(), b"\x30\x04\x03")
        self.assertFalse(reading.done())
        self.assertTrue(self.sent.empty())
        self.client.accept(reply(key=HidKey.F9))
        self.assertEqual((await saving).key, HidKey.F9)
        self.assertEqual(await self.sent.get(), b"\x30\x04\x04")
        self.client.accept(reply(HidGesture.RIGHT, HidKey.DISABLED))
        self.assertEqual((await reading).key, HidKey.DISABLED)

    async def test_readback_mismatch_does_not_quarantine(self):
        saving = asyncio.create_task(self.client.save(HidGesture.LEFT, HidKey.F9))
        await self.sent.get()
        self.client.accept(reply(key=HidKey.F9))
        await self.sent.get()
        self.client.accept(reply(key=HidKey.F3))
        with self.assertRaises(HidError) as caught:
            await saving
        self.assertEqual(caught.exception.reason, "readback_mismatch")
        reading = asyncio.create_task(self.client.read(HidGesture.LEFT))
        await self.sent.get()
        self.client.accept(reply(key=HidKey.F3))
        await reading

    async def test_hidden_hid_service_verifies_mapping_before_write_and_afterward(self):
        self.client.connected(True, probe_before_save=True)
        saving = asyncio.create_task(self.client.save(HidGesture.LEFT, HidKey.F9))
        self.assertEqual(await self.sent.get(), b"\x30\x04\x03")
        self.client.accept(reply(key=HidKey.F3))
        self.assertEqual(await self.sent.get(), b"\x30\x03\x03\x94\x00")
        self.client.accept(reply(key=HidKey.F9))
        self.assertEqual(await self.sent.get(), b"\x30\x04\x03")
        self.client.accept(reply(key=HidKey.F9))
        self.assertEqual((await saving).key, HidKey.F9)

    async def test_bad_or_wrong_gesture_reply_does_not_finish_request(self):
        task = asyncio.create_task(self.client.read(HidGesture.LEFT))
        await self.sent.get()
        self.assertFalse(self.client.accept(b"\x29\x02\x00\x00\x32"))
        self.assertTrue(self.client.accept(b"\x30\x05"))
        self.assertTrue(self.client.accept(reply(HidGesture.RIGHT)))
        self.assertTrue(self.client.accept(reply(key=0xFFFF)))
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        self.client.accept(reply())
        self.assertEqual(await task, HidMapping(HidGesture.LEFT, HidKey.ENTER))

    async def test_device_errors_preserve_reason_and_code(self):
        for code, reason in ((-95, "unsupported_item"), (-22, "unsupported_item"), (-16, "busy"), (-1, "device_error")):
            task = asyncio.create_task(self.client.read(HidGesture.LEFT))
            await self.sent.get()
            self.client.accept(reply(error=code))
            with self.assertRaises(HidError) as caught:
                await task
            self.assertEqual((caught.exception.reason, caught.exception.device_code), (reason, code))

    async def test_timeout_quarantines_late_replies_until_reconnected(self):
        task = asyncio.create_task(self.client.read(HidGesture.LEFT))
        await self.sent.get()
        with self.assertRaises(HidError) as caught:
            await task
        self.assertEqual(caught.exception.reason, "result_unknown")
        self.client.accept(reply())
        with self.assertRaises(HidError) as caught:
            await self.client.read(HidGesture.LEFT)
        self.assertEqual(caught.exception.reason, "result_unknown")
        self.assertTrue(self.sent.empty())
        self.client.connected(True)
        task = asyncio.create_task(self.client.read(HidGesture.LEFT))
        await self.sent.get()
        self.client.accept(reply())
        await task

    async def test_cancelled_sent_request_quarantines_connection(self):
        task = asyncio.create_task(self.client.read(HidGesture.LEFT))
        await self.sent.get()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        with self.assertRaises(HidError) as caught:
            await self.client.read(HidGesture.LEFT)
        self.assertEqual(caught.exception.reason, "result_unknown")

    async def test_disconnect_fails_inflight_and_prevents_stale_queued_request(self):
        first = asyncio.create_task(self.client.read(HidGesture.LEFT))
        await self.sent.get()
        queued = asyncio.create_task(self.client.read(HidGesture.RIGHT))
        await asyncio.sleep(0)
        self.client.disconnected()
        self.client.connected(True)
        for task in (first, queued):
            with self.assertRaises(HidError) as caught:
                await task
            self.assertEqual(caught.exception.reason, "disconnected")
        self.assertTrue(self.sent.empty())
        current = asyncio.create_task(self.client.read(HidGesture.LEFT))
        await self.sent.get()
        self.client.accept(reply())
        await current

    async def test_disconnected_and_unsupported_rejected_before_send(self):
        self.client.disconnected()
        with self.assertRaises(HidError) as caught:
            await self.client.read(HidGesture.LEFT)
        self.assertEqual(caught.exception.reason, "disconnected")
        self.client.connected(False)
        with self.assertRaises(HidError) as caught:
            await self.client.read(HidGesture.LEFT)
        self.assertEqual(caught.exception.reason, "unsupported")
        self.assertTrue(self.sent.empty())

    async def test_send_failure_quarantines_connection(self):
        async def fail(packet):
            raise OSError("link unavailable")

        self.client._send = fail
        for _ in range(2):
            with self.assertRaises(HidError) as caught:
                await self.client.read(HidGesture.LEFT)
            self.assertEqual(caught.exception.reason, "result_unknown")


if __name__ == "__main__":
    unittest.main()
