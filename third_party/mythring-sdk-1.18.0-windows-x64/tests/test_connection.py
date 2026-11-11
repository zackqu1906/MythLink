import asyncio
import unittest
from types import SimpleNamespace

from mythring.connection import (
    ConnectedRssiReader, OWNER_UNBIND_COMMAND, OwnerDisconnectClient, RingDisconnectOutcome,
    RingManager, RingSystemConnectionState,
)
from mythring.errors import UnsupportedFeatureError


class OwnerDisconnectTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.generation = 1
        self.ready = True
        self.sent = []
        self.results = []
        self.fail_send = False

        async def send(command, generation):
            self.sent.append((command, generation))
            if self.fail_send:
                raise ConnectionError("write failed")
        self.owner = OwnerDisconnectClient(send, generation=lambda: self.generation,
                                            ready=lambda: self.ready, on_result=self.results.append,
                                            timeout=0.025, write_timeout=0.01)

    async def start(self):
        task = asyncio.create_task(self.owner.unbind())
        for _ in range(4):
            await asyncio.sleep(0)
        return task

    async def test_shared_once_per_generation_even_after_timeout(self):
        first, second = await asyncio.gather(self.owner.unbind(), self.owner.unbind())
        self.assertIs(first, second)
        self.assertEqual(first.outcome, RingDisconnectOutcome.ACK_TIMEOUT)
        self.assertIs(await self.owner.unbind(), first)
        self.assertEqual(self.sent, [(OWNER_UNBIND_COMMAND, 1)])
        self.assertEqual(len(self.results), 1)
        self.generation = 2
        await self.owner.unbind()
        self.assertEqual(len(self.sent), 2)

    async def test_waiter_cancellation_does_not_cancel_device_operation(self):
        waiter = await self.start()
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertTrue(self.owner.is_unbinding)
        result = await self.owner.unbind()
        self.assertEqual(result.outcome, RingDisconnectOutcome.ACK_TIMEOUT)
        self.assertEqual(len(self.sent), 1)

    async def test_gatt_disconnect_does_not_claim_system_acl_success(self):
        task = await self.start()
        self.owner.accept(b"\x37\x01\x01\x00\x00", 1)
        self.owner.connection_lost(1)
        result = await task
        self.assertEqual(result.outcome, RingDisconnectOutcome.SYSTEM_STATE_UNCONFIRMED)
        self.assertTrue(result.unbind_acknowledged)
        self.assertTrue(result.link_disconnected)
        self.assertFalse(result.system_disconnected)
        self.assertEqual(self.owner.system_state, RingSystemConnectionState.UNKNOWN)

    async def test_actual_system_observer_allows_success(self):
        task = await self.start()
        self.owner.accept(b"\x37\x01\x01\x00\x00", 1)
        self.owner.observe_system_state(RingSystemConnectionState.DISCONNECTED)
        self.owner.connection_lost(1)
        self.assertEqual((await task).outcome, RingDisconnectOutcome.SUCCESS)

    async def test_no_ack_preserves_unconfirmed_unbind(self):
        task = await self.start()
        self.owner.observe_system_state(RingSystemConnectionState.DISCONNECTED)
        self.owner.connection_lost(1)
        self.assertEqual((await task).outcome, RingDisconnectOutcome.DISCONNECTED_UNCONFIRMED_UNBIND)

    async def test_rejected_signed_code_and_stale_generation(self):
        task = await self.start()
        self.owner.accept(b"\x37\x01\x01\x00\x00", 0)
        self.owner.accept(b"\x37\x01\x00\xf0\xff", 1)
        result = await task
        self.assertEqual(result.outcome, RingDisconnectOutcome.DEVICE_REJECTED)
        self.assertEqual(result.device_code, -16)

    async def test_local_close_is_connection_changed(self):
        task = await self.start()
        self.owner.connection_lost(1, remote=False)
        self.assertEqual((await task).outcome, RingDisconnectOutcome.CONNECTION_CHANGED)

    async def test_not_ready_and_send_failed(self):
        self.ready = False
        self.assertEqual((await self.owner.unbind()).outcome, RingDisconnectOutcome.NOT_READY)
        self.assertEqual(self.sent, [])
        self.ready, self.fail_send = True, True
        self.assertEqual((await self.owner.unbind()).outcome, RingDisconnectOutcome.SEND_FAILED)

    async def test_unsupported_profile_preserves_error_for_shared_waiters(self):
        error = UnsupportedFeatureError("owner_unbind", "legacy_v1")
        attempts = []

        async def gated_send(command, generation):
            attempts.append((command, generation))
            raise error

        self.owner._send = gated_send
        failures = await asyncio.gather(self.owner.unbind(), self.owner.unbind(), return_exceptions=True)
        self.assertTrue(all(failure is error for failure in failures))
        self.assertEqual(error.reason, "unsupported")
        self.assertEqual(error.profile, "legacy_v1")
        self.assertFalse(self.owner.is_unbinding)
        self.assertEqual(self.results, [])
        with self.assertRaises(UnsupportedFeatureError) as caught:
            await self.owner.unbind()
        self.assertIs(caught.exception, error)
        self.assertEqual(attempts, [(OWNER_UNBIND_COMMAND, 1)])


class RssiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.reader = ConnectedRssiReader()
        self.value = asyncio.Future()
        self.reads = 0
        async def physical_read():
            self.reads += 1
            return await self.value
        self.ring = SimpleNamespace(is_connected=True, _generation=1,
                                     _client=SimpleNamespace(_backend=SimpleNamespace(get_rssi=physical_read)))

    async def asyncTearDown(self):
        self.reader.invalidate(self.ring._generation)
        await asyncio.sleep(0)

    async def test_timeout_keeps_physical_request_until_drained(self):
        self.assertIsNone(await self.reader.read(self.ring, timeout=0.01))
        self.assertIsNone(await self.reader.read(self.ring, timeout=0.01))
        self.assertEqual(self.reads, 1)
        self.value.set_result(-52)
        for _ in range(3):
            await asyncio.sleep(0)
        self.assertEqual(await self.reader.read(self.ring), -52)
        self.assertEqual(self.reads, 2)

    async def test_busy_and_rssi_sentinel(self):
        task = asyncio.create_task(self.reader.read(self.ring))
        await asyncio.sleep(0)
        self.assertIsNone(await self.reader.read(self.ring))
        self.value.set_result(127)
        self.assertIsNone(await task)

    async def test_disconnect_resolves_old_read_as_none(self):
        task = asyncio.create_task(self.reader.read(self.ring))
        await asyncio.sleep(0)
        self.ring._generation = 2
        self.ring.is_connected = False
        self.reader.invalidate(1)
        self.assertIsNone(await task)

    async def test_unsupported_backend_is_none(self):
        self.ring._client = object()
        self.assertIsNone(await self.reader.read(self.ring))


class FakeManagedRing:
    def __init__(self, device):
        self.address = device
        self.callbacks = {}
        self.connected = False
        self.closed = 0

    def on(self, event, callback):
        self.callbacks[event] = callback
        return lambda: self.callbacks.pop(event, None)

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False
        self.closed += 1


class RingManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_remember_forward_forget_and_close_all(self):
        manager = RingManager(ring_factory=FakeManagedRing)
        received = []
        unsubscribe = manager.on("status", lambda ring, value: received.append((ring.address, value)))
        first = await manager.connect("AB")
        self.assertIs(manager.remember("ab"), first)
        second = await manager.connect("CD")
        first.callbacks["status"](42)
        self.assertEqual(received, [("AB", 42)])
        unsubscribe()
        first.callbacks["status"](43)
        self.assertEqual(len(received), 1)
        await manager.forget("ab")
        self.assertEqual(manager.rings, (second,))
        self.assertEqual(first.callbacks, {})
        await manager.disconnect()
        self.assertFalse(second.connected)


if __name__ == "__main__":
    unittest.main()
