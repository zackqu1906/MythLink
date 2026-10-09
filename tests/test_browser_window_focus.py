"""Tab/window commands remain usable after a browser reports an unfocused web root."""
from types import SimpleNamespace
import pytest

from proximic_ring.scenes.recognition.focus import inspect_focus
from proximic_ring.scene_diagnostics import SceneActionError
from test_activity_scenes import metadata
from test_presentation_portability import native_backend
from test_app_shortcuts import desktop
from test_app_gestures import route
from test_application_menus import configure
from test_ring_gestures import request

BROWSERS = ['com.apple.Safari', 'com.google.Chrome', 'org.mozilla.firefox',
            'com.microsoft.edgemac', 'com.brave.Browser', 'test.unknown.browser']


def document_window():
    window = dict(AXRole='AXWindow', AXSubrole='AXStandardWindow', AXModal=False,
                  AXMinimized=False, AXSheets=[], AXFullScreen=False)
    # Current AXFocusedUIElement after Ctrl+Tab in the reported Safari trace.
    web = dict(AXRole='AXWebArea', AXFocused=False, AXEnabled=True,
               AXEditable=False, AXIsEditable=False, AXParent=window, AXWindow=window,
               AXURL='https://example.test/page', AXChildren=[])
    window['AXChildren'] = [web]
    return window, web


def test_focused_page_object_need_not_itself_be_first_responder_for_window_commands():
    window, web = document_window()
    strict = inspect_focus(window, web, metadata)
    assert strict.blocked and strict.reason == 'stale_focus'
    command = inspect_focus(window, web, metadata, window_shortcut=True)
    assert not command.blocked and command.ancestors[-1] is window
    assert command.unfocused_containers == ('AXWebArea',)


@pytest.mark.parametrize('settable', [False, None])
def test_optional_web_editability_attributes_need_not_be_supported(settable):
    window, web = document_window()
    web.pop('AXEditable')
    web.pop('AXIsEditable')
    web['AXValueSettable'] = settable
    command = inspect_focus(window, web, metadata, window_shortcut=True)
    assert not command.blocked and command.ancestors[-1] is window
    assert command.unfocused_containers == ('AXWebArea',)
    assert inspect_focus(window, web, metadata).blocked


@pytest.mark.parametrize('state', ['hidden', 'disabled', 'busy', 'editable', 'is_editable',
    'missing_parent', 'foreign_window', 'menu', 'sheet', 'dialog', 'hidden_parent',
    'disabled_parent', 'cycle', 'depth', 'disabled_window'])
def test_unfocused_web_root_never_bypasses_real_blockers_or_incomplete_ownership(state):
    window, web = document_window()
    if state == 'hidden': web['AXHidden'] = True
    if state == 'disabled': web['AXEnabled'] = False
    if state == 'busy': web['AXElementBusy'] = True
    if state == 'editable': web['AXEditable'] = True
    if state == 'is_editable': web['AXIsEditable'] = True
    if state == 'missing_parent': web.pop('AXParent')  # AXWindow alone must not stand in for page ownership.
    if state == 'foreign_window': web['AXParent'] = dict(AXRole='AXWindow')
    if state == 'menu': web['AXParent'] = dict(AXRole='AXMenu', AXParent=window)
    if state == 'sheet': web['AXParent'] = dict(AXRole='AXSheet', AXParent=window)
    if state == 'dialog': web['AXParent'] = dict(AXRole='AXGroup', AXSubrole='AXDialog', AXParent=window)
    if state == 'hidden_parent': web['AXParent'] = dict(AXRole='AXGroup', AXHidden=True, AXParent=window)
    if state == 'disabled_parent': web['AXParent'] = dict(AXRole='AXWebArea', AXEnabled=False, AXParent=window)
    if state == 'cycle': web['AXParent'] = web
    if state == 'depth':
        parent=window
        for _ in range(25): parent=dict(AXRole='AXGroup', AXParent=parent)
        web['AXParent']=parent
    if state == 'disabled_window': window['AXEnabled'] = False
    assert inspect_focus(window, web, metadata, window_shortcut=True).blocked


@pytest.mark.parametrize('role', ['AXTextField','AXTextArea','AXSearchField','AXComboBox',
    'AXButton','AXCheckBox','AXSlider','AXList','AXWindow','AXUnknown'])
