"""Permission monitoring must never stall pointer delivery or replay input."""
import os
import threading
import time
from types import SimpleNamespace

import pytest

from proximic_ring.mac_permissions import MacPermissionError, PermissionState
from proximic_ring.native_touchpad import NativeTouchpadMouse, WorkerTouchpadMouse
from proximic_ring.touchpad_mouse import TouchpadMouseOutput
from ring_python_sdk.touchpad import macos


@pytest.fixture
def pointer(monkeypatch):
    monkeypatch.setattr(macos, 'PERMISSION_CHECK_INTERVAL', .02)
    posted = []
    quartz = SimpleNamespace(CGPreflightPostEventAccess=lambda: True, kCGHIDEventTap=0,
        kCGEventMouseMoved=1, kCGEventLeftMouseDown=2, kCGEventLeftMouseUp=3,
        kCGMouseEventClickState=1, CGEventSetIntegerValueField=lambda *args: None,
        CGEventPost=lambda tap, event: posted.append(event))
    read_fd, write_fd = os.pipe()
    mouse = WorkerTouchpadMouse(read_fd, quartz=quartz,
        accessibility=SimpleNamespace(AXIsProcessTrusted=lambda: True))
    mouse._position = lambda: (10, 20)
    mouse._clamp = lambda x, y: (x, y)
    mouse._event = lambda kind, point: (kind, point)
    yield mouse, posted
    mouse.disable()
    if mouse._permissions:
        mouse._permissions.thread.join(1)
    os.close(read_fd)
    os.close(write_fd)


def test_slow_permission_probe_does_not_block_moves_clicks_or_stop(pointer, monkeypatch):
    import proximic_ring.native_access_worker as worker
    import proximic_ring.mac_workspace as workspace
    mouse, posted = pointer
    entered, release = threading.Event(), threading.Event()
    caller = threading.get_ident()
    checks = []
    def probe():
        checks.append(threading.get_ident())
        if threading.get_ident() != caller:
            entered.set()
            assert release.wait(2)
        return True
    mouse.permitted = probe
    monkeypatch.setattr(workspace, 'frontmost_application', lambda: SimpleNamespace(
        processIdentifier=lambda: 123, bundleIdentifier=lambda: 'test.editor'))
    monkeypatch.setattr(worker, 'read_permission_state',
        lambda: pytest.fail('delivery synchronously queried permissions'))
    dispatcher = worker.Dispatcher()
    dispatcher.touchpad_mouse = mouse
    mouse.enable()
    monitor = mouse._permissions
    try:
        assert entered.wait(1)
        for _ in range(100):
            result = dispatcher.handle(dict(operation='touchpad_apply', created=time.monotonic(),
                events=[dict(kind='move', dx=1, dy=2)]))
            assert result == {'result': {'moves': 1}}
        dispatcher.handle(dict(operation='touchpad_apply', events=[dict(kind='click')], created=time.monotonic()))
        target = dispatcher.handle(dict(operation='touchpad_double_click_target', created=time.monotonic()))
        assert target == {'result': {'bundle': 'test.editor', 'pid': 123}}
        dispatcher.handle(dict(operation='touchpad_health'))
        assert checks == [caller, monitor.thread.ident]
        assert posted == [(1, (11, 22))] * 100 + [(2, (10, 20)), (3, (10, 20))]
        mouse.disable()  # Must also return before the blocked check completes.
        assert not mouse.enabled and monitor.thread.is_alive()
    finally:
        release.set()
        monitor.thread.join(1)
    assert not monitor.thread.is_alive()


@pytest.mark.parametrize('failure', ['denied', 'exception'])
def test_monitor_failure_blocks_delivery_until_explicit_reenable(pointer, failure):
    mouse, posted = pointer
    allow = [True]
    def probe():
        if not allow[0] and failure == 'exception':
            raise RuntimeError('permission service unavailable')
        return allow[0]
    mouse.permitted = probe
    mouse.enable()
    allow[0] = False
    mouse._permissions.thread.join(1)
    assert mouse._permissions.error is not None
    with pytest.raises(PermissionError if failure == 'denied' else RuntimeError):
        mouse.apply([dict(kind='move', dx=1, dy=2)])
    assert not mouse.enabled and not posted
    allow[0] = True
    mouse.apply([dict(kind='move', dx=9, dy=9)])
    assert not posted  # Granting again cannot replay or resume old input.
    mouse.enable()
    mouse.apply([dict(kind='move', dx=1, dy=2)])
    assert posted == [(1, (11, 22))]


def test_late_result_from_stopped_monitor_cannot_revoke_new_session(pointer):
    mouse, posted = pointer
    entered, release = threading.Event(), threading.Event()
    caller = threading.get_ident()
    def probe():
        if threading.get_ident() != caller:
            entered.set()
            assert release.wait(2)
            return False
        return True
    mouse.permitted = probe
    mouse.enable()
    old = mouse._permissions
    try:
        assert entered.wait(1)
        mouse.disable()
        mouse.permitted = lambda: True
        mouse.enable()
    finally:
        release.set()
        old.thread.join(1)
    mouse.apply([dict(kind='move', dx=1, dy=2)])
    assert old.error is None and mouse.enabled and posted == [(1, (11, 22))]


def test_denied_enable_does_not_start_monitor_or_deliver_input(pointer):
    mouse, posted = pointer
    mouse.permitted = lambda: False
    with pytest.raises(PermissionError): mouse.enable()
    assert mouse._permissions is None and not mouse.enabled and not posted


def test_idle_output_reports_monitor_failure_and_closes_channel():
    calls = []
    revoked, ready, ended = threading.Event(), threading.Event(), threading.Event()
    errors = []
    class Channel:
        def call(self, operation, **options):
            calls.append(operation)
            if operation == 'touchpad_health' and revoked.is_set():
                raise MacPermissionError(PermissionState(False, False))
        def close(self): calls.append('close')
    mouse = NativeTouchpadMouse(channel_factory=lambda **_: Channel())
    output = TouchpadMouseOutput(connection=threading.Event(), gain=1, clicks=True, invert_y=False,
        on_ready=ready.set, on_end=lambda reason, error: (errors.append(error), ended.set()),
        mouse_factory=lambda: mouse)
    output.start()
    try:
        assert ready.wait(1)
        # Advance the health deadline without waiting a real second in the test.
        revoked.set()
        mouse._next_health_check = 0.
        output.wake.set()
        assert ended.wait(1)
        assert len(errors) == 1 and isinstance(errors[0], MacPermissionError)
        assert calls == ['touchpad_enable', 'touchpad_health', 'close']
        assert not mouse.enabled
    finally:
        output.stop()
        assert ended.wait(1)


def test_parent_health_poll_is_limited_to_once_per_second(monkeypatch):
    import proximic_ring.native_touchpad as native
    clock, calls = [10.], []
    monkeypatch.setattr(native, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    channel = SimpleNamespace(call=lambda op, **kw: calls.append(op), close=lambda: None)
    mouse = NativeTouchpadMouse(channel_factory=lambda **_: channel)
    try:
        mouse.enable()
        for _ in range(1000): mouse.check_health()
        assert calls == ['touchpad_enable']
        clock[0] = 11.
        for _ in range(1000): mouse.check_health()
        assert calls == ['touchpad_enable', 'touchpad_health']
    finally:
        mouse.close()
