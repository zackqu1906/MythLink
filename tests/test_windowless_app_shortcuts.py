"""Application commands can create their first window without a window prerequisite."""
from dataclasses import replace
import sys

import pytest

from test_app_shortcuts import desktop
from test_presentation_portability import native_backend


@pytest.mark.parametrize('scene', [False, True])
@pytest.mark.parametrize('focus_role', [None, 'AXApplication'])
@pytest.mark.parametrize('shortcut', ['Cmd+N', 'Cmd+O'])
def test_configured_command_posts_to_windowless_foreground_app(monkeypatch, desktop, scene, focus_role, shortcut):
    _, state, _ = desktop
    focus = dict(AXRole=focus_role) if focus_role else None
    backend, _, _ = native_backend(monkeypatch, None, focus)
    target = backend.capture(menu_action=True, scene=scene)
    assert target is not None and not target.blocked
    assert target.window is None and not target.scene
    backend.post(target, shortcut)
    assert state.sent and {pid for pid, _ in state.sent} == {42}


@pytest.mark.parametrize('role', ['AXMenu', 'AXMenuItem', 'AXMenuBar', 'AXSheet', 'AXDialog'])
@pytest.mark.parametrize('nested', [False, True])
def test_missing_window_does_not_skip_popup_focus_guards(monkeypatch, role, nested):
    focus = dict(AXRole=role)
    if nested:
        focus = dict(AXRole='AXButton', AXParent=focus)
    backend, _, _ = native_backend(monkeypatch, None, focus)
    target = backend.capture(menu_action=True)
    assert target.blocked and target.diagnostic['reason'] != 'window_missing'


@pytest.mark.parametrize('flag,value', [('AXHidden', True), ('AXEnabled', False),
                                     ('AXFocused', False), ('AXElementBusy', True)])
def test_windowless_command_still_rejects_explicitly_invalid_focus(monkeypatch, flag, value):
    backend, _, _ = native_backend(monkeypatch, None, dict(AXRole='AXApplication', **{flag: value}))
    assert backend.capture(menu_action=True).blocked


@pytest.mark.parametrize('attribute', ['AXFocusedWindow', 'AXFocusedUIElement'])
@pytest.mark.parametrize('error,allowed', [(-25212, True), (-25205, True), (-25204, False), (-25211, False)])
def test_missing_optional_ax_attributes_are_not_confused_with_read_failure(monkeypatch, attribute, error, allowed):
    backend, root, _ = native_backend(monkeypatch, None, None)
    ax = sys.modules['ApplicationServices']
    read = ax.AXUIElementCopyAttributeValue
    ax.AXUIElementCopyAttributeValue = lambda node, key, arg: (
        (error, None) if node is root and key == attribute else read(node, key, arg))
    target = backend.capture(menu_action=True)
    assert bool(target is not None and not target.blocked) is allowed


def test_legacy_chat_navigation_still_needs_a_window(monkeypatch):
    backend, _, _ = native_backend(monkeypatch, None, None, bundle='com.openai.codex')
    assert backend.capture().blocked


@pytest.mark.parametrize('change', ['window', 'app', 'pid', 'dialog'])
def test_windowless_command_does_not_follow_a_changed_recipient(monkeypatch, desktop, change):
    _, state, _ = desktop
    backend, root, _ = native_backend(monkeypatch, None, None)
    target = backend.capture(menu_action=True)
    assert target is not None and not target.blocked
    if change == 'window':
        root['AXFocusedWindow'] = dict(AXRole='AXWindow')
    elif change == 'dialog':
        root['AXFocusedUIElement'] = dict(AXRole='AXButton', AXParent=dict(AXRole='AXSheet'))
    else:
        current = replace(target, **({'bundle': 'other.app'} if change == 'app' else {'pid': 43}))
        monkeypatch.setattr(backend, 'capture', lambda **kw: current)
    with pytest.raises(RuntimeError):
        backend.post(target, 'Cmd+N')
    assert not state.sent
