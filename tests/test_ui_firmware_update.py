import threading
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtTest import QTest

from proximic_ring.ui.firmware_update_controller import FirmwareUpdateController
from ring_python_sdk.ota import OtaPhase, OtaProgress

ADDRESS = '12345678-1234-1234-1234-123456789ABC'


class Owner(QObject):
    connectedChanged = Signal()
    busyChanged = Signal()
    batteryChanged = Signal()
    connectionChanged = Signal()
    interactionChanged = Signal()
    statusChanged = Signal()

    def __init__(self):
        super().__init__()
        self.connected, self.busy = True, False
        self.batteryAvailable, self.batteryPercentage = True, 70
        self._utterance_active = self.interactionCanCancel = False
        self.deviceName, self.selector = 'Ringo8F56', ADDRESS
        self._disconnect_event = threading.Event()
        self._worker = None
        self._firmware_update_busy = False
        self.disconnected = self.restored = self.inputs_restored = 0
        self.logs = []

    def disconnectDevice(self):
        self.disconnected += 1
        self.connected = False
        self._disconnect_event.set()

    def _start_selected_device(self, *, firmware_restore=False):
        assert firmware_restore
        self.restored += 1
        self.connected = True

    def _restore_input_preferences(self): self.inputs_restored += 1
    def _append_log(self, message): self.logs.append(message)


@pytest.fixture
def updater():
    owner = Owner()
    update = FirmwareUpdateController(owner)
    source = SimpleNamespace(firmware_identity={'address': ADDRESS, 'version': '2.1.5'}, transport_released=False)
    update.attach(source, owner._disconnect_event)
    yield update, owner, source
    update._timer.stop()
    if update._worker: update._worker.join(2)


def wait_until(predicate):
    for _ in range(200):
        if predicate(): return
        QTest.qWait(10)
    assert predicate()


@pytest.mark.parametrize('battery', [-1, 0, 29])
def test_battery_gate_prevents_disconnect(updater, battery):
    u, owner, _ = updater
    owner.batteryPercentage = battery
    owner.batteryAvailable = battery >= 0
    assert not u.canStart
    u.start()
    assert owner.disconnected == 0


def test_current_identity_must_match_selected_ring(updater):
    u, owner, source = updater
    source.firmware_identity['address'] = '11:22:33:44:55:66'
    u.start()
    assert u.error and not u.busy and owner.disconnected == 0


@pytest.mark.parametrize('fails', [False, True])
def test_release_exclusivity_progress_restore_and_duplicate_click(updater, monkeypatch, fails):
    u, owner, source = updater
    gate, calls = threading.Event(), []
    async def update(client, path, *, on_progress, verify_timeout_s, expected_image_hash):
        assert expected_image_hash == u._bundle.image_hash
        calls.append(client.address)
        on_progress(OtaProgress(OtaPhase.UPLOADING, 50, 100))
        import asyncio
        while not gate.is_set(): await asyncio.sleep(.001)
        if fails: raise RuntimeError('upload lost connection')
        return SimpleNamespace(version='2.1.6+0')
    monkeypatch.setattr('ring_python_sdk.ota.client.OtaClient.update', update)
    # Tests never share or contact the process's real CoreBluetooth loop.
    import asyncio
    monkeypatch.setattr('proximic_ring.ui.firmware_update_controller.run_ble', asyncio.run)
    u.start(); u.start()
    assert u.busy and owner._firmware_update_busy and owner.disconnected == 1
    u._poll()
    assert not calls
    source.transport_released = True
    u._poll()
    wait_until(lambda: u.progress == 50)
    assert calls == [ADDRESS] and not u.canStart
    gate.set()
    wait_until(lambda: not u.busy)
    assert owner.restored == 1 and owner.inputs_restored == 1
    assert not owner._firmware_update_busy
    assert u.error == fails
    assert u.phase == ('failed' if fails else 'complete')


def test_release_timeout_never_uploads(updater):
    u, owner, _ = updater
    u.start(); u._deadline = 0; u._poll()
    assert u.error and not u.busy and u._worker is None
    assert '未开始写入' in u.message


def test_real_qml_settings_entry_and_locked_actions(inline_ui, monkeypatch, tmp_path):
    from PySide6.QtCore import QMetaObject
    from test_ui_device_discovery import click
    c, _, _, root, _ = inline_ui
    root.resize(1100, 780)
    root.show()
    # Exercise the composed settings page via its existing dialog.
    dialog = root.findChild(QObject, 'runtimeSettingsDialog')
    assert dialog is not None
    QMetaObject.invokeMethod(dialog, 'open')
    QTest.qWait(200)
    click(root, 'settingsFirmwareEntry')
    QTest.qWait(200)
    assert root.findChild(QObject, 'startFirmwareUpdateButton').property('visible')
    assert root.findChild(QObject, 'bundledFirmwareVersion').property('text') == c.firmwareUpdate.bundledVersion
    assert not root.findChild(QObject, 'startFirmwareUpdateButton').property('enabled')
    c._firmware_update_busy = True
    c._firmware_update._busy = True
    assert not c.connectionInfo['enabled']
    monkeypatch.setattr(c, '_start_selected_device', lambda **kw: pytest.fail('must not reconnect during OTA'))
    c.reconnectDevice(); c.scanDevices(); c.requestDevicePicker()
    c.connectToDevice('11:22:33:44:55:66', 'another ring'); c.disconnectDevice(); c.requestQuit()
    assert not c._quitting
    c._firmware_update_busy = c._firmware_update._busy = False
    c._firmware_update.changed.emit()
    QTest.qWait(200)
    from pathlib import Path
    preview = Path(__file__).resolve().parents[1] / '.build/firmware-preview'
    preview.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(preview / 'firmware-settings.png'))


from test_inline_ui import inline_ui
