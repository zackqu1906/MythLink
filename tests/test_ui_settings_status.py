"""Settings status reflects grants and on-disk IME versions, without requesting either."""
import os
from pathlib import Path
import plistlib
import shutil

import pytest
from PySide6.QtCore import QObject, QMetaObject, QPointF, Q_ARG
from PySide6.QtTest import QTest

from proximic_ring.platforms.macos.mac_permissions import PermissionState
from proximic_ring.voice.ime import input_method_install as installer_module
from test_inline_ui import inline_ui
from test_input_method_installer import installer, bundle


@pytest.mark.parametrize("size", [(1200, 780), (940, 700)])
def test_status_is_left_of_action_and_recovers_from_missing_permissions_and_old_version(
    inline_ui, installer, monkeypatch, tmp_path, size
):
    controller, bridge, _, root, _ = inline_ui
    value, _, calls = installer
    monkeypatch.setattr(installer_module, "InputMethodInstaller", lambda: value)
    bundle(value.destination, installer_module.BUNDLE_ID, 'old', ('0.9.0', '1'))
    monitor = controller.inlineInput.permissions
    QTest.qWait(30)
    monitor._timer.stop()
    monitor._generation += 1
    monitor._checking = False
    monkeypatch.setattr(monitor, "refresh", lambda: None)
    monkeypatch.setattr(monitor, "refreshScreenRecording", lambda: None)
    monitor._checked = monitor._screen_checked = True
    monitor._state = PermissionState(False, False)
    monitor._screen_access = False
    monitor._device_access = dict(bluetooth=False, microphone=False)
    monitor.changed.emit()
    messages = list(bridge.messages)
    root.resize(*size)
    root.show()
    QMetaObject.invokeMethod(root, "showSettings", Q_ARG("QVariant", 6))
    QTest.qWait(180)

    def item(name):
        result = root.findChild(QObject, name)
        assert result is not None, name
        return result

    pairs = [
        ("systemAuthorizationStatus", "permissionSetupButton", "未开启"),
        ("settingsAccessibilityStatus", "settingsAccessibilityButton", "未开启"),
        ("inputMethodVersionStatus", "settingsInputMethodButton", "待更新"),
        ("screenRecordingPermissionStatus", "openScreenRecordingPermissionsButton", "未开启"),
        ("settingsBluetoothStatus", "settingsBluetoothButton", "未开启"),
        ("settingsMicrophoneStatus", "settingsMicrophoneButton", "未开启"),
    ]
    version_detail = item('inputMethodInstallationMessage')
    assert '已安装：0.9.0 (1)' in version_detail.property('text')
    assert '随应用提供：1.0.0 (1)' in version_detail.property('text')
    assert version_detail.property('visible')
    assert item('settingsAppVersion').property('text') == controller.appVersion
    for name, button_name, text in pairs:
        label, button = item(name), item(button_name)
        assert label.property("text") == text
        assert label.property("color").name() == "#76520b"
        point = label.mapToItem(button, QPointF())
        assert point.x() + label.width() < 0
        assert point.y() + label.height() / 2 == pytest.approx(button.height() / 2, abs=1)
        right = button.mapToScene(QPointF(button.width(), 0)).x()
        assert 0 < right <= root.width()
    shots = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    shots.mkdir(parents=True, exist_ok=True)
    root.grabWindow().save(str(shots / f"status-pending-{size[0]}.png"))
    flick = item('runtimeSettingsScroll').property('contentItem')
    offset = version_detail.mapToItem(flick, QPointF()).y()
    flick.setProperty('contentY', flick.property('contentY') + offset - flick.height() / 2)
    QTest.qWait(40)
    position = version_detail.mapToItem(flick, QPointF()).y()
    assert 0 <= position < position + version_detail.height() <= flick.height()
    root.grabWindow().save(str(shots / f"input-version-{size[0]}.png"))
    assert not calls and bridge.messages == messages
    # A partial grant must stay yellow; only all four grants turn the summary green.
    monitor._device_access["bluetooth"] = True
    monitor.changed.emit()
    assert item("systemAuthorizationStatus").property("text") == "部分开启"
    assert item("systemAuthorizationStatus").property("color").name() == "#76520b"
    monitor._state = PermissionState(True, True)
    monitor._screen_access = True
    monitor._device_access["microphone"] = True
    monitor.changed.emit()
    target = value.destination / 'Contents/Info.plist'
    target.write_bytes((value.source / 'Contents/Info.plist').read_bytes())
    controller.inlineInput.refreshInputMethodVersion()
    for name, _, _ in pairs:
        assert item(name).property("text") == ("已是最新版" if name == "inputMethodVersionStatus" else "已开启")
        assert item(name).property("color").name() == "#16703f"
    QTest.qWait(40)
    root.grabWindow().save(str(shots / f"status-ready-{size[0]}.png"))
    assert not calls and bridge.messages == messages
    # A newer installed build opens input-source settings instead of the older installer.
    info = plistlib.loads(target.read_bytes())
    info['CFBundleVersion'] = '2'
    target.write_bytes(plistlib.dumps(info))
    controller.inlineInput.refreshInputMethodVersion()
    assert item("inputMethodVersionStatus").property("text") == "已是更高版本"
    assert item("settingsInputMethodButton").property("text") == "输入设置"
    QMetaObject.invokeMethod(item("runtimeSettingsDialog"), "close")


