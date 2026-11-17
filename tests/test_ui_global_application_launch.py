"""The global picker selects installed apps without creating application schemes."""
from copy import deepcopy
from pathlib import Path
import os

import pytest
from PySide6.QtCore import QMetaObject, QPointF, Qt
from PySide6.QtTest import QTest

from proximic_ring.gestures.gesture_settings import GlobalGestureBindings, GLOBAL_BINDINGS_KEY
from test_inline_ui import inline_ui
from test_ui_application_mapping import mapping_ui, BUNDLE
from test_ui_gesture_hand import click, find, idle


def pointer_click(root, name):
    item = find(root, name)
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, point)
    QTest.qWait(150)


def open_picker(root):
    pointer_click(root, 'gestureMappingDropdown')
    rows = find(root, 'gestureActionEditor').property('menuRows').toVariant()
    assert all(row.get('id') != 'open_application' for row in rows)
    pointer_click(root, 'gestureCategory_system')
    pointer_click(root, 'menuAction_open_application')
    assert find(root, 'launchGestureApplicationDialog').property('visible')


@pytest.mark.parametrize('size', [(1200, 780), (940, 560)])
def test_choose_unopened_app_auto_save_cancel_and_undo(mapping_ui, tmp_path, size):
    c, _, root, catalog, _, navigate, _ = mapping_ui
    idle(c)
    root.resize(*size)
    navigate('globalGestureScope')
    navigate('gestureCard_snap')
    before, schemes = c._global_gesture_bindings, deepcopy(catalog._apps)
    shots = Path(os.environ.get('MYTHLINK_SCREENSHOT_DIR', str(tmp_path)))
    shots.mkdir(parents=True, exist_ok=True)
    pointer_click(root, 'gestureMappingDropdown')
    pointer_click(root, 'gestureCategory_system')
    assert root.grabWindow().save(str(shots / f'open-app-menu-{size[0]}.png'))
    pointer_click(root, 'menuAction_open_application')
    dialog = find(root, 'launchGestureApplicationDialog')
    assert dialog.property('visible') and dialog.property('title') == '选择要打开的应用'
    assert c._global_gesture_bindings == before  # No assignment until an app is chosen.
    pointer_click(root, 'showAllLaunchApplications')
    assert find(root, 'chooseLaunchApp_com.example.Other').property('visible')
    find(root, 'launchApplicationSearch').setProperty('text', '终端')
    QTest.qWait(100)
    assert find(root, 'availableLaunchApplications').property('count') == 1
    assert root.grabWindow().save(str(shots / f'open-app-picker-{size[0]}.png'))
    pointer_click(root, 'chooseLaunchApp_com.example.Other')
    assert not dialog.property('visible')
    assert catalog._apps == schemes
    saved = GlobalGestureBindings.from_json(c._settings.value(GLOBAL_BINDINGS_KEY))
    assert saved.application_for('snap').bundle == 'com.example.Other'
    editor = find(root, 'gestureActionEditor')
    assert editor.property('savedId') == 'application:snap'
    assert catalog.globalOccupancy['snap'] == '打开 终端'
    assert root.grabWindow().save(str(shots / f'open-app-saved-{size[0]}.png'))
    open_picker(root)
    assert dialog.property('selectedBundle') == 'com.example.Other'
    QTest.keyClick(root, Qt.Key_Escape)
    QTest.qWait(150)
    assert not dialog.property('visible') and c._global_gesture_bindings == saved
    pointer_click(root, 'undoMenuMappingButton')
    assert c._global_gesture_bindings == before


def test_failed_selection_keeps_previous_mapping_and_context_change_closes_picker(mapping_ui):
    c, _, root, catalog, _, navigate, add = mapping_ui
    idle(c)
    navigate('globalGestureScope')
    navigate('gestureCard_clench')
    before = c._global_gesture_bindings
    open_picker(root)
    c._inline_input._view['phase'] = 'listening'
    pointer_click(root, 'chooseLaunchApp_' + BUNDLE)
    dialog = find(root, 'launchGestureApplicationDialog')
    assert dialog.property('visible')
    assert '结束当前操作' in dialog.property('errorText')
    assert c._global_gesture_bindings == before
    idle(c)
    find(root, 'gesturesPage').setProperty('selectedGesture', 'snap')
    QTest.qWait(150)
    assert not dialog.property('visible')
    add()
    options = find(root, 'gestureActionEditor').property('availableActions').toVariant()
    assert all(item['id'] != 'open_application' for item in options)


@pytest.mark.parametrize('size', [(1200, 780), (940, 560)])
def test_system_menu_shares_five_saved_apps_across_all_gestures(mapping_ui, tmp_path, size):
    from proximic_ring.gestures.gesture_settings import GESTURE_LABELS
    c, _, root, catalog, _, navigate, _ = mapping_ui
    idle(c)
    root.resize(*size)
    navigate('globalGestureScope')
    catalog._candidates = [dict(value=f'com.example.App{index}', label=f'应用 {index}') for index in range(6)]
    assert c.ringGestures.setGlobalApplicationBinding('snap', 'com.example.App0')
    for index in range(1, 6):
        assert c.ringGestures.setGlobalApplicationBinding('clench', f'com.example.App{index}')
    for gesture in GESTURE_LABELS:
        # Select through page state to cover single/double gesture variants alike.
        find(root, 'gesturesPage').setProperty('selectedGesture', gesture)
        QTest.qWait(30)
        pointer_click(root, 'gestureMappingDropdown')
        rows = find(root, 'gestureActionEditor').property('menuRows').toVariant()
        assert [row.get('category', row.get('id')) for row in rows] == ['', 'system', 'shortcut']
        pointer_click(root, 'gestureCategory_system')
        rows = find(root, 'gestureActionEditor').property('menuRows').toVariant()
        assert rows[0]['id'] == 'open_application'
        assert [row['applicationBundle'] for row in rows if row.get('applicationBundle')] == [
            f'com.example.App{index}' for index in range(5, 0, -1)]
        QMetaObject.invokeMethod(find(root, 'gestureMappingMenu'), 'close')
        QTest.qWait(100)
    navigate('gestureCard_snap')
    assert find(root, 'gestureActionEditor').property('saved')['label'] == '打开 应用 0'
    pointer_click(root, 'gestureMappingDropdown')
    pointer_click(root, 'gestureCategory_system')
    shots = Path(os.environ.get('MYTHLINK_SCREENSHOT_DIR', str(tmp_path)))
    shots.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(shots / f'shared-open-app-system-menu-{size[0]}.png'))
    pointer_click(root, 'menuAction_application-choice:com.example.App5')
    assert c._global_gesture_bindings.application_for('snap').bundle == 'com.example.App5'
    assert c._global_gesture_bindings.application_for('clench').bundle == 'com.example.App5'
    pointer_click(root, 'gestureMappingDropdown')
    pointer_click(root, 'gestureCategory_system')
    assert find(root, 'menuAction_application-choice:com.example.App5').property('checked')
    QMetaObject.invokeMethod(find(root, 'gestureMappingMenu'), 'close')
    QTest.qWait(100)
    pointer_click(root, 'undoMenuMappingButton')
    assert c._global_gesture_bindings.application_for('snap').bundle == 'com.example.App0'
