"""Application gestures use window identity and IMK, never AX focus ancestry."""
from dataclasses import replace
from types import SimpleNamespace
import sys
import pytest
from proximic_ring.diagnostics.scene_diagnostics import SceneActionError, reason_message
from proximic_ring.scenes.models import PRESENTATION
from test_presentation_portability import native_backend
from test_app_shortcuts import desktop
from test_app_gestures import route
from test_application_menus import configure
from test_ring_gestures import request
from test_gesture_scenes import presentation


def activation_snapshot(role='AXLayoutArea', focused=True):
    window = dict(AXRole='AXWindow', AXSubrole='AXStandardWindow', AXModal=False,
                  AXMinimized=False, AXSheets=[], AXFullScreen=False)
    focus = dict(AXRole=role, AXEnabled=False, AXFocused=focused, AXWindow=window,
                 AXParent=window, AXChildren=[])
    window['AXChildren'] = [focus]
    return window, focus


@pytest.mark.parametrize('bundle', ['com.microsoft.Powerpoint', 'com.kingsoft.wpsoffice.mac',
    'com.apple.iWork.Keynote', 'com.apple.Preview', 'com.openai.codex', 'test.unknown.application',
    'com.apple.Safari', 'com.google.Chrome'])
@pytest.mark.parametrize('scene', [False, True])
@pytest.mark.parametrize('role', ['AXWebArea', 'AXTextArea', 'AXMenu', 'AXUnknown'])
def test_window_commands_never_read_or_prepare_ax_focus(monkeypatch, bundle, scene, role):
    monkeypatch.setattr('proximic_ring.scenes.recognition.browser.request',
                        lambda _: {'error': 'browser_disconnected'})
    window, focus = activation_snapshot(role, focused=False)
    focus.pop('AXParent'); focus.pop('AXWindow')
    backend, root, _ = native_backend(monkeypatch, window, focus, bundle=bundle)
    ax = sys.modules['ApplicationServices']; original = ax.AXUIElementCopyAttributeValue
    def read(node, key, unused):
        assert key not in {'AXFocusedUIElement', 'AXParent', 'AXFocused', 'AXWindow'}
        return original(node, key, unused)
    monkeypatch.setattr(ax, 'AXUIElementCopyAttributeValue', read)
    monkeypatch.setattr(ax, 'AXUIElementSetAttributeValue', lambda *a: pytest.fail('AX warmup'), raising=False)
    target = backend.capture(menu_action=True, scene=scene)
    assert target and not target.blocked and not target.focus_sensitive
    assert target.focus is None and target.input_context == 'unknown'
    root['AXFocusedUIElement'] = None
    assert backend.same_target(target, require_focus=True)


@pytest.mark.parametrize('state', ['modal', 'sheet', 'dialog', 'minimized'])
def test_window_command_keeps_actual_window_state(monkeypatch, state):
    window, focus = activation_snapshot()
    window[{'modal':'AXModal', 'sheet':'AXSheets', 'dialog':'AXSubrole', 'minimized':'AXMinimized'}[state]] = (
        [dict(AXRole='AXSheet')] if state == 'sheet' else 'AXDialog' if state == 'dialog' else True)
    backend, _, _ = native_backend(monkeypatch, window, focus)
    assert backend.capture(menu_action=True).blocked


