"""Exercise the installed Bleak delegate paths without physical Bluetooth I/O."""
import asyncio
import sys
import threading
from types import MethodType, SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="CoreBluetooth only")


def delegate(loop):
    from bleak.backends.corebluetooth.CentralManagerDelegate import CentralManagerDelegate

    manager = SimpleNamespace(event_loop=loop, _connect_futures={},
                              _disconnect_futures={}, _disconnect_callbacks={})
    for method in ("connect", "disconnect", "did_connect_peripheral", "did_disconnect_peripheral"):
        setattr(manager, method, MethodType(getattr(CentralManagerDelegate, method), manager))
    return manager


def native_callback(manager, method, *args):
    from bleak.backends.corebluetooth.CentralManagerDelegate import CentralManagerDelegate

    # Run the exact Python implementation called by PyObjC. No NSObject/central
    # manager is constructed, so the regular suite never acquires a BLE device.
    getattr(CentralManagerDelegate, method).callable(manager, None, *args)


def test_scans_and_repeated_sessions_keep_loop_alive_for_late_native_callbacks(monkeypatch):
    from proximic_ring.audio.ring import RingAudioSource
    from proximic_ring.ui.shared.device_scan import DeviceScanWorker
    from ring_python_sdk import ble

    loops, managers, results = [], [], []

    async def scan(_timeout):
        loops.append(asyncio.get_running_loop())
        return []

    async def session(_self):
        loop = asyncio.get_running_loop()
        loops.append(loop)
        managers.append(delegate(loop))

    monkeypatch.setattr(ble, "scan_all_devices", scan)
    monkeypatch.setattr(RingAudioSource, "_run_async", session)
    for _ in range(20):
        scanner = DeviceScanWorker(lambda *result: results.append(result))
        scanner.start(); scanner.join(2)
        assert not scanner.is_alive() and results[-1][2] == ""
        source = RingAudioSource()
        source.connect(); source.close()
        assert source.error is None and source._thread is None
        # Reproduces the crash timing: the RingAudioSource worker has ended,
        # then a native delegate reports the old connection's completion.
        manager = managers[-1]
        assert manager.event_loop.is_running() and not manager.event_loop.is_closed()
        peripheral = SimpleNamespace(identifier=lambda: "retired-session")
        native_callback(manager, "centralManager_didConnectPeripheral_", peripheral)
        delivered = threading.Event()
        manager.event_loop.call_soon_threadsafe(delivered.set)
        assert delivered.wait(1)
    assert len({id(loop) for loop in loops}) == 1


def test_cancel_pending_native_connect_waits_for_disconnect_ack_and_ignores_late_success():
    from CoreBluetooth import CBPeripheralStateConnecting, CBPeripheralStateDisconnected
    from ring_python_sdk.ble.macos_client import CancellableMacClient

    async def run():
        loop = asyncio.get_running_loop()
        manager = delegate(loop)
        connecting, cancelling = asyncio.Event(), asyncio.Event()
        state = [CBPeripheralStateDisconnected]
        peripheral = SimpleNamespace(identifier=lambda: "ring", state=lambda: state[0])
        disconnected = []

        def connect(_peripheral, _options):
            state[0] = CBPeripheralStateConnecting
            connecting.set()

        def cancel(_peripheral):
            cancelling.set()

        manager.central_manager = SimpleNamespace(connectPeripheral_options_=connect,
                                                  cancelPeripheralConnection_=cancel)
        client = object.__new__(CancellableMacClient)
        client._peripheral, client._central_manager_delegate = peripheral, manager

        async def connect_and_close():
            try:
                await manager.connect(peripheral, lambda: disconnected.append(True), timeout=2)
            finally:
                await client.disconnect()

        task = asyncio.create_task(connect_and_close())
        await connecting.wait()
        task.cancel()
        await asyncio.wait_for(cancelling.wait(), .5)
        assert not task.done()  # Native link is pending, is_connected is false.
        native_callback(manager, "centralManager_didConnectPeripheral_", peripheral)
        await asyncio.sleep(0)
        assert not task.done()
        state[0] = CBPeripheralStateDisconnected
        native_callback(manager, "centralManager_didDisconnectPeripheral_error_", peripheral, None)
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, .5)
        assert disconnected == [True]
        assert not manager._connect_futures and not manager._disconnect_futures
        assert not manager._disconnect_callbacks
        # Disconnecting the already closed client must not await a second ACK.
        await asyncio.wait_for(client.disconnect(), .5)

    asyncio.run(run())


def test_missing_disconnect_ack_retires_delegate_before_retry():
    from CoreBluetooth import CBPeripheralStateConnecting
    from ring_python_sdk.ble.macos_client import CancellableMacClient

    async def run():
        loop = asyncio.get_running_loop()
        manager = delegate(loop)
        loop._ringo_central_manager = manager
        peripheral = SimpleNamespace(identifier=lambda: "ring", state=lambda: CBPeripheralStateConnecting)
        requests = []
        manager.central_manager = SimpleNamespace(cancelPeripheralConnection_=requests.append)
        manager._disconnect_callbacks["ring"] = lambda: pytest.fail("retired session called back")
        client = object.__new__(CancellableMacClient)
        client._peripheral, client._central_manager_delegate = peripheral, manager
        # Simulate Transport.close's timeout without spending five seconds.
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(client.disconnect(), .01)
        assert requests == [peripheral]
        assert not hasattr(loop, "_ringo_central_manager")
        assert not manager._disconnect_futures and not manager._disconnect_callbacks
        errors = []
        loop.set_exception_handler(lambda _loop, context: errors.append(context))
        native_callback(manager, "centralManager_didConnectPeripheral_", peripheral)
        native_callback(manager, "centralManager_didDisconnectPeripheral_error_", peripheral, None)
        await asyncio.sleep(0)
        assert not errors

    asyncio.run(run())
