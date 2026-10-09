"""Permission recovery and immediate pointer cancellation, without OS input."""
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from proximic_ring.mac_permissions import MacPermissionError, PermissionState
from proximic_ring.native_access import NativeAccessChannel
from proximic_ring.native_touchpad import NativeTouchpadMouse, WorkerTouchpadMouse


def test_pointer_recovers_cached_denial_in_new_child_without_restarting_host(tmp_path, monkeypatch):
    # The host's cached denial must neither block the fresh child nor be bypassed
    # to post events from the host itself.
    monkeypatch.setitem(sys.modules, 'ApplicationServices', SimpleNamespace(
        AXIsProcessTrusted=lambda: pytest.fail('pointer checked host-process trust')))
    grant = tmp_path / 'grant'; grant.write_text('0')
    writes = tmp_path / 'writes'
    worker = tmp_path / 'worker.py'
    worker.write_text('''import sys, json, os, select
from pathlib import Path
grant, writes = map(Path, sys.argv[1:])
cached = grant.read_text() == '1'
cancel = None
for line in sys.stdin:
    m = json.loads(line)
    if not cached or grant.read_text() != '1':
        reply = {'error': 'denied', 'permissions': {'accessibility': grant.read_text() == '1', 'post_events': cached}}
    else:
        if m['operation'] == 'touchpad_enable': cancel = m['cancel_fd']
        if m['operation'] == 'touchpad_apply' and not select.select([cancel], [], [], 0)[0]:
            with writes.open('a') as stream: stream.write('click\\n')
        reply = {'result': None}
    print(json.dumps({'id': m['id'], **reply}), flush=True)
''')
    starts = []
    def factory(**options):
        channel = NativeAccessChannel(**options)
        def start():
            if channel._process is None:
                channel._process = subprocess.Popen([sys.executable, str(worker), str(grant), str(writes)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    pass_fds=channel._pass_fds, bufsize=0)
                starts.append(channel._process.pid)
        monkeypatch.setattr(channel, '_start', start)
        return channel
    mouse = NativeTouchpadMouse(channel_factory=factory)
    host_pid = os.getpid()
    try:
        with pytest.raises(MacPermissionError): mouse.enable()
        assert mouse._channel._process is None and not writes.exists()
        grant.write_text('1')
        mouse.enable()
        assert mouse.enabled and len(starts) == 2 and starts[0] != starts[1]
        assert not writes.exists()  # Checking/granting permissions never moves or clicks.
        mouse.apply([dict(kind='click')])
        assert writes.read_text() == 'click\n' and os.getpid() == host_pid
        mouse.disable()
        mouse.apply([dict(kind='click')])
        assert writes.read_text() == 'click\n'
    finally:
        mouse.close()


def test_stop_gate_reaches_child_during_an_inflight_batch():
    read_fd, write_fd = os.pipe()
    posted = []
    def post(_tap, event):
        posted.append(event)
        os.write(write_fd, b'\0')
    q = SimpleNamespace(CGPreflightPostEventAccess=lambda: True, kCGHIDEventTap=0,
        kCGEventMouseMoved=1, CGEventPost=post)
    mouse = WorkerTouchpadMouse(read_fd, quartz=q, accessibility=SimpleNamespace(AXIsProcessTrusted=lambda: True))
    mouse._position = lambda: (0, 0)
    mouse._clamp = lambda x, y: (x, y)
    mouse._event = lambda kind, point: (kind, point)
    try:
        mouse.enable()
        mouse.apply([dict(kind='move', dx=1, dy=2), dict(kind='move', dx=3, dy=4)])
        assert posted == [(1, (1, 2))] and not mouse.enabled
    finally:
        mouse.disable()
        os.close(read_fd); os.close(write_fd)


def test_worker_discards_stale_batches_and_rechecks_revocation(monkeypatch):
    import proximic_ring.native_access_worker as worker
    import proximic_ring.native_touchpad as touchpad
    import ring_python_sdk.touchpad.macos as macos
    monkeypatch.setattr(macos, 'PERMISSION_CHECK_INTERVAL', .02)
    posted, checks, allowed = [], [], [True]
    def permitted():
        checks.append(threading.get_ident())
        return allowed[0]
    q = SimpleNamespace(CGPreflightPostEventAccess=permitted, kCGHIDEventTap=0,
        kCGEventMouseMoved=1, CGEventPost=lambda tap, event: posted.append(event))
    read_fd, write_fd = os.pipe()
    mouse = WorkerTouchpadMouse(read_fd, quartz=q,
        accessibility=SimpleNamespace(AXIsProcessTrusted=lambda: True))
    mouse._position = lambda: (0, 0)
    mouse._clamp = lambda x, y: (x, y)
    mouse._event = lambda kind, point: (kind, point)
    monkeypatch.setattr(touchpad, 'WorkerTouchpadMouse', lambda fd: mouse)
    def no_synchronous_check(): pytest.fail('queried permissions on the delivery thread')
    monkeypatch.setattr(worker, 'read_permission_state', no_synchronous_check)
    try:
        dispatcher = worker.Dispatcher()
        dispatcher.handle(dict(operation='touchpad_enable', cancel_fd=read_fd,
                               gain=1.5, clicks=False, invert_y=True))
        assert not posted and checks.count(threading.get_ident()) == 1
        batch = [dict(kind='move', dx=1, dy=2)]
        dispatcher.handle(dict(operation='touchpad_apply', events=batch, created=time.monotonic()-1))
        assert not posted
        checks.clear()
        result = dispatcher.handle(dict(operation='touchpad_apply', events=batch, created=time.monotonic()))
        assert result == {'result': {'moves': 1}} and threading.get_ident() not in checks
        assert posted == [(1, (1.5, -3.))]
        allowed[0] = False
        mouse._permissions.thread.join(1)  # Monitor exits after observing denial.
        assert mouse._permissions.error is not None
        monkeypatch.setattr(worker, 'read_permission_state', lambda: PermissionState(True, False))
        with pytest.raises(MacPermissionError):
            dispatcher.handle(dict(operation='touchpad_apply', events=batch, created=time.monotonic()))
        assert len(posted) == 1 and not mouse.enabled
    finally:
        mouse.disable()
        os.close(read_fd)
        os.close(write_fd)


def test_app_pointer_uses_fresh_channel_instead_of_main_process_quartz(monkeypatch):
    import proximic_ring.native_touchpad as native
    from proximic_ring.touchpad_mouse import TouchpadMouseOutput
    ready, ended = threading.Event(), threading.Event()
    calls = []
    class Mouse:
        def enable(self): calls.append('enable')
        def disable(self): pass
        def close(self): calls.append('close')
    monkeypatch.setattr(native, 'NativeTouchpadMouse', Mouse)
    output = TouchpadMouseOutput(connection=threading.Event(), gain=1, clicks=True, invert_y=False,
        on_ready=ready.set, on_end=lambda *_: ended.set())
    output.start()
    assert ready.wait(1)
    output.stop()
    assert ended.wait(1) and calls == ['enable', 'close']
