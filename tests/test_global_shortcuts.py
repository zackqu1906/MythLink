"""Global defaults, custom keys, and built-ins share one persisted gesture route."""
from dataclasses import replace
import json

import pytest

from proximic_ring.gestures.gesture_settings import GlobalGestureBindings, GLOBAL_BINDINGS_KEY
from proximic_ring.ui.gestures.gesture_hud_model import hint_view
from test_app_gestures import route
from test_application_menus import configure, BUNDLE, ACTION
from test_gesture_ownership import outside
from test_interaction_controls import _controller, _close
from test_ring_gestures import request, deliver_gestures


def test_defaults_cover_requested_functions_and_upgrade_preserves_old_choices():
    default = GlobalGestureBindings()
    assert default.as_dict() == dict(show_menu='index-pinch', window_selector='clench',
        scroll_up='swipe-up', scroll_down='swipe-down', agent='double-middle-pinch',
        workflow_recommendations='double-index-pinch')
    old = dict(show_menu='double-middle-pinch', window_selector='snap', scroll_up='', scroll_down='')
    upgraded = GlobalGestureBindings.from_json(json.dumps(old))
    assert all(upgraded.as_dict()[key] == value for key, value in old.items())
    assert upgraded.agent == '' and upgraded.workflow_recommendations == 'double-index-pinch'


def test_custom_actions_swap_with_builtins_and_each_other_and_roundtrip():
    value = GlobalGestureBindings().with_custom('snap', ' 自定义甲 ', 'shift+command+k')
    assert value.custom_for('snap').shortcut == 'Cmd+Shift+K'
    value = value.with_action('clench', 'custom:snap')
    assert value.window_selector == 'snap' and value.custom_for('clench').label == '自定义甲'
    value = value.with_custom('circle-clockwise', '自定义乙', 'Ctrl+Alt+J')
    value = value.with_action('clench', 'custom:circle-clockwise')
    assert value.custom_for('clench').label == '自定义乙'
    assert value.custom_for('circle-clockwise').label == '自定义甲'
    assert GlobalGestureBindings.from_json(value.to_json()) == value
    value = value.with_action('circle-clockwise', '')
    assert value.custom_for('circle-clockwise') is None
    assert value.custom_for('clench').shortcut == 'Ctrl+Alt+J'


def test_custom_settings_reload_after_controller_restart(tmp_path, monkeypatch):
    first = _controller(tmp_path, monkeypatch)
    try:
        assert first.ringGestures.setGlobalCustomBinding('snap', '测试快捷键', 'Cmd+Shift+K')
        assert first.ringGestures.setGlobalGestureAction('circle-clockwise', 'agent')
        first._settings.sync()
        saved = first._global_gesture_bindings
    finally:
        first._app_gestures.close()
        _close(first)
    second = _controller(tmp_path, monkeypatch)
    try:
        assert second._global_gesture_bindings == saved
        assert second.ringGestures.globalActionLabels['custom:snap'] == '测试快捷键'
        assert second.ringGestures.globalBindings['agent'] == 'circle-clockwise'
        assert second.appGestures.catalog.globalOccupancy['snap'] == '测试快捷键'
    finally:
        second._app_gestures.close()
        _close(second)


@pytest.mark.parametrize('label,shortcut', [('', 'Cmd+K'), ('无效', 'K'), ('无效', 'Cmd+'), ('无效', 'Cmd+K, Cmd+J')])
def test_invalid_custom_edit_preserves_saved_action(route, label, shortcut):
    c = route[0]
    assert c.ringGestures.setGlobalCustomBinding('snap', '有效', 'Cmd+K')
    saved = c._settings.value(GLOBAL_BINDINGS_KEY)
    assert not c.ringGestures.setGlobalCustomBinding('snap', label, shortcut)
    assert c._settings.value(GLOBAL_BINDINGS_KEY) == saved


