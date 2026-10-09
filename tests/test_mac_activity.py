"""App Nap assertions follow input lifetimes, including failed/cancelled starts."""
import asyncio
import os
import sys
import threading
from types import SimpleNamespace

import pytest

from proximic_ring.mac_activity import MacActivity
from proximic_ring.audio.ring import RingAudioSource


@pytest.fixture
def foundation(monkeypatch):
    import proximic_ring.mac_activity as activity
    calls = []
    def begin(options, reason):
        token = object()
        calls.append(('begin', token, options, reason))
        return token
    process = SimpleNamespace(beginActivityWithOptions_reason_=begin,
                              endActivity_=lambda token: calls.append(('end', token)))
    module = SimpleNamespace(NSProcessInfo=SimpleNamespace(processInfo=lambda: process),
        NSActivityUserInitiatedAllowingIdleSystemSleep=0xEFFFFF,
        NSActivitySuddenTerminationDisabled=1 << 14,
        NSActivityAutomaticTerminationDisabled=1 << 15,
        NSActivityLatencyCritical=0xFF00000000)
    monkeypatch.setattr(activity.sys, 'platform', 'darwin')
    monkeypatch.setitem(sys.modules, 'Foundation', module)
    return calls, process


def test_concurrent_start_close_balance_and_do_not_prevent_system_sleep(foundation):
    calls, _ = foundation
    activity = MacActivity('test', latency_critical=True)
    threads = [threading.Thread(target=activity.start) for _ in range(12)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert activity.active and len(calls) == 1
    options = calls[0][2]
    assert options & 0xFF00000000 == 0xFF00000000
    assert not options & ((1 << 20) | (1 << 40) | (1 << 14) | (1 << 15))
    threads = [threading.Thread(target=activity.close) for _ in range(12)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert not activity.active and calls[1] == ('end', calls[0][1]) and len(calls) == 2


def test_non_macos_does_not_import_foundation(monkeypatch):
    import proximic_ring.mac_activity as activity
    monkeypatch.setattr(activity.sys, 'platform', 'win32')
    monkeypatch.setitem(sys.modules, 'Foundation', None)
    scope = MacActivity('test')
    assert scope.start() is False
    scope.close()


def test_failed_assertion_is_reported_and_can_retry(foundation, caplog):
    calls, process = foundation
    begin = process.beginActivityWithOptions_reason_
    process.beginActivityWithOptions_reason_ = lambda *_: None
    scope = MacActivity('test')
    assert not scope.start() and not scope.active
    assert 'state=failed' in caplog.text
    process.beginActivityWithOptions_reason_ = begin
    assert scope.start()
    scope.close()
    assert len(calls) == 2


class Session:
    mic_active = swipe_active = touchpad_active = False
    async def swipe_on(self, **options): self.swipe_active = True
    async def swipe_off(self): self.swipe_active = False
    async def touchpad_on(self, **options):
        self.stopped = options['on_stopped']
        self.touchpad_active = True
    async def touchpad_off(self):
        self.touchpad_active = False
        self.stopped(None)
    async def disconnect(self): pass


def test_input_scopes_overlap_without_releasing_gesture_protection(foundation):
    calls, _ = foundation
    async def run():
        source = RingAudioSource(gesture_observer=lambda _: None)
        session = Session()
        await source._start_gestures(session)
        source._touchpad_endpoint = (asyncio.get_running_loop(), session)
        for _ in range(2):  # stop/start creates a fresh scope on the same connection
            await asyncio.wrap_future(source.start_touchpad(on_event=lambda _: None))
            assert source._gesture_activity.active and len(source._touchpad_activities) == 1
            await asyncio.wrap_future(source.stop_touchpad())
            assert source._gesture_activity.active and not source._touchpad_activities
        await source._shutdown_session(session)
        assert not source._gesture_activity.active
        await source._shutdown_session(session)
    asyncio.run(run())
    begins = [c for c in calls if c[0] == 'begin']
    ends = [c[1] for c in calls if c[0] == 'end']
    assert len(begins) == len(ends) == 3
    assert set(c[1] for c in begins) == set(ends)
    assert not begins[0][2] & 0xFF00000000


@pytest.mark.parametrize('error', [RuntimeError('start failed'), asyncio.CancelledError()])
def test_touchpad_and_gesture_failed_start_release_protection(foundation, error):
    calls, _ = foundation
    async def fail(**_): raise error
    async def run():
        source = RingAudioSource(gesture_observer=lambda _: None)
        session = SimpleNamespace(touchpad_on=fail, swipe_on=fail)
        with pytest.raises(type(error)):
            await source._begin_touchpad(session, on_stopped=None)
        assert not source._touchpad_activities
        with pytest.raises(type(error)):
            await source._start_gestures(session)
        assert not source._gesture_activity.active
    asyncio.run(run())
    assert len([c for c in calls if c[0] == 'end']) == 2


def test_automatic_end_releases_even_when_observer_throws(foundation):
    calls, _ = foundation
    def fail(_): raise RuntimeError('observer failed')
    async def run():
        source = RingAudioSource()
        session = Session()
        await source._begin_touchpad(session, on_stopped=fail)
        with pytest.raises(RuntimeError, match='observer failed'):
            session.stopped(RuntimeError('BLE disconnected'))
        assert not source._touchpad_activities
        await source._shutdown_session(session)
    asyncio.run(run())
    assert len(calls) == 2


def test_disconnect_releases_all_scopes_even_if_sdk_cleanup_fails(foundation):
    calls, _ = foundation
    async def run():
        source = RingAudioSource(gesture_observer=lambda _: None)
        session = Session()
        await source._start_gestures(session)
        await source._begin_touchpad(session, on_stopped=None)
        async def fail(): raise RuntimeError('BLE gone')
        session.touchpad_off = session.swipe_off = session.disconnect = fail
        await source._shutdown_session(session)
        assert not source._gesture_activity.active and not source._touchpad_activities
    asyncio.run(run())
    assert len([c for c in calls if c[0] == 'end']) == 2


def test_mouse_process_balances_enable_disable_and_permission_failure(foundation):
    from proximic_ring.native_touchpad import WorkerTouchpadMouse
    calls, _ = foundation
    allowed = [True]
    read_fd, write_fd = os.pipe()
    mouse = WorkerTouchpadMouse(read_fd,
        quartz=SimpleNamespace(CGPreflightPostEventAccess=lambda: allowed[0]),
        accessibility=SimpleNamespace(AXIsProcessTrusted=lambda: True))
    try:
        mouse.enable()
        assert mouse.app_nap_protected
        mouse.disable(); mouse.disable()
        assert not mouse.app_nap_protected and len(calls) == 2
        allowed[0] = False
        with pytest.raises(PermissionError): mouse.enable()
        assert not mouse.app_nap_protected and len(calls) == 2
        allowed[0] = True
        mouse.enable()
        assert mouse.app_nap_protected
    finally:
        mouse.disable()
        os.close(read_fd); os.close(write_fd)
    assert len(calls) == 4
