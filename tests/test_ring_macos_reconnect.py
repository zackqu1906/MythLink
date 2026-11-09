import asyncio
from types import SimpleNamespace

import pytest

from ring_python_sdk.session import connection
from ring_python_sdk.ble import macos_device

TARGET = "a44fbefd-c2d7-46ce-a9a2-731f1d5b2cf3"


def test_known_macos_device_connects_without_advertisement_scan(monkeypatch):
    device = object()
    received = []
    async def retrieve(identifier, timeout):
        assert identifier == TARGET
        return device
    async def unexpected_scan(*args, **kwargs):
        raise AssertionError("Already known UUID must not require advertising")
    class Session(connection.ConnectionMixin):
        timeout_s = 10
        async def _connect_device(self, target, *, new_session):
            received.append(target)
            return True
    monkeypatch.setattr(connection.sys, "platform", "darwin")
    monkeypatch.setattr(macos_device, "retrieve_device", retrieve)
    monkeypatch.setattr(connection.BleakScanner, "find_device_by_address", unexpected_scan)
    assert asyncio.run(Session().connect_target(TARGET))
    assert received == [device]


def test_unknown_uuid_falls_back_to_scan(monkeypatch):
    device = object()
    async def retrieve(*args, **kwargs): return None
    async def scan(identifier, **kwargs):
        return SimpleNamespace(name="Ring", address=identifier)
    class Session(connection.ConnectionMixin):
        timeout_s = 10
        scanned = []
        async def _connect_device(self, target, **kwargs):
            assert target.address == TARGET
            return True
    monkeypatch.setattr(connection.sys, "platform", "darwin")
    monkeypatch.setattr(macos_device, "retrieve_device", retrieve)
    monkeypatch.setattr(connection.BleakScanner, "find_device_by_address", scan)
    assert asyncio.run(Session().connect_target(TARGET))


@pytest.mark.parametrize("async_readiness", [False, True])
def test_native_retrieval_creates_delegate_in_runtime_loop_and_matches_exact_uuid(monkeypatch, async_readiness):
    import sys
    managers = []
    class NativeUUID:
        @staticmethod
        def alloc(): return NativeUUID()
        def initWithUUIDString_(self, value): return value
    peripheral = SimpleNamespace(name=lambda: "Ring", identifier=lambda: SimpleNamespace(UUIDString=lambda: TARGET.upper()))
    class Manager:
        def __init__(self):
            self.loop = asyncio.get_running_loop()
            managers.append(self)
            self.central_manager = SimpleNamespace(retrievePeripheralsWithIdentifiers_=self.retrieve)
        def retrieve(self, ids):
            assert ids == [TARGET]
            return [peripheral]
    if async_readiness:
        async def wait_until_ready(self): pass
        Manager.wait_until_ready = wait_until_ready
    monkeypatch.setitem(sys.modules, "Foundation", SimpleNamespace(NSUUID=NativeUUID))
    monkeypatch.setitem(sys.modules, "bleak.backends.corebluetooth.CentralManagerDelegate", SimpleNamespace(CentralManagerDelegate=Manager))
    async def retrieve_twice():
        first = await macos_device.retrieve_device(TARGET, 2)
        second = await macos_device.retrieve_device(TARGET, 2)
        assert second.details[1] is first.details[1]
        assert len(managers) == 1
        return second
    device = asyncio.run(retrieve_twice())
    assert device.address.lower() == TARGET
    assert device.details == (peripheral, managers[0])