def test_global_custom_dispatch_app_override_removal_and_gesture_rebinding(route, monkeypatch):
    c, service, _, backend, sent, _, _ = route
    outside(backend)
    ring = c.ringGestures
    assert ring.setGlobalCustomBinding('snap', '测试快捷键', 'Cmd+Shift+K')
    request(c, 'snap')
    assert sent == [(BUNDLE, 'Cmd+Shift+K')]
    catalog = configure(service, monkeypatch)
    assert catalog.setBinding(BUNDLE, 'snap', ACTION['id'])
    request(c, 'snap')
    assert sent[-1] == (BUNDLE, 'Ctrl+Tab')
    assert catalog.setBinding(BUNDLE, 'snap', '')
    assert ring.setGlobalGestureAction('circle-clockwise', 'custom:snap')
    request(c, 'snap')
    assert len(sent) == 2
    request(c, 'circle-clockwise')
    assert sent == [(BUNDLE, 'Cmd+Shift+K'), (BUNDLE, 'Ctrl+Tab'), (BUNDLE, 'Cmd+Shift+K')]


@pytest.mark.parametrize('change', ['settings', 'window', 'connection', 'busy', 'imk_focus'])
def test_custom_queued_action_drops_after_target_or_ownership_change(route, change):
    c, _, inline, backend, sent, _, _ = route
    outside(backend)
    assert c.ringGestures.setGlobalCustomBinding('swipe-left', '测试快捷键', 'Cmd+K')
    request(c, 'swipe-left', deliver=False)
    if change == 'settings': c.ringGestures.setGlobalCustomBinding('swipe-left', '新动作', 'Cmd+J')
    if change == 'window': backend.target = replace(backend.target, window='new-window')
    if change == 'connection': c._disconnect_event.set()
    if change == 'busy': inline._view['phase'] = 'listening'
    if change == 'imk_focus': backend.target = replace(backend.target, input_context='text', focus='composer')
    deliver_gestures(c)
    assert not sent


def test_shared_custom_obeys_live_imk_but_independent_custom_works_in_text(route):
    c, _, _, backend, sent, _, _ = route
    assert c.ringGestures.setGlobalCustomBinding('swipe-left', '共享手势', 'Cmd+K')
    request(c, 'swipe-left')
    assert not sent
    assert c.ringGestures.setGlobalCustomBinding('snap', '独立手势', 'Cmd+J')
    request(c, 'snap')
    assert sent == [('codex', 'Cmd+J')]
    outside(backend)
    request(c, 'swipe-left')
    assert sent[-1] == (BUNDLE, 'Cmd+K')


@pytest.mark.parametrize('pipeline', [False, True])
@pytest.mark.parametrize('action,default_gesture', [('agent', 'double-middle-pinch'),
    ('workflow_recommendations', 'double-index-pinch')])
def test_agent_and_recommendations_obey_rebinding_unbinding_and_current_app_override(route, monkeypatch, pipeline, action, default_gesture):
    c, service, _, backend, sent, _, _ = route
    outside(backend)
    agent, calls = c.agent, []
    from proximic_ring.agent_workflow import pipeline_policy
    monkeypatch.setattr(pipeline_policy, 'legacy_workflow_enabled', lambda: pipeline)
    monkeypatch.setattr(agent, 'toggleFocus', lambda: calls.append('agent'))
    monkeypatch.setattr(agent, 'openRecommendations', lambda *args: calls.append('workflow_recommendations'))
    request(c, default_gesture)
    assert calls == [action]
    assert c.ringGestures.setGlobalGestureAction('snap', action)
    request(c, default_gesture)
    assert calls == [action]
    request(c, 'snap')
    assert calls == [action, action]
    catalog = configure(service, monkeypatch)
    assert catalog.setBinding(BUNDLE, 'snap', ACTION['id'])
    request(c, 'snap')
    assert calls == [action, action] and sent == [(BUNDLE, 'Ctrl+Tab')]
    assert c.ringGestures.setGlobalGestureAction('snap', '')
    assert catalog.setBinding(BUNDLE, 'snap', '')
    request(c, 'snap')
    assert calls == [action, action]


def test_hint_model_uses_custom_label_and_app_override():
    bindings = GlobalGestureBindings().with_custom('snap', '放大页面', 'Cmd+=')
    view = hint_view('operation', global_bindings=bindings.assignments(), global_labels=bindings.labels())
    assert next(row for row in view['orbs'] if row['key'] == 'snap')['action'] == '放大页面'
    view = hint_view('operation', [dict(key='snap', action='应用动作')], bindings.assignments(), bindings.labels())
    assert view['orbs'][0]['action'] == '应用动作'
