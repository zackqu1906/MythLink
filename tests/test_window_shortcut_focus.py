"""Window commands must not inherit a layout container's enabled/focused state."""
from dataclasses import replace
import sys
from types import SimpleNamespace

import pytest

from proximic_ring.scenes.recognition.focus import inspect_focus, WINDOW_SHORTCUT_CONTAINERS
from proximic_ring.scenes.recognition.engine import detect_scene
from proximic_ring.scenes.models import PRESENTATION, PDF, IMAGE, VIDEO, MUSIC
from proximic_ring.scene_diagnostics import SceneActionError
from test_presentation_portability import native_backend
from test_activity_scenes import content, metadata
from test_gesture_scenes import window_tree
from test_app_shortcuts import desktop


def activation_snapshot(role='AXLayoutArea', focused=True):
    # Oct 5/8 traces: active ordinary document window with a disabled layout
    # canvas; on Oct 8 that AXFocusedUIElement also reported AXFocused=false.
    window = dict(AXRole='AXWindow', AXSubrole='AXStandardWindow', AXModal=False,
                  AXMinimized=False, AXSheets=[], AXFullScreen=False)
    focus = dict(AXRole=role, AXEnabled=False, AXFocused=focused, AXWindow=window,
                 AXParent=window, AXChildren=[])
    window['AXChildren'] = [focus]
    return window, focus


@pytest.mark.parametrize('bundle', ['com.microsoft.Powerpoint', 'com.kingsoft.wpsoffice.mac',
    'com.apple.iWork.Keynote', 'com.apple.Preview', 'com.apple.QuickTimePlayerX',
    'com.apple.Music', 'com.openai.codex', 'test.unknown.application'])
@pytest.mark.parametrize('scene_capture', [False, True])
@pytest.mark.parametrize('focused', [True, False])
def test_all_app_window_shortcuts_work_from_disabled_canvas_and_revalidate(monkeypatch, bundle, scene_capture, focused):
    window, focus = activation_snapshot(focused=focused)
    backend, _, _ = native_backend(monkeypatch, window, focus, bundle=bundle)
    target = backend.capture(menu_action=True, scene=scene_capture)
    assert target is not None and not target.blocked
    assert target.diagnostic['focus_policy'] == 'window_shortcut'
    assert target.diagnostic['disabled_containers'] == ['AXLayoutArea']
    assert target.diagnostic.get('unfocused_containers', []) == ([] if focused else ['AXLayoutArea'])
    assert backend.same_target(target)
    # Selecting a thumbnail was why the same command started working later.
    focus.update(AXRole='AXList', AXEnabled=True, AXFocused=True)
    enabled = backend.capture(menu_action=True, scene=scene_capture)
    assert not enabled.blocked and backend.same_target(enabled)


@pytest.mark.parametrize('role', sorted(WINDOW_SHORTCUT_CONTAINERS))
@pytest.mark.parametrize('focused,enabled', [(True, False), (False, False), (False, True)])
def test_container_flag_is_advisory_only_for_proven_window_commands(role, focused, enabled):
    window, focus = activation_snapshot(role, focused)
    focus['AXEnabled'] = enabled
    strict = inspect_focus(window, focus, metadata)
    assert strict.blocked and strict.context == 'unknown'
    assert strict.reason == ('stale_focus' if enabled else 'disabled_focus')
    # Browser recognition's special web-root policy must not relax native canvases.
    assert inspect_focus(window, focus, metadata, page_focus=True).blocked
    snapshot = inspect_focus(window, focus, metadata, window_shortcut=True)
    assert not snapshot.blocked and snapshot.context == 'nontext'
    assert snapshot.ancestors[-1] is window
    assert snapshot.disabled_containers == (() if enabled else (role,))
    assert snapshot.unfocused_containers == (() if focused else (role,))


@pytest.mark.parametrize('role', ['AXTextField', 'AXTextArea', 'AXSearchField', 'AXComboBox',
    'AXButton', 'AXCheckBox', 'AXSlider', 'AXList', 'AXWebArea', 'AXWindow', 'AXUnknown'])
def test_disabled_interactive_or_unknown_controls_and_windows_still_block(role):
    window, focus = activation_snapshot(role)
    assert inspect_focus(window, focus, metadata, window_shortcut=True).blocked


@pytest.mark.parametrize('focused', [True, False])
@pytest.mark.parametrize('state', ['hidden', 'busy', 'editable', 'is_editable',
    'menu', 'sheet', 'dialog', 'foreign_window', 'missing_parent', 'cycle', 'depth',
    'disabled_window', 'hidden_parent'])
