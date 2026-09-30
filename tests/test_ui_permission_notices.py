"""Permission notices belong to their feature; contact actions stay local."""
from pathlib import Path
import os

import pytest
from PySide6.QtCore import QObject, QMetaObject, QPointF
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from proximic_ring.mac_permissions import PermissionState
from test_inline_ui import inline_ui
from test_ui_shell import visual_child


def shot(root, tmp_path, name):
    directory = Path(os.environ.get('MYTHLINK_SCREENSHOT_DIR', str(tmp_path)))
    directory.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(directory/name))


@pytest.mark.parametrize('size', [(1440, 940), (940, 700)])
def test_contact_is_separate_from_help_and_copies_only_basic_information(inline_ui, monkeypatch, tmp_path, size):
    controller, bridge, _, root, _ = inline_ui
    monkeypatch.setattr(controller.inlineInput.permissions, '_refresh_device_permissions', lambda: None)
    controller.inlineInput.permissions._device_access = {'bluetooth': None, 'microphone': None}
    controller.inlineInput.permissions.changed.emit()
    root.resize(*size); root.setProperty('currentPage', 0); root.show()
    before = {k: controller._settings.value(k) for k in controller._settings.allKeys()}
    bindings, messages = controller.gestureBindings, list(bridge.messages)
    clipboard = QGuiApplication.clipboard(); previous = clipboard.text()
    try:
        QMetaObject.invokeMethod(root.findChild(QObject, 'contactButton'), 'click'); QTest.qWait(150)
        contact, help_ = (root.findChild(QObject, name) for name in ('contactDialog', 'helpDialog'))
        assert contact.property('visible') and not help_.property('visible')
        QMetaObject.invokeMethod(root.findChild(QObject, 'copyContactEmailButton'), 'click')
        assert clipboard.text() == 'zackqu1906@gmail.com'
        QMetaObject.invokeMethod(root.findChild(QObject, 'copyContactAppInfoButton'), 'click')
        assert clipboard.text().splitlines()[0] == 'MythLink'
        assert len(clipboard.text().splitlines()) == 3
        assert '版本：' in clipboard.text() and '系统：' in clipboard.text()
        assert '明天三点开会' not in clipboard.text() and '/Users/' not in clipboard.text()
        shot(root, tmp_path, f'contact-{size[0]}.png')
        QMetaObject.invokeMethod(contact, 'close')
        for _ in range(60):
            if not contact.property('visible'):
                break
            QTest.qWait(10)
        assert not contact.property('visible')
        QMetaObject.invokeMethod(root.findChild(QObject, 'helpButton'), 'click'); QTest.qWait(100)
        assert help_.property('visible') and not contact.property('visible')
        assert root.findChild(QObject, 'runtimeLogButton').property('visible')
        QMetaObject.invokeMethod(help_, 'close')
        assert controller.gestureBindings == bindings and bridge.messages == messages
        assert {k: controller._settings.value(k) for k in controller._settings.allKeys()} == before
    finally:
        clipboard.setText(previous)


@pytest.mark.parametrize('size', [(1440, 940), (940, 700)])
def test_home_only_shows_known_missing_relevant_permissions_and_recovers(inline_ui, monkeypatch, tmp_path, size):
    controller, _, _, root, _ = inline_ui
    monitor = controller.inlineInput.permissions
    monitor._timer.stop()
    state = PermissionState(False, False)
    monkeypatch.setattr(monitor, '_reader', lambda: state)
    monkeypatch.setattr(monitor, '_refresh_device_permissions', lambda: None)
    monitor._generation += 1
    monitor._apply(monitor._generation, state)
    monitor._device_access = {'bluetooth': False, 'microphone': False}
    monitor.changed.emit()
    root.resize(*size); root.setProperty('currentPage', 0); root.show(); QTest.qWait(120)
    container = root.findChild(QObject, 'homePermissionNotices')
    assert container.property('visible')
    assert visual_child(container, 'homePermission_accessibility') is not None
    assert visual_child(container, 'homePermission_bluetooth') is not None
    assert visual_child(container, 'homePermission_microphone') is None
    controller._audio_source = 'microphone'; controller.settingsChanged.emit(); QTest.qWait(30)
    microphone = visual_child(container, 'homePermission_microphone')
    assert microphone is not None and microphone.isVisible()
    for kind in ('accessibility', 'bluetooth', 'microphone'):
        notice = visual_child(container, 'homePermission_'+kind)
        point = notice.mapToScene(QPointF())
        assert point.x() >= root.property('sidebarWidth') and point.x() + notice.width() <= root.width()
    shot(root, tmp_path, f'home-permissions-{size[0]}.png')
    # Screen permission stays next to window previews, never blocks the home.
    state = PermissionState(True, True)
    monitor._device_access = {'bluetooth': True, 'microphone': True}
    monitor._screen_checked, monitor._screen_access = True, False
    monitor._apply(monitor._generation, state); QTest.qWait(30)
    assert not container.property('visible') and monitor.screenRecordingWarning
    # Unavailable/undetermined status is not presented as missing permission.
    state = PermissionState()
    monitor._device_access = {'bluetooth': None, 'microphone': None}
    monitor._apply(monitor._generation, state); QTest.qWait(30)
    assert not container.property('visible')
