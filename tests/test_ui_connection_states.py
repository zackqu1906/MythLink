"""Real QML controls and controller lifecycle, without real BLE/ASR/input."""
import os
from pathlib import Path
import threading

import pytest
from PySide6.QtCore import QObject, QPointF
from PySide6.QtTest import QTest

from test_inline_ui import inline_ui
from test_ui_device_discovery import click, scanner, wait_until


@pytest.fixture
def runtimes(monkeypatch):
    from proximic_ring.ui import controller as module
    attempts = []
    class Runtime:
        def __init__(self, *_args, **_kw):
            self.started, self.release, self.stopping = (threading.Event() for _ in range(3))
            self.error = ""
            attempts.append(self)
        def run(self, stop, _recognition, **callbacks):
            self.stop, self.callbacks = stop, callbacks
            self.started.set()
            while not self.release.wait(.005):
                if stop.is_set() and not self.stopping.is_set():
                    callbacks["on_stopping"]()
                    self.stopping.set()
            if stop.is_set():
                callbacks["on_disconnected"]()
            if self.error:
                raise RuntimeError(self.error)
    monkeypatch.setattr(module, "RecognitionRuntime", Runtime)
    yield attempts
    for attempt in attempts:
        attempt.release.set()


def prepare(c, root, size):
    c._selector, c._device_name, c._can_reconnect = "test-ring", "Ringo8F56", True
    c.reconnectAvailabilityChanged.emit()
    c.settingsChanged.emit()
    root.resize(*size)
    root.setProperty("currentPage", 0)
    root.show()
    QTest.qWait(80)


def shot(root, tmp_path, state, size):
    path = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", tmp_path))
    path.mkdir(parents=True, exist_ok=True)
    QTest.qWait(40)
    assert root.grabWindow().save(str(path / f"connection-{state}-{size[0]}.png"))


@pytest.mark.parametrize("size", [(940, 700), (1440, 940)])
def test_cancel_retry_late_callbacks_and_connected_preparation(inline_ui, runtimes, tmp_path, size):
    c, _, _, root, _ = inline_ui
    prepare(c, root, size)
    button = root.findChild(QObject, "homeConnectionButton")
    spinner = root.findChild(QObject, "homeConnectionSpinner")
    label = root.findChild(QObject, "homeConnectionStatus")
    errors = root.findChild(QObject, "homeConnectionError")
    workers = []
    try:
        assert button.property("text") == "重新连接" and button.property("enabled")
        click(root, "homeConnectionButton")
        workers.append(c._worker)
        wait_until(lambda: runtimes and runtimes[0].started.is_set())
        first, old = runtimes[0], c._disconnect_event
        assert c.connectionState == "connecting"
        assert button.property("text") == "取消连接" and button.property("enabled")
        assert not button.property("primary") and spinner.property("running")
        assert label.property("text") == "正在连接…"
        assert 0 < label.mapToScene(QPointF()).x() - spinner.mapToScene(QPointF()).x() < 30
        shot(root, tmp_path, "connecting", size)
        click(root, "homeConnectionButton")
        assert c.connectionState == "cancelling" and old.is_set()
        assert button.property("text") == "正在取消…" and not button.property("enabled")
        assert c._proximity_reconnect_cancelled and not c.ringDisconnectNoticeVisible
        c.activateConnectionAction(); c.cancelConnection()  # No duplicate task.
        c._apply_runtime_connected(old)
        c._apply_runtime_started(old)
        c._apply_runtime_status("正在连接设备 late")
        assert c.connectionState == "cancelling" and not c.connected
        root.setProperty("currentPage", 2)
        QTest.qWait(30)
        header = root.findChild(QObject, "headerConnectionStatus")
        assert header.property("text") == "取消中" and not header.property("truncated")
        root.setProperty("currentPage", 0)
        shot(root, tmp_path, "cancelling", size)
        first.error = "Timed out while connection was being cancelled"
        first.release.set()
        wait_until(lambda: c._worker is None)
        assert c.connectionState == "cancelled" and not c.busy
        assert label.property("text") == "已取消连接" and not errors.isVisible()
        assert button.property("text") == "重新连接" and button.property("enabled")
        assert not c.ringDisconnectNoticeVisible and c.selector == "test-ring"
        shot(root, tmp_path, "cancelled", size)
        click(root, "homeConnectionButton")
        workers.append(c._worker)
        wait_until(lambda: len(runtimes) == 2 and runtimes[1].started.is_set())
        second = runtimes[1]
        c._apply_runtime_connected(old)
        c._apply_runtime_finished("late old error", old)
        assert c.connectionState == "connecting"
        second.callbacks["on_connected"]()
        QTest.qWait(20)
        assert c.connectionState == "connected" and c.busy
        assert button.property("text") == "断开设备" and button.property("enabled")
        assert "已连接" in label.property("text") and "准备中" in label.property("text")
        shot(root, tmp_path, "preparing", size)
        click(root, "homeConnectionButton")
        assert c.connectionState == "disconnecting" and not button.property("enabled")
        shot(root, tmp_path, "disconnecting", size)
        second.release.set()
        wait_until(lambda: c._worker is None)
        assert c.connectionState == "disconnected" and not c.ringDisconnectNoticeVisible
    finally:
        for runtime in runtimes:
            runtime.release.set()
        for worker in workers:
            worker.join(2)
        QTest.qWait(30)


