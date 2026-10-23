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
        assert key not in {'AXChildren', 'AXVisibleChildren', 'AXValue', 'AXSelectedText',
                          'AXSelectedTextRange', 'AXURL', 'AXDocument', 'AXTitle', 'AXDescription'}
        return original(node, key, unused)
    ax.AXUIElementCopyAttributeValue = metadata_only
    extension = {'reply': {'error': 'browser_disconnected'}}
    monkeypatch.setattr('proximic_ring.scenes.recognition.browser.request',
                        lambda bundle: extension['reply'])
    return backend, root, window, web, text, extension


@pytest.mark.parametrize('reply', [
    {'error': 'browser_disconnected'}, {'error': 'browser_timeout'},
    snapshot(authorized=False, input_context='nontext'),
    snapshot(focused=False, input_context='text'),
    snapshot(scene='', input_context='unknown', reason='browser_page_unavailable'),
    snapshot(input_context='nontext'),
])
@pytest.mark.parametrize('field', ['AXTextArea', 'AXTextField', 'AXSearchField',
                                  'AXComboBox', 'contenteditable', 'address_bar'])
def test_native_editor_keeps_text_focus_without_extension_or_website_permission(browser_focus, reply, field):
    backend, root, window, web, text, extension = browser_focus
    extension['reply'] = reply
    text['AXRole'] = 'AXGroup' if field == 'contenteditable' else 'AXTextField' if field == 'address_bar' else field
    if field == 'contenteditable':
        text['AXIsEditable'] = True
    if field == 'address_bar':
        text['AXParent'] = window
    target = backend.capture(menu_action=True, scene=True)
    assert not target.blocked and target.input_context == 'text'
    assert target.focus is text and target.role == text['AXRole']
    assert target.diagnostic['focus_source'] == 'accessibility'
    assert backend.same_target(target, require_focus=True)
    if not reply.get('authorized') or not reply.get('focused'):
        assert not target.scene  # Input recovery must not invent a media scene.


@pytest.mark.parametrize('context', ['text', 'nontext'])
def test_extension_still_refines_a_web_root_and_retains_media_identity(browser_focus, context):
    backend, root, window, web, text, extension = browser_focus
    web['AXFocused'] = False  # WebKit reports its page root after tab navigation.
    root['AXFocusedUIElement'] = web
    extension['reply'] = snapshot(input_context=context)
    target = backend.capture(menu_action=True, scene=True)
    assert not target.blocked and target.input_context == context
    assert target.scene == 'video' and target.browser_target == extension['reply']
    assert target.diagnostic['focus_source'] == 'extension'


def test_toolbar_focus_is_not_overridden_by_old_dom_text_focus(browser_focus):
    backend, root, window, web, text, extension = browser_focus
    root['AXFocusedUIElement'] = dict(AXRole='AXButton', AXParent=window, AXWindow=window)
    extension['reply'] = snapshot(input_context='text')
    target = backend.capture(menu_action=True, scene=True)
    assert not target.blocked and target.input_context == 'nontext'
    assert target.diagnostic['focus_source'] == 'accessibility'


@pytest.mark.parametrize('change', ['hidden', 'disabled', 'stale', 'busy', 'menu',
                                  'dialog', 'foreign_window', 'missing_parent'])
def test_invalid_native_focus_cannot_be_rescued_by_the_extension(browser_focus, change):
    backend, root, window, web, text, extension = browser_focus
    extension['reply'] = snapshot(input_context='text')
    if change in {'hidden', 'disabled', 'stale', 'busy'}:
        key = {'hidden': 'AXHidden', 'disabled': 'AXEnabled',
               'stale': 'AXFocused', 'busy': 'AXElementBusy'}[change]
        text[key] = change in {'hidden', 'busy'}
    elif change == 'menu':
        text['AXParent'] = dict(AXRole='AXMenu', AXParent=window)
    elif change == 'dialog':
        text['AXParent'] = dict(AXRole='AXGroup', AXSubrole='AXDialog', AXParent=window)
    elif change == 'foreign_window':
        foreign = dict(AXRole='AXWindow', AXIdentifier='other')
        text.update(AXParent=foreign, AXWindow=foreign)
    else:
        text.pop('AXParent')
        text.pop('AXWindow')
    target = backend.capture(menu_action=True, scene=True)
    assert target.blocked or target.input_context == 'unknown'


@pytest.mark.parametrize('change', ['focus', 'window', 'foreground', 'during_capture'])
def test_voice_start_rechecks_the_original_native_target(browser_focus, monkeypatch, change):
    backend, root, window, web, text, extension = browser_focus
    target = backend.capture(menu_action=True, scene=True)
    if change == 'focus':
        root['AXFocusedUIElement'] = dict(text, AXIdentifier='second-editor')
    elif change == 'window':
        root['AXFocusedWindow'] = dict(window, AXIdentifier='second-window')
    elif change == 'foreground':
        monkeypatch.setattr('proximic_ring.platforms.macos.mac_workspace.frontmost_application',
            lambda: SimpleNamespace(bundleIdentifier=lambda: target.bundle, processIdentifier=lambda: 99,
                                    localizedName=lambda: 'Browser'))
    else:
        def switch(bundle):
            root['AXFocusedUIElement'] = dict(text, AXIdentifier='second-editor')
            return extension['reply']
        monkeypatch.setattr('proximic_ring.scenes.recognition.browser.request', switch)
    assert not backend.same_target(target, require_focus=True)


def test_shared_voice_gesture_routes_to_input_without_media_extension(browser_focus):
    backend, root, window, web, text, extension = browser_focus
    from proximic_ring.voice.ime.focus_probe import InputFocus
    target = backend.capture(menu_action=True, scene=True)
    proof = InputFocus('text', ('e', 'c', '', target.bundle, target.pid, (0, 0)))
    current = {'proof': proof}
    inline = SimpleNamespace(capture_start_focus=lambda: current['proof'],
                             same_gesture_target=lambda value: value == current['proof'].target)
    owner = SimpleNamespace(_inline_input=inline, _gesture_bindings=SimpleNamespace(confirm=('tap',)),
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
