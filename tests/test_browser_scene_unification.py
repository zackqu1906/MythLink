"""Web content uses shared scene types, defaults, focus and dispatch checks."""
from dataclasses import replace
import json
import pytest
from PySide6.QtCore import QCoreApplication
from proximic_ring.scenes.recognition.engine import detect_scene
from proximic_ring.scene_capabilities import application_scene_profiles, BROWSERS, PDF, VIDEO, MUSIC
from proximic_ring.application_scene_policy import enabled_profiles
from proximic_ring.scene_defaults import scene_defaults
from proximic_ring.ui.desktop.application_mapping_controller import ApplicationMappingController
from test_scene_defaults import add
from test_presentation_portability import native_backend
from test_app_gestures import route
from test_gesture_scenes import presentation
from test_ring_gestures import request


class Node:
    def __init__(self, role, parent=None, **attrs):
        self.attrs=dict(AXRole=role, AXParent=parent, **attrs)
        if parent is not None:parent.attrs.setdefault('AXChildren',[]).append(self)


def read(node,key):
    assert key not in {'AXValue','AXSelectedText','AXSelectedTextRange'}
    assert key != 'AXTitle' or node.attrs['AXRole'] in {'AXButton', 'AXCheckBox', 'AXSlider', 'AXValueIndicator'}
    return node.attrs.get(key)


def page(url='https://example.test/watch'):
    window=Node('AXWindow')
    web=Node('AXWebArea',window,AXURL=url,AXEditable=False,AXEnabled=True)
    return window,web


def player(web,kind=VIDEO,wrapper=False):
    host=Node('AXGroup',web,AXDOMClassList=['video-js']) if wrapper else web
    media=Node('AXGroup',host,AXSubrole='AXVideo' if kind==VIDEO else 'AXAudio',AXEnabled=True)
    controls=host if wrapper else media
    Node('AXButton',controls,AXDescription='Pause',AXEnabled=True)
    Node('AXSlider',controls,AXDescription='Playback progress',AXEnabled=True)
    return media


def scene(window,focus,bundle='com.apple.Safari',**kwargs):
    return detect_scene(bundle,application_scene_profiles(bundle),window,focus,read,**kwargs)


@pytest.mark.parametrize('bundle',sorted(BROWSERS))
def test_new_browser_has_video_and_audio_defaults_but_explicit_disable_is_durable(bundle):
    profiles=application_scene_profiles(bundle)
    defaults=enabled_profiles(bundle,profiles)
    assert set(defaults)=={VIDEO,MUSIC}
    records,pending=scene_defaults(bundle,defaults)
    assert not pending
    assert records[VIDEO]['tap']['id']=='web-video:play'
    assert records[MUSIC]['tap']['id']=='scene-default:music:play'
    assert records[MUSIC]['swipe-up']['id']=='scene-default:music:volume-up'
    assert enabled_profiles(bundle,profiles,enabled=[])=={}
    assert set(enabled_profiles(bundle,profiles,enabled=[VIDEO]))=={VIDEO}


def test_current_page_scene_selects_bindings_and_switches_cancel_queued_gestures(presentation,monkeypatch):
    c,service,_,backend,_,sent,_=presentation
    bundle='com.apple.Safari';catalog=add(service,monkeypatch,bundle)
    assert catalog.addScene(PDF)  # Preserve explicitly configured legacy scopes.
    catalog.selectScene('regular')
    backend.target=replace(backend.target,bundle=bundle,scene=VIDEO,input_context='nontext',page_key='video-page')
    assert not request(c,'swipe-right') and sent==[(bundle,'Right')]
    assert not request(c,'swipe-right',deliver=False)
    backend.target=replace(backend.target,scene=PDF,page_key='pdf-page')
    QCoreApplication.processEvents()
    assert sent==[(bundle,'Right')]
    assert not request(c,'swipe-right') and sent[-1]==(bundle,'PageDown')
    count=len(sent)
    backend.target=replace(backend.target,input_context='text')
    request(c,'swipe-right')
    assert len(sent)==count


def test_pdf_defaults_survive_restart_and_clearing_does_not_reinstall(route,monkeypatch):
    c,service,*_=route;bundle='com.apple.Safari';catalog=add(service,monkeypatch,bundle)
    assert catalog.configured_scenes()[bundle]==[VIDEO,MUSIC]
    assert catalog.addScene(PDF)
    catalog.selectScene(PDF)
    assert catalog.setCustomBinding(bundle,'swipe-left','我的翻页','Alt+Left')
    assert catalog.disableScene(VIDEO)
    assert catalog.disableScene(MUSIC)
    restored=ApplicationMappingController(service)
    try:
        assert restored.configured_scenes()[bundle]==[PDF]
        assert restored.for_scene(bundle,PDF)['scene:swipe-left'].shortcut=='Alt+Left'
        restored.selectApplication(bundle);restored.clearApplicationBindings(bundle)
        assert set(restored.for_scene(bundle,PDF)) == {"scene:circle-clockwise", "scene:circle-counterclockwise"}
        restored._upgrade_scene_defaults()
        assert set(restored.for_scene(bundle,PDF)) == {"scene:circle-clockwise", "scene:circle-counterclockwise"}
    finally:restored.close()