def test_stale_controls_do_not_get_web_container_exception(role):
    window, focus = document_window(); focus['AXRole'] = role
    assert inspect_focus(window, focus, metadata, window_shortcut=True).blocked


@pytest.mark.parametrize('bundle', BROWSERS)
def test_repeated_tab_navigation_posts_after_leaving_text_field_in_all_browsers(monkeypatch, desktop, bundle):
    _, state, _ = desktop
    window, web = document_window()
    text = dict(AXRole='AXTextArea', AXFocused=True, AXEnabled=True, AXParent=web, AXWindow=window)
    backend, root, _ = native_backend(monkeypatch, window, text, bundle=bundle)
    initial = backend.capture(menu_action=True)
    backend.post(initial, 'Ctrl+Shift+Tab')
    root['AXFocusedUIElement'] = web
    for shortcut in ['Ctrl+Tab', 'Ctrl+Shift+Tab', 'Ctrl+Tab']:
        target = backend.capture(menu_action=True)
        assert not target.blocked and target.diagnostic['unfocused_containers'] == ['AXWebArea']
        backend.post(target, shortcut)
    assert len(state.sent) == 20 and all(pid == 42 for pid,_ in state.sent)
    assert [event['down'] for _,event in state.sent if event['code']==48] == [True,False]*4
    assert backend.last_diagnostic['validation']['observed']['unfocused_containers'] == ['AXWebArea']


@pytest.mark.parametrize('change', ['app', 'pid', 'window', 'menu', 'disabled', 'missing_parent'])
def test_switch_between_capture_and_post_still_cancels_without_posting(monkeypatch, desktop, change):
    import proximic_ring.mac_workspace as workspace
    _, state, _ = desktop
    window, web = document_window()
    backend, root, _ = native_backend(monkeypatch, window, web, bundle='com.apple.Safari')
    target = backend.capture(menu_action=True)
    if change in {'app','pid'}:
        app=SimpleNamespace(bundleIdentifier=lambda:'other.app' if change=='app' else 'com.apple.Safari',
            processIdentifier=lambda:99 if change=='pid' else 42, localizedName=lambda:'Browser')
        monkeypatch.setattr(workspace,'frontmost_application',lambda:app)
    if change == 'window':
        other, focus = document_window(); other['AXIdentifier']='other'
        root.update(AXFocusedWindow=other,AXFocusedUIElement=focus)
    if change == 'menu': web['AXParent'] = dict(AXRole='AXMenu',AXParent=window)
    if change == 'disabled': web['AXEnabled'] = False
    if change == 'missing_parent': web.pop('AXParent')
    with pytest.raises(SceneActionError): backend.post(target,'Ctrl+Tab')
    assert not state.sent


@pytest.mark.parametrize('mode',['input','operation'])
@pytest.mark.parametrize('bundle',['com.apple.Safari','com.google.Chrome','org.mozilla.firefox'])
def test_ring_route_can_switch_back_and_forth_without_refocusing_page(route, desktop, monkeypatch, mode, bundle):
    c, service, inline, _, _, messages, _ = route
    _, state, _ = desktop
    catalog=configure(service,monkeypatch,bundle)
    catalog.selectScene('regular')
    assert catalog.setCustomBinding(bundle,'circle-clockwise','Next tab','Ctrl+Tab')
    assert catalog.setCustomBinding(bundle,'circle-counterclockwise','Previous tab','Ctrl+Shift+Tab')
    window, web = document_window()
    service.backend, _, _ = native_backend(monkeypatch,window,web,bundle=bundle)
    c.ringGestures._mode=mode
    original=dict(inline._view)
    for name in ['circle-clockwise','circle-counterclockwise','circle-clockwise','circle-counterclockwise']:
        if request(c,name):
            c._apply_gesture(c.ringGestures.envelope(SimpleNamespace(name=name)),c._disconnect_event)
    assert len(state.sent)==20 and not messages and inline._view==original
    assert [event['down'] for _,event in state.sent if event['code']==48]==[True,False]*4
    assert service._diagnostics.recent(bundle)[-1]['reason']=='shortcut_posted'
    assert c.ringGestures.mode==mode