def test_update_completion_rereads_version_even_when_the_installer_reports_success(inline_ui, installer, monkeypatch):
    controller, _, _, _, _ = inline_ui
    value, _, calls = installer
    monkeypatch.setattr(installer_module, "InputMethodInstaller", lambda: value)
    bundle(value.destination, installer_module.BUNDLE_ID, 'old', ('0.9.0', '1'))
    component = controller.inlineInput
    component.refreshInputMethodVersion()
    assert component.inputMethodVersion['state'] == 'outdated'
    # Completion text alone cannot turn a still-old component green.
    component._installation_finished("已安装并启用")
    assert component.inputMethodVersion['state'] == 'outdated'
    (value.destination / 'Contents/Info.plist').write_bytes((value.source / 'Contents/Info.plist').read_bytes())
    component._installation_finished("已安装并启用")
    assert component.inputMethodVersion['state'] == 'current'
    assert not calls


@pytest.mark.parametrize("size", [(1200, 780), (940, 700)])
def test_home_reports_old_input_method_and_clears_notice_after_real_update(
    inline_ui, installer, monkeypatch, tmp_path, size
):
    controller, _, _, root, event = inline_ui
    value, state, calls = installer
    monkeypatch.setattr(installer_module, "InputMethodInstaller", lambda: value)
    bundle(value.destination, installer_module.BUNDLE_ID, 'old', ('0.9.0', '1'))
    root.resize(*size)
    root.setProperty('currentPage', 0)
    root.show()
    QTest.qWait(100)
    notice = root.findChild(QObject, 'homeInputMethodNotice')
    button = root.findChild(QObject, 'homeInputMethodNoticeButton')
    assert notice.property('visible') and '待更新' in notice.property('message')
    assert button.property('visible') and button.property('text') == '立即更新'
    position = button.mapToScene(QPointF())
    assert 0 <= position.x() and position.x() + button.width() <= root.width()
    assert 0 <= position.y() and position.y() + button.height() <= root.height()
    shots = Path(os.environ.get('MYTHLINK_SCREENSHOT_DIR', str(tmp_path)))
    shots.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(shots / f'home-input-method-update-{size[0]}.png'))
    # Home uses the same installer guard and explains why a live input blocks updating.
    dialog = root.findChild(QObject, 'runtimeSettingsDialog')
    QMetaObject.invokeMethod(button, 'click')
    assert not controller.inlineInput.installing
    assert '先结束当前语音' in notice.property('message')
    assert not calls and not dialog.property('visible')
    QMetaObject.invokeMethod(root.findChild(QObject, 'runtimeSettingsButton'), 'click')
    QTest.qWait(160)
    assert dialog.property('visible')
    assert root.findChild(QObject, 'settingsInputMethodButton').property('visible')
    QMetaObject.invokeMethod(dialog, 'close')
    QTest.qWait(160)
    controller.inlineInput._installing = True
    controller.inlineInput._installation_message = '正在更新输入法…'
    controller.inlineInput.changed.emit()
    assert not button.property('enabled')
    assert button.property('text') == '处理中…'
    assert notice.property('message') == '正在更新输入法…'
    controller.inlineInput._installation_finished('安装未完成：测试更新失败')
    assert button.property('enabled') and '测试更新失败' in notice.property('message')
    # A success message alone must not hide a still-outdated installed bundle.
    controller.inlineInput._installation_finished('已安装并启用')
    assert notice.property('visible')
    # Exercise the actual installer worker: rollback on failure, then a successful retry.
    event(phase='idle', ready=False)
    for failure in ('register', ''):
        state['fail'] = failure
        QMetaObject.invokeMethod(button, 'click')
        assert controller.inlineInput.installing
        assert not button.property('enabled')
        assert button.property('text') == '处理中…'
        assert notice.property('message') == controller.inlineInput.installationMessage
        for _ in range(100):
            QTest.qWait(10)
            if not controller.inlineInput.installing:
                break
        assert not controller.inlineInput.installing and not dialog.property('visible')
        assert button.property('enabled')
        if failure:
            assert notice.property('visible')
            assert '安装未完成' in notice.property('message')
            assert 'test registration failure' in notice.property('message')
            assert (value.destination / 'marker').read_text() == 'old'
        else:
            assert not notice.property('visible')
            assert (value.destination / 'marker').read_text() == 'new'
    assert calls
    calls.clear()
    # Returning home also catches an installation changed outside this window.
    root.setProperty('currentPage', 1)
    target = value.destination / 'Contents/Info.plist'
    info = plistlib.loads(target.read_bytes())
    info['CFBundleVersion'] = '2'
    target.write_bytes(plistlib.dumps(info))
    root.setProperty('currentPage', 0)
    assert controller.inlineInput.inputMethodVersion['state'] == 'newer'
    assert not notice.property('visible')
    root.setProperty('currentPage', 1)
    shutil.rmtree(value.destination)
    root.setProperty('currentPage', 0)
    assert notice.property('visible') and '尚未安装' in notice.property('message')
    assert button.property('visible') and button.property('text') == '立即安装'
    assert notice.property('message') == '语音输入法尚未安装。'
    assert not calls
