import asyncio
import struct
import unittest

from mythring.errors import UnsupportedFeatureError
from mythring.gesture import (
    GestureClient, GestureControlResult, GestureException, GestureMode,
    HandConfiguration, SwipeDirection, WearingHand, parse_gesture_reply,
)


class GestureReplyTests(unittest.TestCase):
    def test_parse_mode_and_signed_failure(self):
        reply = parse_gesture_reply(b"\x26\x08\x07\x00\x00")
        self.assertEqual((reply.command, reply.operation, reply.result), (0x26, 7, GestureControlResult(GestureMode.LOW_POWER)))
        reply = parse_gesture_reply(b"\x38\x80\x01" + struct.pack("<h", -16) + b"\x00")
        self.assertEqual(reply.result, HandConfiguration(WearingHand.RIGHT, -16))

    def test_malformed(self):
        for packet in (
            b"", b"\x26\x08", b"\x26\x08\x02\x00\x00",
            b"\x38\x80\x02\x00\x00\x00", b"\x38\x80\x01\x00\x00\x02",
            b"\x39\x80\x01\x00\x00\xff", b"\x39\x80\x01\x00\x00\x00\x00",
        ):
            self.assertIsNone(parse_gesture_reply(packet), repr(packet))


class GestureClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sent = asyncio.Queue()

        async def send(packet, generation):
            await self.sent.put((packet, generation))

        self.client = GestureClient(send, timeout=0.1)
        self.client.connected(42)

    async def test_all_commands_match_acks(self):
        cases = (
            (self.client.set_mode(GestureMode.NORMAL), b"\x26\x00", b"\x26\x08\x00\x00\x00"),
            (self.client.set_mode(GestureMode.OFF), b"\x26\x01", b"\x26\x08\x01\x00\x00"),
            (self.client.set_mode(GestureMode.LOW_POWER), b"\x26\x07", b"\x26\x08\x07\x00\x00"),
            (self.client.read_hand(), b"\x38\x00", b"\x38\x80\x00\x00\x00\x00"),
            (self.client.set_hand(WearingHand.LEFT), b"\x38\x01\x01", b"\x38\x80\x01\x00\x00\x01"),
            (self.client.read_swipe_direction(), b"\x39\x00", b"\x39\x80\x00\x00\x00\x00"),
            (self.client.set_swipe_direction(SwipeDirection.REVERSED), b"\x39\x01\x01", b"\x39\x80\x01\x00\x00\x01"),
        )
        for request, command, reply in cases:
            task = asyncio.create_task(request)
            self.assertEqual(await self.sent.get(), (command, 42))
            self.assertTrue(self.client.accept(reply))
            result = await task
            self.assertEqual(result.status, 0)
            if isinstance(result, GestureControlResult):
                self.assertTrue(result.acknowledged)

    async def test_duplicate_busy_different_pairs_concurrent(self):
        first = asyncio.create_task(self.client.read_hand())
        await self.sent.get()
        with self.assertRaisesRegex(GestureException, "busy"):
            await self.client.read_hand()
        second = asyncio.create_task(self.client.read_swipe_direction())
        await self.sent.get()
        self.client.accept(b"\x39\x80\x00\x00\x00\x01")
        self.client.accept(b"\x38\x80\x00\x00\x00\x00")
        self.assertEqual((await second).direction, SwipeDirection.REVERSED)
        self.assertEqual((await first).hand, WearingHand.RIGHT)

    async def test_bad_or_nonmatching_ack_does_not_complete(self):
        task = asyncio.create_task(self.client.read_hand())
        await self.sent.get()
        self.assertFalse(self.client.accept(b"\x29\x02\x00\x00\x50"))
        self.assertTrue(self.client.accept(b"\x38\x80"))
        self.client.accept(b"\x38\x80\x01\x00\x00\x01")
        self.assertFalse(task.done())
        self.client.accept(b"\x38\x80\x00\x00\x00\x01")
        self.assertEqual((await task).hand, WearingHand.LEFT)

    async def test_failed_ack_keeps_actual_configuration(self):
        task = asyncio.create_task(self.client.set_hand(WearingHand.LEFT))
        await self.sent.get()
        self.client.accept(b"\x38\x80\x01\xff\xff\x00")
        with self.assertRaises(GestureException) as caught:
            await task
        self.assertEqual(caught.exception.reason, "device_error")
        self.assertEqual(caught.exception.device_code, -1)
        self.assertEqual(caught.exception.response.hand, WearingHand.RIGHT)

    async def test_disconnection_fails_pending_and_new_calls(self):
        task = asyncio.create_task(self.client.read_hand())
        await self.sent.get()
        self.client.disconnected()
        with self.assertRaisesRegex(GestureException, "disconnected"):
            await task
        with self.assertRaisesRegex(GestureException, "disconnected"):
            await self.client.read_hand()
        self.client.connected(43)
        task = asyncio.create_task(self.client.read_hand())
        self.assertEqual(await self.sent.get(), (b"\x38\x00", 43))
        self.client.accept(b"\x38\x80\x00\x00\x00\x00")
        await task

    async def test_cancel_and_timeout_release_pending_slot(self):
        task = asyncio.create_task(self.client.read_hand())
        await self.sent.get()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        with self.assertRaisesRegex(GestureException, "timeout"):
            await self.client.read_hand()
        await self.sent.get()
        task = asyncio.create_task(self.client.read_hand())
        await self.sent.get()
        self.client.accept(b"\x38\x80\x00\x00\x00\x00")
        await task

    async def test_async_prepare_is_awaited_and_early_ack_ignored(self):
        gate = asyncio.Event()
        started = asyncio.Event()

        async def prepare(packet):
            started.set()
            await gate.wait()

        self.client._prepare = prepare
        task = asyncio.create_task(self.client.read_hand())
        await started.wait()
        self.client.accept(b"\x38\x80\x00\x00\x00\x01")
        self.assertTrue(self.sent.empty())
        gate.set()
        await self.sent.get()
        self.client.accept(b"\x38\x80\x00\x00\x00\x00")
        self.assertEqual((await task).hand, WearingHand.RIGHT)

    async def test_prepare_timeout_and_send_failure(self):
        async def prepare(packet):
            await asyncio.Event().wait()

        self.client._prepare = prepare
        with self.assertRaisesRegex(GestureException, "not_ready"):
            await self.client.read_hand()
        self.assertTrue(self.sent.empty())

        async def send(packet, generation):
            raise OSError("transport unavailable")

        self.client._prepare = None
        self.client._send = send
        with self.assertRaisesRegex(GestureException, "send_failed"):
            await self.client.read_hand()

    async def test_legacy_start_stop_complete_after_write_without_ack(self):
        self.client.connected(43, acknowledged=False)
        for mode, command in ((GestureMode.NORMAL, b"\x26\x00"), (GestureMode.OFF, b"\x26\x01")):
            result = await self.client.set_mode(mode)
            self.assertEqual(await self.sent.get(), (command, 43))
            self.assertEqual(result, GestureControlResult(mode, status=None, acknowledged=False))

    async def test_legacy_unsupported_commands_fail_before_prepare_or_send(self):
        prepared = []

        async def prepare(packet):
            prepared.append(packet)

        self.client._prepare = prepare
        self.client.connected(43, acknowledged=False)
        requests = (
            self.client.set_mode(GestureMode.LOW_POWER), self.client.read_hand(),
            self.client.set_hand(WearingHand.RIGHT), self.client.read_swipe_direction(),
            self.client.set_swipe_direction(SwipeDirection.FORWARD),
        )
        for request in requests:
            with self.assertRaises(GestureException) as caught:
                await request
            self.assertEqual(caught.exception.reason, "unsupported")
        self.assertEqual(prepared, [])
        self.assertTrue(self.sent.empty())

    async def test_prepare_unsupported_preserves_reason_and_does_not_send(self):
        async def prepare(packet):
            raise UnsupportedFeatureError("gesture", "legacy_v1")

        client = GestureClient(self.client._send, prepare=prepare)
        client.connected(43, acknowledged=False)
        with self.assertRaises(GestureException) as caught:
            await client.set_mode(GestureMode.NORMAL)
        self.assertEqual(caught.exception.reason, "unsupported")
        self.assertIsInstance(caught.exception.__cause__, UnsupportedFeatureError)
        self.assertTrue(self.sent.empty())

    async def test_reconnect_default_restores_strict_ack_without_timeout_fallback(self):
        self.client.connected(43, acknowledged=False)
        self.assertFalse((await self.client.set_mode(GestureMode.NORMAL)).acknowledged)
        await self.sent.get()
        self.client.connected(44)
        with self.assertRaisesRegex(GestureException, "timeout"):
            await self.client.set_mode(GestureMode.NORMAL)
        self.assertEqual(await self.sent.get(), (b"\x26\x00", 44))
        task = asyncio.create_task(self.client.set_mode(GestureMode.OFF))
        self.assertEqual(await self.sent.get(), (b"\x26\x01", 44))
        self.assertFalse(task.done())
        self.client.accept(b"\x26\x08\x01\x00\x00")
        self.assertEqual(await task, GestureControlResult(GestureMode.OFF, 0, True))

    async def test_legacy_waits_for_prepare_and_write_ignoring_unsolicited_ack(self):
        preparing, prepared, writing, written = (asyncio.Event() for _ in range(4))

        async def prepare(packet):
            preparing.set()
            await prepared.wait()

        async def send(packet, generation):
            await self.sent.put((packet, generation))
            writing.set()
            await written.wait()

        client = GestureClient(send, prepare=prepare, timeout=1)
        client.connected(43, acknowledged=False)
        task = asyncio.create_task(client.set_mode(GestureMode.NORMAL))
        await preparing.wait()
        client.accept(b"\x26\x08\x00\xff\xff")
        self.assertTrue(self.sent.empty())
        self.assertFalse(task.done())
        prepared.set()
        await writing.wait()
        client.accept(b"\x26\x08\x00\xff\xff")
        self.assertFalse(task.done())
        with self.assertRaisesRegex(GestureException, "busy"):
            await client.set_mode(GestureMode.NORMAL)
        written.set()
        self.assertEqual(await task, GestureControlResult(GestureMode.NORMAL, None, False))

    async def test_legacy_reconnect_during_write_does_not_report_old_success(self):
        started, gate = asyncio.Event(), asyncio.Event()

        async def send(packet, generation):
            started.set()
            await gate.wait()

        client = GestureClient(send, timeout=1)
        client.connected(43, acknowledged=False)
        task = asyncio.create_task(client.set_mode(GestureMode.NORMAL))
        await started.wait()
        client.disconnected()
        client.connected(44)
        gate.set()
        with self.assertRaisesRegex(GestureException, "disconnected"):
            await task

    async def test_legacy_cancellation_and_writer_failure_are_not_success(self):
        started, gate = asyncio.Event(), asyncio.Event()

        async def send(packet, generation):
            started.set()
            await gate.wait()
            raise OSError("write failed")

        client = GestureClient(send, timeout=1)
        client.connected(43, acknowledged=False)
        task = asyncio.create_task(client.set_mode(GestureMode.NORMAL))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        gate.set()
        with self.assertRaisesRegex(GestureException, "send_failed"):
            await client.set_mode(GestureMode.NORMAL)


if __name__ == "__main__":
    unittest.main()
