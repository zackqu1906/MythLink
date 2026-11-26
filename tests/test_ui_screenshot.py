"""Screenshot binding and save-location controls in the actual QML shell."""
from pathlib import Path
import os

import pytest
from PySide6.QtCore import QMetaObject, Q_ARG, QPointF
from PySide6.QtTest import QTest

from test_inline_ui import inline_ui
from test_ui_application_mapping import mapping_ui
from test_ui_global_application_launch import pointer_click
from test_ui_gesture_hand import find, idle, click
from proximic_ring.gestures.gesture_settings import GlobalGestureBindings, GLOBAL_BINDINGS_KEY


@pytest.mark.parametrize('size', [(1200, 780), (940, 560)])
def test_screenshot_system_action_and_directory_settings(mapping_ui, tmp_path, monkeypatch, size):
    owner, _, root, _, _, navigate, _ = mapping_ui
    idle(owner)
    root.resize(*size)
    navigate('globalGestureScope')
    navigate('gestureCard_snap')
    pointer_click(root, 'gestureMappingDropdown')
    pointer_click(root, 'gestureCategory_system')
    rows = find(root, 'gestureActionEditor').property('menuRows').toVariant()
    assert any(row['id'] == 'screen_capture' and row['label'] == '截图' for row in rows)
    pointer_click(root, 'menuAction_screen_capture')
    saved = GlobalGestureBindings.from_json(owner._settings.value(GLOBAL_BINDINGS_KEY))
    assert saved.action_for('snap') == 'screen_capture'
    assert find(root, 'gestureActionEditor').property('savedId') == 'screen_capture'
    shots = Path(os.environ.get('MYTHLINK_SCREENSHOT_DIR', str(tmp_path)))
    shots.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(shots / f'screenshot-binding-{size[0]}.png'))

    QMetaObject.invokeMethod(root, 'showSettings', Q_ARG('QVariant', 6))
    QTest.qWait(180)
    click(root, 'settingsGeneralEntry')
    flick = find(root, 'runtimeSettingsScroll').property('contentItem')
    row = find(root, 'screenshotDirectoryRow')
    offset = row.mapToItem(flick, QPointF()).y() - 20
    flick.setProperty('contentY', min(max(0, flick.property('contentY') + offset),
                                    max(0, flick.property('contentHeight') - flick.height())))
    QTest.qWait(60)
    cap = owner.ringGestures.screenshot
    selected = tmp_path / '我的截图'
    from proximic_ring.screenshots.saved_capture import QFileDialog, QDesktopServices
    monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *_: str(selected))
    opened = []
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda url: opened.append(url.toLocalFile()) or True)
    pointer_click(root, 'changeScreenshotDirectoryButton')
    assert cap.directory == str(selected)
    assert find(root, 'screenshotDirectoryRow').property('description') == str(selected)
    pointer_click(root, 'openScreenshotDirectoryButton')
    assert list(map(Path, opened)) == [selected] and selected.is_dir()
    for name in ('changeScreenshotDirectoryButton', 'openScreenshotDirectoryButton'):
        control = find(root, name)
        position = control.mapToScene(QPointF())
        assert 0 <= position.x() <= root.width() - control.width()
        assert 0 <= position.y() <= root.height() - control.height()
    assert root.grabWindow().save(str(shots / f'screenshot-directory-{size[0]}.png'))


@pytest.mark.parametrize('size', [(1200, 780), (940, 560)])
def test_paste_is_selectable_in_system_functions(mapping_ui, tmp_path, size):
    owner, _, root, _, _, navigate, _ = mapping_ui
    idle(owner)
    root.resize(*size)
    navigate('globalGestureScope')
    navigate('gestureCard_middle-pinch')
    pointer_click(root, 'gestureMappingDropdown')
    pointer_click(root, 'gestureCategory_system')
    rows = find(root, 'gestureActionEditor').property('menuRows').toVariant()
    assert any(row['id'] == 'clipboard_paste' and row['label'] == '粘贴' for row in rows)
    pointer_click(root, 'menuAction_clipboard_paste')
    saved = GlobalGestureBindings.from_json(owner._settings.value(GLOBAL_BINDINGS_KEY))
    assert saved.clipboard_paste == 'middle-pinch'
    assert find(root, 'gestureActionEditor').property('savedId') == 'clipboard_paste'