def test_disabled_container_exception_cannot_skip_ownership_and_safety_checks(state, focused):
    window, focus = activation_snapshot(focused=focused)
    if state == 'hidden': focus['AXHidden'] = True
    if state == 'busy': focus['AXElementBusy'] = True
    if state == 'editable': focus['AXEditable'] = True
    if state == 'is_editable': focus['AXIsEditable'] = True
    if state == 'menu': focus['AXParent'] = dict(AXRole='AXMenu', AXParent=window)
    if state == 'sheet': focus['AXParent'] = dict(AXRole='AXSheet', AXParent=window)
    if state == 'dialog': focus['AXParent'] = dict(AXRole='AXGroup', AXSubrole='AXDialog', AXParent=window)
    if state == 'foreign_window': focus['AXParent'] = dict(AXRole='AXWindow')
    if state == 'missing_parent': focus.pop('AXParent'); focus.pop('AXWindow')
    if state == 'cycle': focus['AXParent'] = focus
    if state == 'depth':
        parent = window
        for _ in range(25): parent = dict(AXRole='AXGroup', AXParent=parent)
        focus['AXParent'] = parent
    if state == 'disabled_window': window['AXEnabled'] = False
    if state == 'hidden_parent': focus['AXParent'] = dict(AXRole='AXGroup', AXHidden=True, AXParent=window)
    assert inspect_focus(window, focus, metadata, window_shortcut=True).blocked


@pytest.mark.parametrize('state', ['modal', 'sheet', 'dialog', 'minimized', 'menu', 'foreign_window'])
def test_window_command_capture_keeps_real_window_guards(monkeypatch, state):
    window, focus = activation_snapshot()
    if state == 'modal': window['AXModal'] = True
    if state == 'sheet': window['AXSheets'] = [dict(AXRole='AXSheet')]
    if state == 'dialog': window['AXSubrole'] = 'AXDialog'
    if state == 'minimized': window['AXMinimized'] = True
    if state == 'menu': focus['AXParent'] = dict(AXRole='AXMenu', AXParent=window)
    if state == 'foreign_window': focus['AXParent'] = dict(AXRole='AXWindow')
    backend, _, _ = native_backend(monkeypatch, window, focus)
    assert backend.capture(menu_action=True).blocked


@pytest.mark.parametrize('scene', [PRESENTATION, PDF, IMAGE, VIDEO, MUSIC])
@pytest.mark.parametrize('focused,enabled', [(True, False), (False, False), (False, True)])
def test_scene_recognition_does_not_inherit_relaxed_window_command_policy(scene, focused, enabled):
    window, focus = window_tree() if scene == PRESENTATION else content(scene)
    focus.update(AXRole='AXLayoutArea', AXEnabled=enabled, AXFocused=focused)
    result = detect_scene('test.app', {scene:'generic'}, window, focus, metadata)
    assert result.input_context != 'nontext'


@pytest.mark.parametrize('focused', [True, False])
def test_send_from_log_snapshot_posts_one_balanced_command_and_stops_on_window_change(monkeypatch, desktop, focused):
    _, state, quartz = desktop
    window, focus = activation_snapshot(focused=focused)
    backend, root, _ = native_backend(monkeypatch, window, focus)
    target = backend.capture(menu_action=True)
    backend.post(target, 'Cmd+Return')
    assert [(pid, event['down'], event['code']) for pid,event in state.sent] == [(42,True,55),(42,True,36),(42,False,36),(42,False,55)]
    assert backend.last_diagnostic['validation']['observed']['disabled_containers'] == ['AXLayoutArea']
    other, other_focus = activation_snapshot()
    other['AXIdentifier'] = 'new-window'
    root.update(AXFocusedWindow=other, AXFocusedUIElement=other_focus)
    with pytest.raises(SceneActionError) as error: backend.post(target, 'Cmd+Return')
    assert error.value.reason == 'window_changed' and len(state.sent) == 4


from test_app_gestures import route
from test_application_menus import configure
from test_ring_gestures import request


@pytest.mark.parametrize('mode', ['input', 'operation'])
@pytest.mark.parametrize('bundle', ['com.microsoft.Powerpoint', 'com.kingsoft.wpsoffice.mac', 'test.unknown.application'])
@pytest.mark.parametrize('focused', [True, False])
@pytest.mark.parametrize('gesture', ['tap', 'snap'])
def test_real_gesture_route_sends_bound_window_command_without_clicking_another_control(route, desktop, monkeypatch, mode, bundle, focused, gesture):
    c, service, inline, _, _, messages, _ = route
    _, state, _ = desktop
    catalog = configure(service, monkeypatch, bundle)
    catalog.selectScene('regular')
    assert catalog.setCustomBinding(bundle, gesture, 'Window command', 'Cmd+Return')
    window, focus = activation_snapshot(focused=focused)
    service.backend, _, _ = native_backend(monkeypatch, window, focus, bundle=bundle)
    c.ringGestures._mode = mode
    before_voice = dict(inline._view)
    if request(c, gesture):
        c._apply_gesture(c.ringGestures.envelope(SimpleNamespace(name=gesture)), c._disconnect_event)
    assert len(state.sent) == 4 and all(pid == 42 for pid, _ in state.sent)
    assert [event['down'] for _,event in state.sent] == [True, True, False, False]
    assert not messages and inline._view == before_voice
    assert c.ringGestures.mode == mode
    outcome = service._diagnostics.recent(bundle)[-1]
    assert outcome['reason'] == 'shortcut_posted'
    assert outcome['native']['validation']['observed']['disabled_containers'] == ['AXLayoutArea']
