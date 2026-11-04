"""Browser input focus is independent of optional media-extension access."""
import sys
from types import SimpleNamespace

import pytest

from proximic_ring.ui.gestures.app_gesture_controller import AppGestureController
from test_browser_bridge import snapshot
from test_presentation_portability import native_backend


@pytest.fixture(params=['com.apple.Safari', 'com.google.Chrome'])
def browser_focus(monkeypatch, request):
    window = dict(AXRole='AXWindow', AXSubrole='AXStandardWindow')
    web = dict(AXRole='AXWebArea', AXEditable=False, AXParent=window)
    text = dict(AXRole='AXTextArea', AXFocused=True, AXParent=web, AXWindow=window)
    backend, root, reads = native_backend(monkeypatch, window, text, bundle=request.param)
    ax = sys.modules['ApplicationServices']
    original = ax.AXUIElementCopyAttributeValue
    def metadata_only(node, key, unused):
        assert key not in {'AXFocusedUIElement', 'AXParent', 'AXFocused', 'AXWindow', 'AXChildren', 'AXVisibleChildren', 'AXValue', 'AXSelectedText',
                          'AXSelectedTextRange', 'AXURL', 'AXDocument', 'AXTitle', 'AXDescription'}
        return original(node, key, unused)
    ax.AXUIElementCopyAttributeValue = metadata_only
    extension = {'reply': {'error': 'browser_disconnected'}}
    monkeypatch.setattr('proximic_ring.scenes.recognition.browser.request',
                        lambda bundle: extension['reply'])
    return backend, root, window, web, text, extension


@pytest.mark.parametrize('context', ['text', 'nontext', 'unknown'])
def test_browser_context_comes_only_from_extension(browser_focus, context):
    backend, root, window, web, text, extension = browser_focus
    extension['reply'] = snapshot(input_context=context)
    text.pop('AXParent'); text['AXFocused'] = False
    target = backend.capture(menu_action=True, scene=True)
    assert not target.blocked and target.input_context == context
    assert target.scene == 'video' and target.browser_target == extension['reply']
    assert target.diagnostic['focus_source'] == 'extension'
    assert target.focus is None and not target.focus_sensitive


@pytest.mark.parametrize('reply', [
    {'error': 'browser_disconnected'}, {'error': 'browser_timeout'},
    snapshot(authorized=False), snapshot(focused=False),
])
def test_extension_failure_does_not_block_regular_browser_commands(browser_focus, reply):
    backend, root, window, web, text, extension = browser_focus
    extension['reply'] = reply
    target = backend.capture(menu_action=True, scene=True)
    assert not target.blocked and not target.scene and target.input_context == 'unknown'
    assert backend.same_target(target)


@pytest.mark.parametrize('change', ['window', 'foreground', 'page'])
def test_browser_action_rechecks_target_identity(browser_focus, monkeypatch, change):
    backend, root, window, web, text, extension = browser_focus
    extension['reply'] = snapshot()
    target = backend.capture(menu_action=True, scene=True)
    if change == 'window':
        root['AXFocusedWindow'] = dict(window, AXIdentifier='second-window')
    elif change == 'foreground':
        monkeypatch.setattr('proximic_ring.platforms.macos.mac_workspace.frontmost_application',
            lambda: SimpleNamespace(bundleIdentifier=lambda: target.bundle, processIdentifier=lambda: 99,
                                    localizedName=lambda: 'Browser'))
    else:
        extension['reply'] = snapshot(page='new-page')
    assert not backend.same_target(target)


def test_shared_voice_gesture_routes_to_input_without_media_extension(browser_focus):
    backend, root, window, web, text, extension = browser_focus
    from proximic_ring.voice.ime.focus_probe import InputFocus
    from proximic_ring.gestures.gesture_settings import GestureBindings
    target = backend.capture(menu_action=True, scene=True)
    proof = InputFocus('text', ('e', 'c', '', target.bundle, target.pid, (0, 0)))
    current = {'proof': proof}
    inline = SimpleNamespace(capture_start_focus=lambda **_: current['proof'],
                             same_gesture_target=lambda value: value == current['proof'].target)
    owner = SimpleNamespace(_inline_input=inline, _gesture_bindings=GestureBindings(confirm=('tap',)),
                            _ring_gestures=SimpleNamespace(speech_busy=lambda: False))
    target = backend.capture(menu_action=True, scene=True)
    service = SimpleNamespace(owner=owner, backend=backend, _generation=1, _source_gesture='',
        input_gestures=lambda bindings: {'tap'}, phase_state=lambda: dict(phase='idle', composing=False,
            writing=False, edit_requested=False), sentence=lambda: ('', '', ''),
        _trace=lambda *a, **kw: None, _capture_trace=lambda *a: '',
        _override_stop_reason=lambda event: '', _stop_trace=lambda *a: None,
        foreground_identity=lambda: (target.bundle, target.pid),
        _catalog=SimpleNamespace(uses_scene=lambda name, **kw: True, configured_scenes=lambda: {}))
    event = AppGestureController.envelope(service, SimpleNamespace(name='tap'))
    assert event.route == 'input' and not event.exclusive and event.input_method_target == proof.target
    assert AppGestureController.input_event_valid(service, event)
    current['proof'] = InputFocus('text', ('e', 'second-editor', '', target.bundle, target.pid, (0, 0)))
    assert not AppGestureController.input_event_valid(service, event)


@pytest.mark.parametrize('state', ['authorized', 'focused'])
def test_unavailable_extension_cannot_claim_input_focus(state):
    from proximic_ring.scenes.recognition.browser import detect_browser
    result = detect_browser('com.apple.Safari', profiles={'video': 'browser'},
                            snapshot=snapshot(**{state: False}, input_context='text'))
    assert result.input_context == 'unknown' and not result.scene
