import asyncio
import json
import struct
import time
import unittest
from unittest.mock import patch

from mythring.touchpad import (
    DIAGNOSTICS, INFO, MODEL_ID, START, STOP, TokenTracker, TouchpadClient, TouchpadDeviceMismatch,
    TouchpadProtocolError, TouchpadSequenceError, TouchpadStage,
    parse_diagnostics, parse_info, parse_status, parse_token, parse_tokens,
    require_touchpad_inference, sample_arrival,
)


def token_packet(seq=0, *, epoch=7, session=42, uptime=100, reset=True, features=None):
    return bytes((0x3C, 2, 2, int(reset))) + struct.pack("<4I", session, epoch, seq, uptime) + struct.pack(
        "8b", *(features if features is not None else (32, 64, -32, 0, 127, -64, 1, -127)))


def status_packet(state=1, result=0, *, session=42, epoch=7, deadline=0):
    packet = bytearray(64)
    packet[:4] = bytes((0x3C, 0x11, 2, state))
    struct.pack_into("<i9I", packet, 4, result, session, epoch, 10, 5, 5, 0, 1, 500, 200)
    struct.pack_into("<IHH4BII", packet, 44, MODEL_ID, 200, 100, 8, 255, 1, 1, 2000, deadline)
    return bytes(packet)


def info_packet(version=b"2.1.6-dev.touchpad.5.debug", hardware=2):
    return bytes((0x2A, 2, 1, hardware, 0, 0, 0, 0, 0, len(version))) + version


