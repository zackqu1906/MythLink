"""Browser capture is independent of the AX page tree and control labels."""
import sys
from dataclasses import replace
import pytest
from test_browser_bridge import snapshot
from test_presentation_portability import native_backend
from proximic_ring.diagnostics.scene_diagnostics import SceneActionError


@pytest.mark.parametrize('bundle', ['com.apple.Safari', 'com.google.Chrome'])
def test_native_boundary_reads_outer_window_only(monkeypatch,bundle):
    import proximic_ring.scenes.recognition.browser as module
    window={'AXRole':'AXWindow'}
    backend,root,_=native_backend(monkeypatch,window,None,bundle=bundle)
    monkeypatch.setattr(module,'request',lambda _:snapshot())
    ax=sys.modules['ApplicationServices']; original=ax.AXUIElementCopyAttributeValue
    def read(node,key,unused):
        assert key in {'AXFocusedWindow','AXModal','AXSheets','AXMinimized','AXSubrole'}
        return original(node,key,unused)
    ax.AXUIElementCopyAttributeValue=read
    ax.AXUIElementSetAttributeValue=lambda *_:pytest.fail('AX initialization is forbidden for browser scenes')
    target=backend.capture(scene=True,menu_action=True)
    assert target.scene=='video' and target.browser_target['media']=='player'
    assert backend.same_target(target)
    monkeypatch.setattr(module,'request',lambda _:snapshot(source='new-source'))
    assert not backend.same_target(target)


@pytest.mark.parametrize('field',['AXModal','AXSheets','AXMinimized'])
def test_window_safety_still_blocks_extension_actions(monkeypatch,field):
    import proximic_ring.scenes.recognition.browser as module
    window={'AXRole':'AXWindow',field:True}
    backend,_,_=native_backend(monkeypatch,window,None,bundle='com.apple.Safari')
    monkeypatch.setattr(module,'request',lambda _:snapshot())
    target=backend.capture(scene=True,menu_action=True)
    assert target.blocked
    with pytest.raises(SceneActionError):backend.browser_action(target,'play')


def test_browser_action_reports_ack_and_does_not_post_keys(monkeypatch):
    import proximic_ring.scenes.recognition.browser as module
    import proximic_ring.browser_bridge.client as client
    backend,_,reads=native_backend(monkeypatch,{'AXRole':'AXWindow'},None,bundle='com.google.Chrome')
    monkeypatch.setattr(module,'request',lambda _:snapshot())
    target=backend.capture(scene=True,menu_action=True)
    monkeypatch.setattr(backend, 'post', lambda *args, **kw: pytest.fail('keyboard path must not run'))
    calls=[]
    monkeypatch.setattr(client,'request',lambda *args,**kw:calls.append((args,kw)) or {'ok':True})
    assert backend.browser_action(target,'play')['delivery']=='acknowledged'
    assert calls[0][1]['target']==target.browser_target and len(calls)==1
    monkeypatch.setattr(client,'request',lambda *args,**kw:{'error':'browser_timeout'})
    with pytest.raises(SceneActionError) as error:backend.browser_action(target,'play')
    assert error.value.diagnostic['delivery']=='unknown'
