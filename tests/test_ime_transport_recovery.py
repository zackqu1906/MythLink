"""Recover a host startup collision without touching the user's IMK channel."""
import errno
import json
from pathlib import Path
import socket
import tempfile
import time

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtTest import QTest

from proximic_ring.ui.voice import inline_controller
from proximic_ring.voice.ime.ime_bridge import IMEBridge, IMEBridgeInUseError


@pytest.fixture
def controllers(monkeypatch):
    app = QCoreApplication.instance() or QCoreApplication([])
    permissions = inline_controller.MacPermissionsController
    monkeypatch.setattr(inline_controller, "MacPermissionsController",
                        lambda parent, **_: permissions(parent, enabled=False))
    instances = []

    def create(factory):
        value = inline_controller.InlineInputController(enabled=True, bridge_factory=factory)
        instances.append(value)
        return value

    yield create
    for value in instances:
        value.close()
    app.processEvents()


def wait_until(predicate):
    deadline = time.monotonic() + 2
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert predicate()


def test_busy_host_retries_after_owner_exits_and_accepts_native_handshake(controllers):
    with tempfile.TemporaryDirectory(prefix="ime-retry-", dir="/private/tmp") as directory:
        path = Path(directory) / "bridge.sock"
        owner = IMEBridge(lambda _: None, path=path)
        owner.start()
        waiting = None
        try:
            token = (path.parent / "bridge.token").read_bytes()
            inode = path.stat().st_ino
            attempts = []

            def factory(callback):
                bridge = IMEBridge(callback, path=path)
                attempts.append(bridge)
                return bridge

            waiting = controllers(factory)
            assert waiting._bridge is None
            assert "另一个 Mythlink" in waiting.connectionStatus
            assert waiting._transport_retry_timer.isActive()
            waiting._transport_retry_timer.start(1)
            wait_until(lambda: len(attempts) == 2)
            assert all(bridge._closed and bridge._lock_fd is None for bridge in attempts)
            assert path.stat().st_ino == inode
            assert (path.parent / "bridge.token").read_bytes() == token

            owner.close()
            waiting._transport_retry_timer.start(1)
            wait_until(lambda: waiting._bridge is not None)
            assert len(attempts) == 3
            assert not waiting._bridge._closed
            assert not waiting._transport_retry_timer.isActive()
            assert not waiting._transport_error
            assert not waiting.ready  # Listening alone does not establish input focus.

            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(2)
                client.connect(str(path))
                with client.makefile("rwb") as stream:
                    hello = dict(type="hello", protocol=1,
                                 token=(path.parent / "bridge.token").read_text())
                    stream.write(json.dumps(hello).encode() + b"\n")
                    stream.flush()
                    welcome = json.loads(stream.readline())
                    assert welcome["type"] == "welcome"
                    state = dict(type="state", epoch=welcome["epoch"], client_id="test-client",
                                 ready=True, phase="idle", application="test.editor", selection=[0, 0])
                    stream.write(json.dumps(state).encode() + b"\n")
                    stream.flush()
                    wait_until(lambda: waiting.ready)
                    assert waiting.connectionStatus == "输入法已连接 · 已就绪"
        finally:
            if waiting is not None:
                waiting.close()
            owner.close()


def test_busy_retries_back_off_and_stop_when_controller_closes(controllers):
    attempts = []

    class BusyBridge:
        def __init__(self, callback):
            self.closed = False
            attempts.append(self)

        def start(self):
            raise IMEBridgeInUseError(errno.EAGAIN, "busy")

        def close(self):
            self.closed = True

    controller = controllers(BusyBridge)
    assert controller._transport_retry_timer.interval() == 1000
    for interval in (2000, 4000, 5000, 5000):
        controller._start_bridge()
        assert controller._transport_retry_timer.interval() == interval
    assert len(attempts) == 5 and all(bridge.closed for bridge in attempts)
    controller._transport_retry_timer.start(1)
    controller.close()
    QTest.qWait(30)
    controller._start_bridge()  # A callback already queued at close must also be harmless.
    assert not controller._transport_retry_timer.isActive()
    assert len(attempts) == 5


@pytest.mark.parametrize("error", [
    PermissionError(errno.EACCES, "denied"),
    RuntimeError("输入法通信目录必须仅当前用户可访问（权限 700）"),
    BlockingIOError(errno.EAGAIN, "unrelated resource failure"),
])
def test_other_startup_errors_are_reported_without_retry(controllers, error):
    attempts = []

    class BrokenBridge:
        def __init__(self, callback):
            self.closed = False
            attempts.append(self)

        def start(self):
            raise error

        def close(self):
            self.closed = True

    controller = controllers(BrokenBridge)
    assert controller._bridge is None
    assert controller.connectionStatus == "输入法连接失败：" + str(error)
    assert not controller._transport_retry_timer.isActive()
    QTest.qWait(30)
    assert len(attempts) == 1 and attempts[0].closed
