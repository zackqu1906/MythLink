"""Chat navigation uses current native commands, not historical key presets."""
from dataclasses import replace
import json

import pytest

from proximic_ring.scenes.defaults import chat_actions, default_mappings
from proximic_ring.scenes.menus import chat_navigation_action
from proximic_ring.ui.desktop.application_mapping_controller import ApplicationMappingController, SETTINGS_KEY
from test_app_gestures import route
from test_application_menus import configure

CHATGPT = 'com.openai.codex'


def menu(label, shortcut):
    return dict(id=label + '-' + shortcut, label=label, path='View › ' + label,
                shortcut=shortcut, available=True)


MENUS = [menu('Previous Sidebar Item', 'Cmd+Alt+['), menu('Next Sidebar Item', 'Cmd+Alt+]'),
         menu('Previous Tab', 'Cmd+Shift+['), menu('Next Tab', 'Cmd+Shift+]')]


def refresh(catalog, menus=MENUS, **kwargs):
    catalog._apply_result('menu', catalog._generation['menu'], CHATGPT, dict(actions=menus, **kwargs), '')


def test_no_fixed_chat_shortcut_without_menu_evidence():
    assert chat_actions(CHATGPT, 'ChatGPT', 'regular') == []
    assert default_mappings(CHATGPT, 'ChatGPT')['regular'] == {}
    assert default_mappings(CHATGPT, 'ChatGPT', menus=MENUS[2:])['regular'] == {}


def test_chatgpt_sidebar_and_tab_commands_remain_distinct_and_menu_keys_drive_dispatch(route, monkeypatch):
    _, service, _, backend, sent, _, emit = route
    catalog = configure(service, monkeypatch, CHATGPT)
    backend.target = replace(backend.target, input_context='nontext', focus='page')
    emit('circle-clockwise')
    assert not sent and catalog.pendingDefaultCount == 2
    refresh(catalog)
    assert catalog.actions == MENUS  # No synthetic "next chat" or duplicate presets.
    assert catalog.pendingDefaultCount == 0
    emit('circle-clockwise')
    emit('circle-counterclockwise')
    assert sent == [('codex', 'Cmd+Alt+]'), ('codex', 'Cmd+Alt+[')]
    assert catalog.bindings[CHATGPT]['circle-clockwise']['label'] == 'Next Sidebar Item'
    assert not catalog.for_target(CHATGPT)['menu:circle-clockwise'].system_wide


def test_menu_key_changes_follow_command_identity_and_invalidate_queued_gestures(route, monkeypatch):
    _, service, _, _, _, _, _ = route
    catalog = configure(service, monkeypatch, CHATGPT)
    refresh(catalog)
    assert catalog.setCustomBinding(CHATGPT, 'snap', 'My recorded command', 'Cmd+Alt+]')
    original_custom = dict(catalog.bindings[CHATGPT]['snap'])
    generation = service._generation
    changed = menu('Next Sidebar Item', 'Ctrl+Alt+J')
    refresh(catalog, [MENUS[0], changed, *MENUS[2:]])
    assert catalog.bindings[CHATGPT]['circle-clockwise']['shortcut'] == 'Ctrl+Alt+J'
    assert catalog.bindings[CHATGPT]['circle-clockwise']['id'] == changed['id']
    assert catalog.bindings[CHATGPT]['snap'] == original_custom
    assert service._generation > generation


def test_legacy_chat_keys_retire_before_discovery_and_preserve_reassigned_gestures(route, monkeypatch):
    owner, service, _, _, _, _, _ = route
    old = dict(id='codex:next', label='下一个聊天', path='Codex 默认快捷键', shortcut='Cmd+Shift+]')
    custom = dict(id='custom:kept', label='My keys', path='自定义快捷键', shortcut='Cmd+Shift+]')
    owner._settings.setValue(SETTINGS_KEY, json.dumps({CHATGPT: dict(label='ChatGPT', defaultsVersion=3,
        bindings={'circle-clockwise': old, 'swipe-right': old, 'snap': custom})}))
    catalog = ApplicationMappingController(service)
    monkeypatch.setattr(catalog, '_request', lambda *args: None)
    try:
        catalog.selectApplication(CHATGPT)
        assert catalog.bindings[CHATGPT] == {'snap': custom}
        assert set(catalog.pendingBindings) == {'circle-clockwise', 'swipe-right'}
        refresh(catalog, partial=True)
        assert catalog.bindings[CHATGPT] == {'snap': custom}
        refresh(catalog)
        for gesture in ['circle-clockwise', 'swipe-right']:
            assert catalog.bindings[CHATGPT][gesture]['shortcut'] == 'Cmd+Alt+]'
        assert catalog.bindings[CHATGPT]['snap'] == custom
        restored = ApplicationMappingController(service)
        try:
            assert restored.bindings == catalog.bindings
            assert not restored._apps[CHATGPT]['pendingChatBindings']
        finally:
            restored.close()
    finally:
        catalog.close()


@pytest.mark.parametrize('operation', ['unbind', 'custom', 'clear', 'remove'])
def test_pending_chat_discovery_never_overwrites_user_choices(route, monkeypatch, operation):
    _, service, *_ = route
    catalog = configure(service, monkeypatch, CHATGPT)
    if operation == 'unbind':
        assert catalog.setBinding(CHATGPT, 'circle-clockwise', '')
    elif operation == 'custom':
        assert catalog.setCustomBinding(CHATGPT, 'circle-clockwise', 'Mine', 'Ctrl+J')
    elif operation == 'clear':
        catalog.clearApplicationBindings(CHATGPT)
    else:
        catalog.removeApplication(CHATGPT)
    refresh(catalog)
    bindings = catalog.bindings.get(CHATGPT, {})
    if operation == 'custom':
        assert bindings['circle-clockwise']['shortcut'] == 'Ctrl+J'
    else:
        assert 'circle-clockwise' not in bindings


def test_incomplete_or_ambiguous_menu_never_replaces_existing_command(route, monkeypatch):
    _, service, *_ = route
    catalog = configure(service, monkeypatch, CHATGPT)
    refresh(catalog)
    original = dict(catalog.bindings[CHATGPT]['circle-clockwise'])
    newer = menu('Next Sidebar Item', 'Ctrl+J')
    refresh(catalog, [newer], partial=True)
    assert catalog.bindings[CHATGPT]['circle-clockwise'] == original
    refresh(catalog, [MENUS[1], newer])
    assert catalog.bindings[CHATGPT]['circle-clockwise'] == original
    assert chat_navigation_action('codex', 'next', [MENUS[1], newer]) is None
    assert chat_navigation_action('codex', 'next', [MENUS[3]]) is None
