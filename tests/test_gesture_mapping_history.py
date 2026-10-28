"""Undo restores the actual saved transaction without overwriting newer edits."""
from copy import deepcopy
import json

from proximic_ring.gestures.gesture_settings import GestureBindings, GlobalGestureBindings, GLOBAL_BINDINGS_KEY
from proximic_ring.ui.desktop.application_mapping_controller import SETTINGS_KEY
from test_app_gestures import route
from test_application_menus import configure, BUNDLE, ACTION


def test_global_auto_save_and_undo_restore_both_sides_of_a_swap(route):
    c, *_ = route
    history = c.gestureMappingHistory
    before = c._global_gesture_bindings
    assert history.apply('global', '', 'regular', 'swipe-up', {'id': 'window_selector'})
    assert c._global_gesture_bindings.window_selector == 'swipe-up'
    assert c._global_gesture_bindings.scroll_up == 'clench'
    assert GlobalGestureBindings.from_json(c._settings.value(GLOBAL_BINDINGS_KEY)) == c._global_gesture_bindings
    assert not history.undo('global/snap')
    assert history.undo('global/swipe-up')
    assert GlobalGestureBindings.from_json(c._settings.value(GLOBAL_BINDINGS_KEY)) == before
    assert not history.canUndo and not history.undo('global/swipe-up')


def test_voice_auto_save_undo_and_external_change_invalidation(route):
    c, *_ = route
    history = c.gestureMappingHistory
    before = c._gesture_bindings
    assert history.apply('voice', '', 'regular', 'tap', {'voiceAction': 'send'})
    assert c._gesture_bindings.end == ('', '')
    assert c._gesture_bindings.send == ('swipe-up', 'tap')
    assert GestureBindings.from_json(c._settings.value('input/gestureBindings')) == c._gesture_bindings
    assert history.undo('voice/tap')
    assert GestureBindings.from_json(c._settings.value('input/gestureBindings')) == before
    assert history.apply('voice', '', 'regular', 'tap', {'voiceAction': 'send'})
    assert c.setVoiceGestureAction('snap', 'send')
    after = c._gesture_bindings
    assert not history.canUndo and not history.undo('voice/tap')
    assert c._gesture_bindings == after


def test_application_undo_restores_pending_defaults_and_keeps_other_apps(route, monkeypatch):
    c, service, *_ = route
    catalog = configure(service, monkeypatch)
    catalog._apps[BUNDLE]['pendingStart'] = True
    catalog._save()
    before = deepcopy(catalog._apps[BUNDLE])
    history = c.gestureMappingHistory
    assert history.apply('application', BUNDLE, 'regular', 'snap', ACTION)
    assert not catalog._apps[BUNDLE]['pendingStart']
    catalog._apps['other.app'] = {'label': 'Other', 'bindings': {}, 'scenes': {}, 'removed': True}
    catalog._save()
    other = deepcopy(catalog._apps['other.app'])
    assert history.undo(BUNDLE + '/regular/snap')
    saved = json.loads(c._settings.value(SETTINGS_KEY))
    assert saved[BUNDLE] == before and saved['other.app'] == other
    assert not history.apply('application', BUNDLE, 'video', 'snap', ACTION)
    assert json.loads(c._settings.value(SETTINGS_KEY)) == saved


def test_rejected_change_preserves_prior_undo_and_later_external_edit_clears_it(route):
    c, service, inline, *_ = route
    history = c.gestureMappingHistory
    assert history.apply('global', '', 'regular', 'snap', {'id': 'window_selector'})
    inline._view['phase'] = 'listening'
    assert not history.apply('global', '', 'regular', 'tap', {'id': 'show_menu'})
    assert history.contextKey == 'global/snap' and history.error
    assert not history.undo('global/snap')
    inline._view['phase'] = 'idle'
    assert c.ringGestures.setGlobalGestureAction('tap', 'show_menu')
    assert not history.canUndo
