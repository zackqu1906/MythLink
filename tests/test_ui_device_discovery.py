"""Closing discovery cancels the scanner and releases the UI immediately."""
import asyncio
import threading
import time

import pytest
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtTest import QTest

from test_inline_ui import inline_ui


def wait_until(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert predicate()


def click(root, name):
    button = root.findChild(QObject, name)
    assert button.property("visible") and button.property("enabled"), name
    QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier,
                     button.mapToScene(QPointF(button.width()/2, button.height()/2)).toPoint())


@pytest.fixture
def scanner(monkeypatch):
    from ring_python_sdk.ble import control

    class Scanner:
        instances = []
        def __init__(self, **_options):
            self.started = threading.Event()
            self.stopping = threading.Event()
            self.release = threading.Event()
            self.release.set()
            self.instances.append(self)

        async def start(self):
            self.started.set()

        async def stop(self):
            self.stopping.set()
            while not self.release.is_set():
                await asyncio.sleep(0.005)

    monkeypatch.setattr(control, "BleakScanner", Scanner)
    yield Scanner
    for instance in Scanner.instances:
        instance.release.set()


def prepare(controller, root):
    controller._selector = "remembered-ring"
    controller._can_reconnect = True
    controller.reconnectAvailabilityChanged.emit()
    root.setProperty("currentPage", 0)
    root.show(); QTest.qWait(100)
    click(root, "homeDevicePickerButton")
    QTest.qWait(150)


def test_completed_scan_keeps_results_and_device_handle_for_connection(inline_ui, monkeypatch):
    from types import SimpleNamespace
    from ring_python_sdk import ble
    c, _, _, root, _ = inline_ui
    handle = object()
    async def scan(_timeout):
        return [SimpleNamespace(name="Ringo Test", identifier="RING-ID", rssi=-42, device=handle)]
    monkeypatch.setattr(ble, "scan_all_devices", scan)
    starts = []
    monkeypatch.setattr(c, "_start_selected_device", lambda: starts.append(c._selected_device))
    prepare(c, root)
    wait_until(lambda: c._scan_worker is None)
    assert not c.scanBusy
    assert c.availableDevices == [{"name": "Ringo Test", "identifier": "RING-ID", "rssi": -42}]
    c.connectToDevice("RING-ID", "Ringo Test")
    assert starts == [handle]


@pytest.mark.parametrize("dismiss", ["close", "cancel", "escape"])
def test_close_picker_stops_scan_without_waiting_and_reenables_home(inline_ui, scanner, dismiss):
    c, _, _, root, _ = inline_ui
    prepare(c, root)
    wait_until(lambda: scanner.instances and scanner.instances[0].started.is_set())
    worker = c._scan_worker
    assert c.scanBusy
    try:
        if dismiss == "escape":
            QTest.keyClick(root, Qt.Key_Escape)
        else:
            click(root, "devicePickerCloseButton" if dismiss == "close" else "devicePickerCancelButton")
        # No delay for the popup exit animation or the scan timeout.
        assert not c.scanBusy and not c._discovery_active
        assert c.statusKind == "idle"
        for name in ("homeConnectionButton", "homeDevicePickerButton"):
            assert root.findChild(QObject, name).property("enabled")
        wait_until(lambda: scanner.instances[0].stopping.is_set())
        wait_until(lambda: c._scan_worker is None)
        snapshot = (c.scanMessage, c.statusTitle, c.availableDevices)
        c._device_scan_finished(worker, [{"name": "late", "identifier": "old"}], "late error")
        assert (c.scanMessage, c.statusTitle, c.availableDevices) == snapshot
    finally:
        c.stopDeviceDiscovery()
        worker.join(2)


def test_immediate_rescan_waits_for_old_cleanup_and_ignores_old_results(inline_ui, scanner):
    c, _, _, root, _ = inline_ui
    prepare(c, root)
    wait_until(lambda: scanner.instances and scanner.instances[0].started.is_set())
    first = scanner.instances[0]
    first.release.clear()
    old = c._scan_worker
    try:
        click(root, "devicePickerCloseButton")
        wait_until(first.stopping.is_set)
        QTest.qWait(100)
        click(root, "homeDevicePickerButton")
        assert c.scanBusy and c._discovery_active
        assert len(scanner.instances) == 1
        first.release.set()
        wait_until(lambda: len(scanner.instances) == 2 and scanner.instances[1].started.is_set())
        current = c._scan_worker
        c._device_scan_finished(old, [{"name": "late", "identifier": "old"}], "old error")
        assert c._scan_worker is current and c.scanBusy and c.availableDevices == []
        QTest.qWait(150)
        click(root, "devicePickerCancelButton")
        assert not c.scanBusy
        wait_until(lambda: c._scan_worker is None)
        assert scanner.instances[1].stopping.is_set()
    finally:
        first.release.set()
        c.stopDeviceDiscovery()
        if c._scan_worker is not None:
            c._scan_worker.join(2)


def test_reconnect_after_cancel_waits_for_scanner_cleanup(inline_ui, scanner, monkeypatch):
    c, _, _, root, _ = inline_ui
    starts = []
    monkeypatch.setattr(c, "_start_selected_device", lambda: starts.append(c.selector))
    prepare(c, root)
    wait_until(lambda: scanner.instances and scanner.instances[0].started.is_set())
    first = scanner.instances[0]
    first.release.clear()
    try:
        click(root, "devicePickerCloseButton")
        wait_until(first.stopping.is_set)
        QTest.qWait(100)
        click(root, "homeConnectionButton")
        assert c.busy and not c.scanBusy and starts == []
        first.release.set()
        wait_until(lambda: starts == ["remembered-ring"])
        assert c._scan_worker is None and not c._pending_device_connection
    finally:
        first.release.set()
        c.stopDeviceDiscovery()
        if c._scan_worker is not None:
            c._scan_worker.join(2)


def test_cancel_before_scan_worker_starts_never_starts_bluetooth(inline_ui, scanner, monkeypatch):
    from proximic_ring.ui.shared.device_scan import DeviceScanWorker
    c, _, _, root, _ = inline_ui
    start = DeviceScanWorker.start
    monkeypatch.setattr(DeviceScanWorker, "start", lambda self: None)
    prepare(c, root)
    worker = c._scan_worker
    click(root, "devicePickerCloseButton")
    assert not c.scanBusy
    start(worker)
    wait_until(lambda: c._scan_worker is None)
    worker.join(2)
    assert not scanner.instances


def test_cancel_pending_connection_cannot_start_after_scan_cleanup(inline_ui, scanner, monkeypatch):
    c, _, _, root, _ = inline_ui
    starts = []
    monkeypatch.setattr(c, "_start_selected_device", lambda: starts.append(c.selector))
    prepare(c, root)
    wait_until(lambda: scanner.instances and scanner.instances[0].started.is_set())
    first = scanner.instances[0]
    first.release.clear()
    try:
        c.connectToDevice("new-ring", "Ringo New")
        assert c.busy and c._pending_device_connection
        c.stopDeviceDiscovery()
        assert not c.busy and not c.scanBusy and not c._pending_device_connection
        first.release.set()
        wait_until(lambda: c._scan_worker is None)
        assert starts == []
    finally:
        first.release.set()
        c.stopDeviceDiscovery()
        if c._scan_worker is not None:
            c._scan_worker.join(2)