class TouchpadProtocolTests(unittest.TestCase):
    def test_quantization_and_reserved_value(self):
        token = parse_token(token_packet())
        self.assertEqual(token.features, (1, 1, -1, 0, 127 / 32, -1, 1 / 64, -127 / 32))
        self.assertEqual((token.session, token.epoch, token.sequence, token.uptime, token.reset), (42, 7, 0, 100, True))
        with self.assertRaisesRegex(TouchpadProtocolError, "Reserved"):
            parse_token(token_packet(features=(-128,) + (0,) * 7))
        for malformed in (b"", token_packet()[:-1], bytes((0x3C, 2, 1)) + token_packet()[3:],
                          token_packet()[:3] + b"\x02" + token_packet()[4:]):
            with self.subTest(malformed=malformed), self.assertRaises(TouchpadProtocolError):
                parse_token(malformed)

    def test_batch_atomicity_and_limits(self):
        first = token_packet()
        second = token_packet(1, reset=False, uptime=110)
        parsed = parse_tokens(b"\x3c\x13\x02\x02" + first + second)
        self.assertEqual([item.sequence for item in parsed], [0, 1])
        for malformed in (b"\x3c\x13\x02\x01" + first,
                          b"\x3c\x13\x02\x02" + first + second[:-1],
                          b"\x3c\x13\x02\x02" + first + token_packet(1, session=99)):
            with self.subTest(malformed=malformed), self.assertRaises(TouchpadProtocolError):
                parse_tokens(malformed)

    def test_status_and_device_identity_gates(self):
        status = parse_status(status_packet())
        self.assertEqual((status.session, status.epoch, status.max_us, status.total_us), (42, 7, 500, 2000))
        self.assertTrue(parse_status(status_packet(state=2)).stopped)
        self.assertFalse(parse_status(status_packet(state=0, result=-16)).stopped)
        for offset, new in ((44, 0), (48, 199), (50, 99), (52, 7), (53, 6), (54, 0), (55, 0)):
            packet = bytearray(status_packet())
            packet[offset] = new
            with self.subTest(offset=offset), self.assertRaises(TouchpadDeviceMismatch):
                parse_status(packet)

    def test_info_strict_utf8_and_hardware(self):
        self.assertEqual(parse_info(info_packet(b"future.debug")), "future.debug")
        for packet in (info_packet(b"\xff"), info_packet()[:-1], info_packet(hardware=1)):
            with self.assertRaises(TouchpadProtocolError):
                parse_info(packet)

    def test_ringo_info_requires_exact_delivered_firmware_and_explicit_mode(self):
        version = b"2.1.6-dev.touchpad.9.debug"
        self.assertEqual(parse_info(info_packet(version, hardware=1), ringo=True), version.decode())
        with self.assertRaises(TouchpadDeviceMismatch):
            parse_info(info_packet(version, hardware=1))
        for packet in (info_packet(version, hardware=2), info_packet(hardware=1),
                       info_packet(b"2.1.6-dev.touchpad.9", hardware=1),
                       info_packet(b"2.1.6-dev.touchpad.10.debug", hardware=1)):
            with self.subTest(packet=packet), self.assertRaises(TouchpadDeviceMismatch):
                parse_info(packet, ringo=True)

    def test_diagnostics_signed_result(self):
        packet = b"\x3c\x12\x02\x03" + struct.pack("<13IiI", *range(13), -16, 4)
        status = parse_diagnostics(packet)
        self.assertEqual((status.phase, status.last_send_result, status.fault_name), (3, -16, "ble_send_failed"))

    def test_sequence_epochs_uptime_and_transactional_batch(self):
        tracker = TokenTracker()
        tracker.start(parse_status(status_packet()))
        first = parse_token(token_packet())
        second = parse_token(token_packet(1, reset=False, uptime=110))
        bad_third = parse_token(token_packet(3, reset=False, uptime=120))
        with self.assertRaises(TouchpadSequenceError):
            tracker.accept_all((first, second, bad_third))
        self.assertTrue(tracker.first)
        self.assertEqual(tracker.accept_all((first, second)), (True, False))
        new_epoch = parse_token(token_packet(2, epoch=8, uptime=120))
        self.assertEqual(tracker.accept_all((new_epoch,)), (True,))
        with self.assertRaises(TouchpadProtocolError):
            tracker.accept_all((parse_token(token_packet(3, epoch=8, uptime=120, reset=False)),))
        self.assertEqual(tracker.sequence, 2)
        self.assertEqual(sample_arrival((first, second), 0, 100_000_000), 90_000_000)

    def test_uint32_wrap_is_allowed(self):
        tracker = TokenTracker(session=42, epoch=0xFFFFFFFF, sequence=0xFFFFFFFF, uptime=0xFFFFFFFA, first=False)
        token = parse_token(token_packet(0, epoch=0, uptime=4))
        self.assertTrue(tracker.accept(token))

    def test_inference_loads_the_cpu_model(self):
        with patch("mythring.touchpad_inference.Raw2Model") as model:
            self.assertIs(require_touchpad_inference(), model.return_value)
            model.assert_called_once_with()


class TouchpadClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sent = []
        self.received = []
        self.errors = []
        self.diagnostics = []
        self.emit_first = True
        self.ack = True
        self.info = info_packet()
        self.start_reply = status_packet()
        self.stop_reply = status_packet(state=0)
        self.stop_misses = 0
        self.diagnostics_reply = b"\x3c\x12\x02\x03" + struct.pack("<13IiI", *range(13), -16, 3)

        async def send(packet):
            self.sent.append(packet)
            if not self.ack:
                return
            if packet == INFO:
                self.client.feed(self.info)
            elif packet == STOP:
                if self.stop_misses:
                    self.stop_misses -= 1
                    return
                self.client.feed(self.stop_reply)
            elif packet == START:
                self.client.feed(self.start_reply)
                if self.emit_first:
                    self.client.feed(token_packet())
            elif packet == DIAGNOSTICS and self.diagnostics_reply is not None:
                self.client.feed(self.diagnostics_reply)

        self.client = TouchpadClient(send, timeout=0.03, on_tokens=self.received.append,
                                     on_error=self.errors.append, on_diagnostics=self.diagnostics.append)

    def ringo(self):
        self.info = info_packet(b"2.1.6-dev.touchpad.9.debug", hardware=1)
        self.client.INITIAL_STOP_ATTEMPT = 0.003
        self.client.INITIAL_STOP_RETRY_DELAY = 0.001
        self.client.INITIAL_STOP_BUDGET = 0.05

    async def asyncTearDown(self):
        self.client.disconnected()
        await asyncio.sleep(0)

    async def test_handshake_first_token_and_stop(self):
        self.assertFalse(self.client.active)
        status = await self.client.start(mtu=247)
        self.assertTrue(self.client.active)
        self.assertEqual(status.session, 42)
        self.assertEqual(self.sent, [INFO, STOP, START])
        self.assertEqual(self.client.stage, TouchpadStage.RUNNING)
        self.assertEqual(self.received[0][0].sequence, 0)
        self.client.feed(token_packet(1, reset=False, uptime=110))
        await self.client.stop()
        self.assertEqual(self.client.stage, TouchpadStage.STOPPED)
        self.assertFalse(self.client.active)
        self.client.feed(token_packet(2, reset=False, uptime=120))
        self.assertEqual(len(self.received), 2)

    async def test_mtu_blocks_wire_writes(self):
        with self.assertRaisesRegex(TouchpadProtocolError, "MTU"):
            await self.client.start(mtu=23)
        self.assertEqual(self.sent, [])

    async def test_gap_stops_stream_without_delivering_bad_data(self):
        await self.client.start(mtu=247)
        self.client.feed(token_packet(2, reset=False, uptime=120))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertEqual(self.client.stage, TouchpadStage.FAILED)
        self.assertEqual(len(self.received), 1)
        self.assertIsInstance(self.errors[0], TouchpadSequenceError)
        self.assertEqual(self.sent[-1], STOP)

    async def test_first_token_timeout_attempts_cleanup(self):
        self.emit_first = False
        with self.assertRaises(asyncio.TimeoutError) as failure:
            await self.client.start(mtu=247)
        self.assertEqual(self.client.stage, TouchpadStage.FAILED)
        self.assertIs(self.client.error, failure.exception)
        self.assertEqual(len(self.diagnostics), 1)
        self.assertEqual(self.diagnostics[0].error_type, "TimeoutError")
        self.assertEqual(self.sent, [INFO, STOP, START, STOP, DIAGNOSTICS])

    async def test_command_timeout_quarantines_until_connection_reset(self):
        self.ack = False
        with self.assertRaises(asyncio.TimeoutError):
            await self.client.start(mtu=247)
        self.assertEqual(self.sent, [INFO, STOP])
        self.ack = True
        with self.assertRaisesRegex(RuntimeError, "reconnect"):
            await self.client.start(mtu=247)
        self.client.disconnected()
        await self.client.start(mtu=247)
        self.assertEqual(self.client.stage, TouchpadStage.RUNNING)

    async def test_stale_input_fails(self):
        await self.client.start(mtu=247)
        self.client.feed(token_packet(1, reset=False, uptime=110), time.monotonic_ns() - 250_000_000)
        self.assertEqual(self.client.stage, TouchpadStage.FAILED)
        self.assertEqual(len(self.received), 1)

    async def _expired_watchdog_poll(self, queued_callback=None):
        await self.client.start(mtu=247)
        self.client._cancel_watchdog()
        self.client._last_input_ns -= 1_100_000_000
        sleep, polls = asyncio.sleep, 0

        async def poll(delay):
            nonlocal polls
            if delay == 0:
                return await sleep(0)
            polls += 1
            if polls > 1:
                raise asyncio.CancelledError
            # Resume the watchdog first, leaving a BLE callback ready behind it.
            if queued_callback is not None:
                asyncio.get_running_loop().call_soon(queued_callback)

        with patch("mythring.touchpad.asyncio.sleep", poll):
            await self.client._watch(self.client._generation)
        if self.client._cleanup is not None:
            await self.client._cleanup

    async def test_watchdog_processes_queued_notification_before_declaring_timeout(self):
        await self._expired_watchdog_poll(
            lambda: self.client.feed(token_packet(1, reset=False, uptime=110)))
        self.assertEqual(self.client.stage, TouchpadStage.RUNNING)
        self.assertEqual(self.received[-1][0].sequence, 1)
        self.assertEqual(self.errors, [])
        self.assertEqual(self.sent, [INFO, STOP, START])

    async def test_watchdog_still_fails_and_stops_after_real_input_silence(self):
        await self._expired_watchdog_poll()
        self.assertEqual(self.client.stage, TouchpadStage.FAILED)
        self.assertEqual(len(self.errors), 1)
        self.assertIsInstance(self.errors[0], TouchpadProtocolError)
        self.assertIn("timed out after 1 second", str(self.errors[0]))
        self.assertEqual(self.sent, [INFO, STOP, START, STOP, DIAGNOSTICS])

    async def test_watchdog_rechecks_connection_after_yield(self):
        await self._expired_watchdog_poll(self.client.disconnected)
        self.assertEqual(self.client.stage, TouchpadStage.STOPPED)
        self.assertEqual(self.errors, [])
        self.assertEqual(self.sent, [INFO, STOP, START])

    async def test_failure_diagnostics_keep_original_error_and_stopped_fault_evidence(self):
        await self.client.start(mtu=247)
        self.stop_reply = status_packet(state=2)
        original = TouchpadProtocolError("stream stopped unexpectedly")
        self.client._fail(original)
        self.client._fail(TouchpadProtocolError("duplicate failure"))
        await self.client._cleanup
        self.assertIs(self.client.error, original)
        self.assertEqual(self.client.stage, TouchpadStage.FAILED)
        self.assertEqual(self.errors, [original])
        self.assertEqual(len(self.diagnostics), 1)
        self.assertIs(self.client.last_failure_diagnostics, self.diagnostics[0])
        exported = json.loads(json.dumps(self.diagnostics[0].to_dict()))
        self.assertEqual(exported["error_message"], str(original))
        self.assertEqual(exported["status"]["state"], 2)
        self.assertEqual(exported["diagnostics"]["fault_name"], "feature_tx_age_exceeded")
        self.assertEqual(self.sent, [INFO, STOP, START, STOP, DIAGNOSTICS])
        self.client.disconnected()
        self.assertIsNone(self.client.last_failure_diagnostics)

    async def test_diagnostics_timeout_keeps_confirmed_stop_without_retrying_or_replacing_error(self):
        await self.client.start(mtu=247)
        self.diagnostics_reply = None
        original = TouchpadProtocolError("no feature input")
        self.client._fail(original)
        await self.client._cleanup
        self.assertIs(self.client.error, original)
        self.assertTrue(self.client._quarantined)
        snapshot = self.client.last_failure_diagnostics
        self.assertTrue(snapshot.status.stopped)
        self.assertFalse(self.client.active)
        self.assertIsNone(snapshot.diagnostics)
        self.assertIn("TimeoutError", snapshot.diagnostics_error)
        self.assertEqual(self.errors, [original])
        self.assertEqual(len(self.diagnostics), 1)
        self.assertEqual(self.sent, [INFO, STOP, START, STOP, DIAGNOSTICS])

    async def test_diagnostics_parse_failure_does_not_replace_stream_error(self):
        await self.client.start(mtu=247)
        self.diagnostics_reply = b"\x3c\x12\x02"
        original = TouchpadProtocolError("stream failed")
        self.client._fail(original)
        await self.client._cleanup
        self.assertIs(self.client.error, original)
        self.assertTrue(self.client.last_failure_diagnostics.status.stopped)
        self.assertIn("Invalid Raw2 diagnostics", self.client.last_failure_diagnostics.diagnostics_error)
        self.assertEqual(self.sent, [INFO, STOP, START, STOP, DIAGNOSTICS])

    async def test_unconfirmed_cleanup_stop_does_not_query_diagnostics(self):
        await self.client.start(mtu=247)
        self.ack = False
        original = TouchpadProtocolError("stream failed")
        with self.assertLogs("mythring.touchpad", level="WARNING"):
            self.client._fail(original)
            await self.client._cleanup
        self.assertIs(self.client.error, original)
        self.assertEqual(self.diagnostics, [])
        self.assertIsNone(self.client.last_failure_diagnostics)
        self.assertTrue(self.client.active)
        self.assertEqual(self.sent, [INFO, STOP, START, STOP])

    async def test_cleanup_occupancy_releases_at_stop_ack_before_diagnostics_complete(self):
        await self.client.start(mtu=247)
        stop_sent, release_stop, diagnostics_sent = asyncio.Event(), asyncio.Event(), asyncio.Event()
        normal_send = self.client._send

        async def send(packet):
            if packet == STOP:
                self.sent.append(packet)
                stop_sent.set()
                await release_stop.wait()
                self.client.feed(status_packet(state=2))
            elif packet == DIAGNOSTICS:
                self.sent.append(packet)
                diagnostics_sent.set()
            else:
                await normal_send(packet)

        self.client._send = send
        self.client._fail(TouchpadProtocolError("stream failed"))
        await asyncio.wait_for(stop_sent.wait(), 1)
        self.assertEqual(self.client.stage, TouchpadStage.FAILED)
        self.assertTrue(self.client.active)
        release_stop.set()
        await asyncio.wait_for(diagnostics_sent.wait(), 1)
        self.assertFalse(self.client.active)
        self.assertFalse(self.client._cleanup.done())
        await self.client._cleanup
        self.assertFalse(self.client.active)

    async def test_ambiguous_start_write_failure_or_cancel_keeps_occupancy_until_disconnect(self):
        normal_send = self.client._send
        for failure in (OSError, asyncio.CancelledError):
            with self.subTest(failure=failure.__name__):
                async def send(packet):
                    if packet == START:
                        self.sent.append(packet)
                        self.assertTrue(self.client.active)
                        raise failure("START may already have reached the ring")
                    await normal_send(packet)

                self.client._send = send
                with self.assertRaises(failure):
                    await self.client.start(mtu=247)
                self.assertTrue(self.client.active)
                self.assertTrue(self.client._quarantined)
                self.client.disconnected()
                self.assertFalse(self.client.active)

    async def test_rejected_stop_does_not_release_touchpad_occupancy(self):
        await self.client.start(mtu=247)
        self.stop_reply = status_packet(state=1, result=-16)
        with self.assertRaises(TouchpadProtocolError):
            await self.client.stop()
        self.assertEqual(self.client.stage, TouchpadStage.FAILED)
        self.assertTrue(self.client.active)

    async def test_disconnect_during_diagnostics_does_not_publish_old_evidence(self):
        await self.client.start(mtu=247)
        queried = asyncio.Event()
        normal_send = self.client._send

        async def send(packet):
            if packet == DIAGNOSTICS:
                self.sent.append(packet)
                queried.set()
                await asyncio.Event().wait()
            else:
                await normal_send(packet)

        self.client._send = send
        self.client._fail(TouchpadProtocolError("stream failed"))
        cleanup = self.client._cleanup
        await asyncio.wait_for(queried.wait(), 1)
        self.client.disconnected()
        with self.assertRaises(asyncio.CancelledError):
            await cleanup
        self.assertEqual(self.diagnostics, [])
        self.assertIsNone(self.client.last_failure_diagnostics)

    async def test_diagnostics_callback_failure_is_logged_without_changing_stream_failure(self):
        await self.client.start(mtu=247)

        def callback(_snapshot):
            raise ValueError("diagnostics consumer failed")

        self.client.on_diagnostics = callback
        original = TouchpadProtocolError("stream failed")
        with self.assertLogs("mythring.touchpad", level="ERROR"):
            self.client._fail(original)
            await self.client._cleanup
        self.assertIs(self.client.error, original)
        self.assertIsNotNone(self.client.last_failure_diagnostics)
        self.assertEqual(self.sent, [INFO, STOP, START, STOP, DIAGNOSTICS])

    async def test_disconnect_invalidates_active_and_queued_operations(self):
        self.ack = False
        first = asyncio.create_task(self.client.start(mtu=247))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        queued = asyncio.create_task(self.client.start(mtu=247))
        queued_stop = asyncio.create_task(self.client.stop())
        await asyncio.sleep(0)
        self.client.disconnected()
        self.ack = True
        new = asyncio.create_task(self.client.start(mtu=247))
        for task in (first, queued, queued_stop):
            with self.assertRaises(ConnectionError):
                await task
        await new
        self.assertEqual(self.sent, [INFO, INFO, STOP, START])
        self.assertEqual(self.client.stage, TouchpadStage.RUNNING)

    async def test_cancelled_stop_is_failed_and_quarantined(self):
        await self.client.start(mtu=247)
        self.ack = False
        stopping = asyncio.create_task(self.client.stop())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        stopping.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await stopping
        self.assertEqual(self.client.stage, TouchpadStage.FAILED)
        with self.assertRaisesRegex(RuntimeError, "reconnect"):
            await self.client.start(mtu=247)

    async def test_ringo_initial_stop_ack_timeout_retries_then_starts(self):
        self.ringo()
        self.stop_misses = 1
        status = await self.client.start(mtu=247, ringo=True)
        self.assertEqual(status.session, 42)
        self.assertEqual(self.sent, [INFO, STOP, STOP, START])
        self.assertEqual(self.client.stage, TouchpadStage.RUNNING)

    async def test_r1_initial_stop_timeout_does_not_retry(self):
        self.stop_misses = 1
        with self.assertRaises(TimeoutError):
            await self.client.start(mtu=247)
        self.assertEqual(self.sent, [INFO, STOP, STOP])
        self.assertTrue(self.client._quarantined)

    async def test_ringo_start_still_checks_model_identity(self):
        self.ringo()
        bad = bytearray(status_packet())
        bad[44] ^= 1
        self.start_reply = bytes(bad)
        with self.assertRaises(TouchpadDeviceMismatch):
            await self.client.start(mtu=247, ringo=True)
        self.assertEqual(self.sent, [INFO, STOP, START, STOP, DIAGNOSTICS])
        self.assertEqual(self.received, [])

    async def test_ringo_initial_stop_rejection_is_not_retried(self):
        self.ringo()
        self.stop_reply = status_packet(state=0, result=-16)
        with self.assertLogs("mythring.touchpad", level="WARNING"), self.assertRaisesRegex(
                TouchpadProtocolError, "STOP refused"):
            await self.client.start(mtu=247, ringo=True)
        self.assertEqual(self.sent, [INFO, STOP, STOP])  # One initial request and one cleanup.

    async def test_ringo_write_timeout_is_not_retried(self):
        self.ringo()
        normal_send = self.client._send

        async def send(packet):
            if packet != STOP:
                return await normal_send(packet)
            self.sent.append(packet)
            await asyncio.sleep(1)

        self.client._send = send
        with self.assertLogs("mythring.touchpad", level="WARNING"), self.assertRaises(TimeoutError):
            await self.client.start(mtu=247, ringo=True)
        self.assertEqual(self.sent, [INFO, STOP, STOP])
        self.assertTrue(self.client._quarantined)

    async def test_ringo_write_failure_is_not_retried(self):
        self.ringo()
        normal_send = self.client._send

        async def send(packet):
            if packet != STOP or STOP in self.sent:
                return await normal_send(packet)
            self.sent.append(packet)
            raise OSError("BLE write failed")

        self.client._send = send
        with self.assertRaisesRegex(OSError, "BLE write failed"):
            await self.client.start(mtu=247, ringo=True)
        self.assertEqual(self.sent, [INFO, STOP, STOP])
        self.assertTrue(self.client._quarantined)

    async def test_ringo_start_timeout_is_not_retried(self):
        self.ringo()
        normal_send = self.client._send

        async def send(packet):
            if packet != START:
                return await normal_send(packet)
            self.sent.append(packet)

        self.client._send = send
        with self.assertRaises(TimeoutError):
            await self.client.start(mtu=247, ringo=True)
        self.assertEqual(self.sent, [INFO, STOP, START, STOP])
        self.assertTrue(self.client._quarantined)

    async def test_ringo_later_stop_timeout_is_not_retried(self):
        self.ringo()
        await self.client.start(mtu=247, ringo=True)
        self.stop_misses = 1
        with self.assertRaises(TimeoutError):
            await self.client.stop()
        self.assertEqual(self.sent, [INFO, STOP, START, STOP])
        self.assertTrue(self.client._quarantined)

    async def test_ringo_initial_stop_has_a_total_deadline(self):
        self.ringo()
        self.client.INITIAL_STOP_BUDGET = 0.012
        self.stop_misses = 100
        with self.assertRaisesRegex(TimeoutError, "deadline expired"):
            await self.client.start(mtu=247, ringo=True)
        self.assertNotIn(START, self.sent)
        self.assertGreaterEqual(self.sent.count(STOP), 2)
        self.assertTrue(self.client._quarantined)

    async def test_cancel_during_ringo_retry_delay_requires_reconnect(self):
        self.ringo()
        self.client.INITIAL_STOP_RETRY_DELAY = 0.2
        self.stop_misses = 1
        starting = asyncio.create_task(self.client.start(mtu=247, ringo=True))
        async with asyncio.timeout(1):
            while STOP not in self.sent or self.client._pending is not None:
                await asyncio.sleep(0.001)
        starting.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await starting
        self.assertEqual(self.sent, [INFO, STOP, STOP])
        with self.assertRaisesRegex(RuntimeError, "reconnect"):
            await self.client.start(mtu=247, ringo=True)
        self.client.disconnected()
        await self.client.start(mtu=247, ringo=True)
        self.assertEqual(self.client.stage, TouchpadStage.RUNNING)

    async def test_disconnect_during_ringo_retry_stops_old_generation(self):
        self.ringo()
        self.client.INITIAL_STOP_RETRY_DELAY = 0.01
        self.stop_misses = 1
        starting = asyncio.create_task(self.client.start(mtu=247, ringo=True))
        async with asyncio.timeout(1):
            while STOP not in self.sent or self.client._pending is not None:
                await asyncio.sleep(0.001)
        self.client.disconnected()
        with self.assertRaises(ConnectionError):
            await starting
        self.assertEqual(self.sent, [INFO, STOP])
        await self.client.start(mtu=247, ringo=True)
        self.assertEqual(self.client.stage, TouchpadStage.RUNNING)

    async def test_unsolicited_status_invalidates_running_stream(self):
        await self.client.start(mtu=247)
        self.client.feed(status_packet(state=2))
        self.client.feed(token_packet(1, reset=False, uptime=110))
        self.assertEqual(self.client.stage, TouchpadStage.FAILED)
        self.assertRegex(str(self.errors[0]), "Unexpected stream status")
        self.assertEqual(len(self.received), 1)

    async def test_duplicate_start_status_before_first_token_fails(self):
        normal_send = self.client._send

        async def send(packet):
            if packet != START:
                return await normal_send(packet)
            self.sent.append(packet)
            self.client.feed(status_packet())
            self.client.feed(status_packet())
            self.client.feed(token_packet())

        self.client._send = send
        with self.assertRaisesRegex(TouchpadProtocolError, "Unexpected stream status"):
            await self.client.start(mtu=247)
        self.assertEqual(self.received, [])

    async def test_token_before_start_ack_fails_and_cannot_reopen_stream(self):
        normal_send = self.client._send

        async def send(packet):
            if packet != START:
                return await normal_send(packet)
            self.sent.append(packet)
            self.client.feed(token_packet())
            self.client.feed(status_packet())
            self.client.feed(token_packet())

        self.client._send = send
        with self.assertRaisesRegex(TouchpadProtocolError, "before START acknowledgement"):
            await self.client.start(mtu=247)
        self.assertEqual(self.received, [])
        self.assertEqual(self.client.stage, TouchpadStage.FAILED)
        self.assertTrue(self.client._quarantined)


if __name__ == "__main__":
    unittest.main()