def test_command_survives_focus_loss_but_rejects_window_change(monkeypatch, desktop):
    _, state, _ = desktop
    window, focus = activation_snapshot()
    backend, root, _ = native_backend(monkeypatch, window, focus)
    target = backend.capture(menu_action=True)
    root['AXFocusedUIElement'] = None
    backend.post(target, 'Cmd+Return')
    assert len(state.sent) == 4
    root['AXFocusedWindow'] = dict(window, AXIdentifier='new-window')
    with pytest.raises(SceneActionError) as error: backend.post(target, 'Cmd+Return')
    assert error.value.reason == 'window_changed' and len(state.sent) == 4


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
    # This native backend has no synthetic .target for route's IMK fixture.
    # Supply the live nontext client independently of the AX window snapshot.
    from proximic_ring.voice.ime.focus_probe import InputFocus
    proof = InputFocus('nontext', ('e', 'c', 'u', bundle, 42, (-1, -1)))
    monkeypatch.setattr(inline, 'capture_start_focus', lambda **_: proof)
    monkeypatch.setattr(inline, 'same_input_focus', lambda current: current == proof)
    c.ringGestures._mode = mode
    before_voice = dict(inline._view)
    if request(c, gesture):
        c._apply_gesture(c.ringGestures.envelope(SimpleNamespace(name=gesture)), c._disconnect_event)
    # Recorded shortcuts go through macOS hotkey handling after the target window is verified.
    assert [destination for destination, _ in state.sent] == ['hid'] * 4
    assert [event['down'] for _,event in state.sent] == [True, True, False, False]
    assert not messages and inline._view == before_voice
    assert c.ringGestures.mode == mode
    outcome = service._diagnostics.recent(bundle)[-1]
    assert outcome['reason'] == 'shortcut_posted'
    validation = outcome['native']['validation']
    assert validation['expected_pid'] == validation['observed_pid'] == 42
    observed = validation['observed']
    assert not observed['focus_sensitive']  # IMK owns text arbitration; AX still guards the window.
    assert 'disabled_containers' not in observed


@pytest.mark.parametrize('gesture', ['swipe-left', 'swipe-right'])
@pytest.mark.parametrize('scope', ['application', 'global'])
def test_codex_missing_focus_blocks_shortcuts_then_editor_wins_and_blur_restores_config(route, desktop, monkeypatch, gesture, scope):
    c, service, inline, _, _, messages, _ = route
    _, state, _ = desktop
    bundle = 'com.openai.codex'
    starts = []
    if scope == 'application':
        assert configure(service, monkeypatch, bundle).setCustomBinding(bundle, gesture, '切换对话', 'Cmd+Shift+]')
    else:
        assert c.ringGestures.setGlobalBinding('window_selector', gesture)
        monkeypatch.setattr(c.ringGestures._selector, 'start', lambda *args: starts.append(True))
    window, editor = activation_snapshot('AXTextArea')
    editor['AXEnabled'] = True
    service.backend, root, _ = native_backend(monkeypatch, window, None, bundle=bundle)
    from proximic_ring.voice.ime.focus_probe import InputFocus
    current = {'proof': InputFocus('unavailable', reason='input_method_not_ready')}
    monkeypatch.setattr(inline, 'capture_start_focus', lambda **_: current['proof'])
    monkeypatch.setattr(inline, 'same_input_focus', lambda proof: proof == current['proof'])
    monkeypatch.setattr(inline, 'same_gesture_target', lambda target: target == current['proof'].target)
    request(c, gesture)
    assert not state.sent and not starts
    assert service.notice == reason_message('input_method_not_ready')
    root['AXFocusedUIElement'] = editor
    current['proof'] = InputFocus('text', ('e', 'c', 'u', bundle, 42, (0, 0)))
    request(c, gesture)  # No undo/convert available; never fall through.
    assert not state.sent and not starts and not messages
    root['AXFocusedUIElement'] = dict(AXRole='AXGroup', AXFocused=True, AXWindow=window, AXParent=window)
    current['proof'] = InputFocus('nontext', ('e', 'c', 'u', bundle, 42, (-1, -1)))
    request(c, gesture)
    assert bool(state.sent) == (scope == 'application')
    assert bool(starts) == (scope == 'global')


@pytest.mark.parametrize('gesture', ['clench', 'snap'])
def test_nonconflicting_scene_binding_runs_with_text_focus(presentation, monkeypatch, gesture):
    c, service, _, backend, catalog, sent, _ = presentation
    bundle = backend.target.bundle
    catalog.selectScene(PRESENTATION)
    assert catalog.setBinding(bundle, gesture, 'powerpoint:next')
    backend.target = replace(backend.target, input_context='text', role='AXTextArea', focus='notes')
    assert not request(c, gesture)
    assert sent == [(bundle, 'Right')]