def test_connection_failure_shows_retry_and_voice_error_keeps_disconnect(inline_ui, runtimes, tmp_path):
    c, _, _, root, _ = inline_ui
    prepare(c, root, (940, 700))
    click(root, "homeConnectionButton")
    worker = c._worker
    try:
        wait_until(lambda: runtimes and runtimes[0].started.is_set())
        runtimes[0].error = "Timed out connecting to the selected Ring after 21.0s"
        runtimes[0].release.set()
        wait_until(lambda: c._worker is None)
        assert c.connectionState == "failed"
        error_card = root.findChild(QObject, "homeConnectionError")
        assert error_card.isVisible()
        assert 0 < error_card.mapToScene(QPointF()).y() < root.height() - error_card.height()
        assert root.findChild(QObject, "homeConnectionButton").property("text") == "重试连接"
        assert "设备未响应" in c.connectionInfo["detail"]
        shot(root, tmp_path, "failed", (940, 700))
        # Optional voice setup failure is independent from a verified BLE link.
        c._disconnect_event = threading.Event()
        c._proximity_reconnect_cancelled = False
        c._runtime_active = True
        c._apply_runtime_connected(c._disconnect_event)
        c._apply_runtime_voice_availability(c._disconnect_event, "语音模型未配置")
        assert c.connectionState == "connected" and c.connectionInfo["action"] == "disconnect"
        assert not root.findChild(QObject, "homeConnectionError").isVisible()
    finally:
        for runtime in runtimes:
            runtime.release.set()
        worker.join(2)
        c._runtime_active = False


def test_cancel_while_waiting_for_scanner_cleanup_never_connects(inline_ui, scanner, monkeypatch):
    from test_ui_device_discovery import prepare as scan_prepare
    c, _, _, root, _ = inline_ui
    starts = []
    monkeypatch.setattr(c, "_start_selected_device", lambda: starts.append(c.selector))
    scan_prepare(c, root)
    wait_until(lambda: scanner.instances and scanner.instances[0].started.is_set())
    current = scanner.instances[0]
    current.release.clear()
    worker = c._scan_worker
    try:
        click(root, "devicePickerCloseButton")
        QTest.qWait(150)
        click(root, "homeConnectionButton")
        assert c.connectionState == "connecting" and c._pending_device_connection
        click(root, "homeConnectionButton")
        assert c.connectionState == "cancelling" and not c._pending_device_connection
        assert not root.findChild(QObject, "homeConnectionButton").property("enabled")
        current.release.set()
        wait_until(lambda: c._scan_worker is None)
        assert c.connectionState == "cancelled" and not starts
        assert c._proximity_reconnect_cancelled
    finally:
        current.release.set()
        c.stopDeviceDiscovery()
        worker.join(2)
