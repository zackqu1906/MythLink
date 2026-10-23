"""Feature notices are read-only; permissions are managed in Settings."""
from pathlib import Path
import os

import pytest
from PySide6.QtCore import QObject, QPointF
from PySide6.QtTest import QTest

from proximic_ring.platforms.macos.mac_permissions import PermissionState
from test_inline_ui import inline_ui
from test_ui_shell import visual_child


def shot(root, tmp_path, name):
    directory = Path(os.environ.get('MYTHLINK_SCREENSHOT_DIR', str(tmp_path)))
    directory.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(directory/name))


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
        assert notice.property('actionText') == ''
        assert '系统与权限' in notice.property('message')
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
