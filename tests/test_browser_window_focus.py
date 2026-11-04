"""Browser window gestures never depend on a webpage's AX focus tree."""
from types import SimpleNamespace
import sys
import pytest
from proximic_ring.diagnostics.scene_diagnostics import SceneActionError
from test_presentation_portability import native_backend
from test_app_shortcuts import desktop
from test_app_gestures import route
from test_application_menus import configure
from test_ring_gestures import request

def document_window():
    window = dict(AXRole='AXWindow', AXSubrole='AXStandardWindow', AXModal=False,
                  AXMinimized=False, AXSheets=[], AXFullScreen=False)
    # Current AXFocusedUIElement after Ctrl+Tab in the reported Safari trace.
    web = dict(AXRole='AXWebArea', AXFocused=False, AXEnabled=True,
               AXEditable=False, AXIsEditable=False, AXParent=window, AXWindow=window,
               AXURL='https://example.test/page', AXChildren=[])
    window['AXChildren'] = [web]
    return window, web


@pytest.mark.parametrize('change', ['app', 'pid', 'window'])
def test_switch_between_capture_and_post_still_cancels_without_posting(monkeypatch, desktop, change):
    import proximic_ring.platforms.macos.mac_workspace as workspace
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


@pytest.mark.parametrize('bundle', ['com.apple.Safari', 'com.google.Chrome', 'org.mozilla.firefox'])
@pytest.mark.parametrize('failure', ['missing_parent', 'unavailable', 'disabled', 'cycle'])
def test_browser_tab_commands_ignore_ax_focus_failures(monkeypatch, desktop, bundle, failure):
    _, state, _ = desktop
    window, web = document_window()
    if failure == 'missing_parent': web.pop('AXParent'); web.pop('AXWindow')
    if failure == 'disabled': web['AXEnabled'] = False
    if failure == 'cycle': web['AXParent'] = web
    backend, root, _ = native_backend(monkeypatch, window, None if failure == 'unavailable' else web, bundle=bundle)
    ax = sys.modules['ApplicationServices']; original = ax.AXUIElementCopyAttributeValue
    def read(node, key, unused):
        assert key not in {'AXFocusedUIElement', 'AXParent', 'AXFocused', 'AXWindow'}
        return original(node, key, unused)
    monkeypatch.setattr(ax, 'AXUIElementCopyAttributeValue', read)
    target = backend.capture(menu_action=True)
    assert not target.blocked
    for shortcut in ['Ctrl+Tab', 'Ctrl+Shift+Tab']:
        backend.post(target, shortcut)
    assert len(state.sent) == 10


def test_safari_clench_with_broken_web_focus_opens_selector(route, monkeypatch):
    c, service, _, _, _, messages, _ = route
    window, web = document_window()
    web.pop('AXParent'); web.pop('AXWindow')
    service.backend, _, _ = native_backend(monkeypatch, window, web, bundle='com.apple.Safari')
    starts = []
    monkeypatch.setattr(c.ringGestures._selector, 'start', lambda *args: starts.append(True))
    assert not request(c, 'clench')
    assert starts == [True] and not messages
