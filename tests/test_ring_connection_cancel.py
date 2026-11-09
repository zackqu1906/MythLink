"""Cancel a real RingSession/provider startup without physical Bluetooth I/O."""
import asyncio
import threading
from types import SimpleNamespace

import pytest

from proximic_ring.audio.ring import RingAudioSource
from ring_mac_sdk import protocol as p
from test_ring_sdk_connection import Client


@pytest.mark.mac_only
@pytest.mark.parametrize("phase", ["connect", "notify", "info"])
def test_cancel_pending_provider_startup_waits_for_cleanup_without_publishing_ready(monkeypatch, tmp_path, phase):
    import bleak
    entered, cleaning, release = threading.Event(), threading.Event(), threading.Event()
    clients, errors = [], []

    async def pending():
        entered.set()
        await asyncio.Event().wait()

    class PendingClient(Client):
        async def connect(self):
            if phase == "connect":
                await pending()
            await super().connect()

        async def start_notify(self, uuid, callback):
            if phase == "notify":
                await pending()
            await super().start_notify(uuid, callback)

        async def write_gatt_char(self, uuid, command, **kw):
            if phase == "info" and command == p.INFO:
                await pending()
            await super().write_gatt_char(uuid, command, **kw)

        async def disconnect(self):
            cleaning.set()
            while not release.is_set():
                await asyncio.sleep(.005)
            await super().disconnect()

    def factory(*args, **kw):
        client = PendingClient(*args, **kw)
        clients.append(client)
        return client

    monkeypatch.setattr(bleak, "BleakClient", factory)
    cancel = threading.Event()
    source = RingAudioSource(device=SimpleNamespace(name="Ringo Test", address="test"),
                             data_root=tmp_path, cancel_event=cancel)
    def connect():
        try:
            source.connect()
        except BaseException as exc:
            errors.append(exc)

    caller = threading.Thread(target=connect)
    caller.start()
    try:
        assert entered.wait(2)
        cancel.set()
        assert cleaning.wait(.5)  # Does not wait for the SDK's 15/20-second timeout.
        assert not source._connected_ready.is_set()
        assert source._touchpad_endpoint is None
        # Repeated close requests must not interrupt the provider's cleanup.
        closer = threading.Thread(target=source.close)
        closer.start()
        release.set()
        caller.join(2); closer.join(2)
        assert not caller.is_alive() and not closer.is_alive()
        assert not errors and source.error is None
        assert clients[0].disconnects == 1 and not clients[0].is_connected
        assert source._touchpad_endpoint is None and not source.gestures_active
    finally:
        cancel.set(); release.set()
        source.close()
        caller.join(2)


def test_cancel_before_connect_does_not_start_bluetooth(monkeypatch):
    cancel = threading.Event()
    cancel.set()
    source = RingAudioSource(cancel_event=cancel)
    monkeypatch.setattr(source, "_thread_main", lambda: pytest.fail("must not start BLE"))
    source.connect()
    assert source._thread is None and source.error is None


def test_cancel_target_discovery_releases_scanner_and_late_success_is_ignored():
    async def run():
        started, cleaned = asyncio.Event(), asyncio.Event()
        cancel = threading.Event()
        source = RingAudioSource(selector="ring-id", cancel_event=cancel)
        class Session:
            async def connect_target(self, identifier):
                assert identifier == "ring-id"
                started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    # A backend may finish successfully while cancellation races
                    # with discovery. It still must not publish a connection.
                    cleaned.set()
                    return True
        task = asyncio.create_task(source._connect_until_stopped(Session()))
        await started.wait()
        cancel.set()
        assert await asyncio.wait_for(task, .5) is None
        assert cleaned.is_set()
    asyncio.run(run())
