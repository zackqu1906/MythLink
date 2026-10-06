"""Web content uses shared scene types, defaults, focus and dispatch checks."""
from dataclasses import replace
import json
import pytest
from PySide6.QtCore import QCoreApplication
from proximic_ring.scene_recognition.engine import detect_scene
from proximic_ring.scene_recognition.browser import MAX_PAGE_NODES
from proximic_ring.scene_capabilities import application_scene_profiles, BROWSERS, PDF, VIDEO, MUSIC
from proximic_ring.application_scene_policy import enabled_profiles
from proximic_ring.scene_defaults import scene_defaults
from proximic_ring.ui.application_mapping_controller import ApplicationMappingController
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
    assert key not in {'AXValue','AXSelectedText','AXSelectedTextRange','AXTitle'}
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
@pytest.mark.parametrize('kind',[VIDEO,MUSIC])
@pytest.mark.parametrize('wrapper',[False,True])
def test_generic_loaded_player_is_detected_without_site_configuration(bundle,kind,wrapper):
    window,web=page();media=player(web,kind,wrapper)
    result=scene(window,web,bundle)
    assert (result.scene,result.input_context)==(kind,'nontext')
    assert result.player is media and result.web_area is web and result.page_key
    assert result.diagnostic['evidence']=='web_player_controls'
    assert len(result.diagnostic['attempts'])==1


@pytest.mark.parametrize('case',['text','address','outside_button','stale_root','stale_button','hidden','disabled','missing','modal'])
def test_browser_auto_mode_preserves_focus_boundaries(case):
    window,web=page();media=player(web);focus=web
    expected=(VIDEO,'nontext')
    if case=='text':focus=Node('AXTextArea',web);expected=(VIDEO,'text')
    if case=='address':focus=Node('AXTextField',window);expected=('', 'unknown')
    if case=='outside_button':focus=Node('AXButton',web);expected=(VIDEO,'unknown')
    if case=='stale_root':web.attrs['AXFocused']=False
    if case=='stale_button':focus=Node('AXButton',media,AXFocused=False);expected=('', 'unknown')
    if case=='hidden':media.attrs['AXHidden']=True;expected=('', 'nontext')
    if case=='disabled':media.attrs['AXEnabled']=False;expected=('', 'nontext')
    if case=='missing':focus=None;expected=('', 'unknown')
    if case=='modal':window.attrs['AXModal']=True;expected=('', 'unknown')
    result=scene(window,focus)
    assert (result.scene,result.input_context)==expected


@pytest.mark.parametrize('case',['no_player','disabled_play','no_seek','only_volume','split_controls','thumbnail','loading'])
def test_unconfirmed_media_never_activates_generic_mode(case):
    window,web=page();media=player(web)
    if case=='no_player':web.attrs['AXChildren']=[]
    if case=='disabled_play':media.attrs['AXChildren'][0].attrs['AXEnabled']=False
    if case=='no_seek':media.attrs['AXChildren']=media.attrs['AXChildren'][:1]
    if case=='only_volume':media.attrs['AXChildren'][1].attrs['AXDescription']='Volume'
    if case=='split_controls':
        slider=media.attrs['AXChildren'].pop();web.attrs['AXChildren'].append(slider);slider.attrs['AXParent']=web
    if case=='thumbnail':media.attrs['AXSubrole']='AXImage'
    if case=='loading':web.attrs['AXElementBusy']=True
    assert not scene(window,web).scene


def test_two_players_require_focus_to_choose_one():
    window,web=page();first=player(web);second=player(web)
    result=scene(window,web)
    assert not result.scene and result.diagnostic['reason']=='multiple_players'
    button=Node('AXButton',second)
    result=scene(window,button)
    assert result.scene==VIDEO and result.player is second


def test_scan_limit_does_not_guess_uniqueness_from_partial_page():
    window,web=page();first=player(web)
    for _ in range(MAX_PAGE_NODES + 10):Node('AXGroup',web)
    player(web)
    result=scene(window,web)
    assert not result.scene and result.diagnostic['reason']=='page_scan_incomplete'
    assert scene(window,Node('AXButton',first)).scene==VIDEO


@pytest.mark.parametrize('url',['https://example.test/file.pdf?token=secret','file:///private/example.pdf',
    'blob:https://example.test/id','chrome-extension://viewer/viewer.html','https://example.test/download/123'])
def test_pdf_url_or_real_surface_keeps_identity_without_reading_content(url):
    window,web=page(url)
    if '.pdf' not in url:Node('AXGroup',web,AXRoleDescription='PDF document')
    result=scene(window,web)
    assert (result.scene,result.input_context)==(PDF,'nontext')
    assert result.page_key and result.web_area is web
    assert 'secret' not in repr(result) and 'download/123' not in repr(result)


def test_pdfjs_uses_structural_markers_not_page_title():
    window,web=page('https://example.test/viewer?id=123')
    web.attrs['AXTitle']='PDF document'
    assert not scene(window,web).scene
    container=Node('AXGroup',web,AXDOMIdentifier='viewerContainer')
    viewer=Node('AXGroup',container,AXDOMIdentifier='viewer',AXDOMClassList=['pdfViewer'])
    assert scene(window,web).scene==PDF
    assert scene(window,Node('AXTextField',viewer)).input_context=='text'
    viewer.attrs['AXHidden']=True
    assert not scene(window,web).scene


def test_embedded_and_background_documents_do_not_override_active_page():
    window,web=page();frame=Node('AXWebArea',web,AXURL='https://example.test/embedded.pdf',AXEditable=False)
    other=Node('AXWebArea',window,AXURL='https://example.test/background.pdf',AXEditable=False)
    assert not scene(window,web).scene
    result=scene(window,frame)
    assert result.scene==PDF and result.web_area is frame
    second=Node('AXGroup',web,AXRoleDescription='PDF document')
    third=Node('AXGroup',web,AXRoleDescription='PDF document')
    assert not scene(window,web).scene
    assert scene(window,Node('AXGroup',second)).scene==PDF


def test_shared_native_pdf_surface_without_webarea_still_requires_owned_focus_and_identity():
    window=Node('AXWindow')
    document=Node('AXPDFDocument',window,AXURL='https://example.test/download?id=3')
    result=scene(window,document)
    assert result.scene==PDF and result.input_context=='nontext' and result.web_area is document
    document.attrs.pop('AXURL')
    assert not scene(window,document).scene


def test_browser_does_not_emit_a_scene_outside_its_declared_capabilities():
    window,web=page();player(web)
    result=detect_scene('com.apple.Safari',{PDF:'generic'},window,web,read)
    assert not result.scene and result.diagnostic['reason']=='unsupported_media_kind'


@pytest.mark.parametrize('bundle',sorted(BROWSERS))
def test_new_browser_has_video_and_pdf_defaults_but_explicit_disable_is_durable(bundle):
    profiles=application_scene_profiles(bundle)
    defaults=enabled_profiles(bundle,profiles)
    assert set(defaults)=={PDF,VIDEO}
    records,pending=scene_defaults(bundle,defaults)
    assert not pending and records[PDF]['swipe-left']['shortcut']=='PageUp'
    assert records[PDF]['swipe-right']['shortcut']=='PageDown'
    assert enabled_profiles(bundle,profiles,enabled=[])=={}
    assert set(enabled_profiles(bundle,profiles,enabled=[VIDEO]))=={VIDEO}


def test_current_page_scene_selects_bindings_and_switches_cancel_queued_gestures(presentation,monkeypatch):
    c,service,_,backend,_,sent,_=presentation
    bundle='com.apple.Safari';catalog=add(service,monkeypatch,bundle)
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


def test_document_identity_fallback_is_revalidated_during_capture(monkeypatch):
    import sys
    window={'AXRole':'AXWindow'}
    focus={'AXRole':'AXWebArea','AXEditable':False,'AXParent':window,'AXDocument':'https://example.test/one.pdf'}
    window['AXChildren']=[focus]
    backend,_,_=native_backend(monkeypatch,window,focus,bundle='com.apple.Safari')
    ax=sys.modules['ApplicationServices'];original=ax.AXUIElementCopyAttributeValue
    def changing(node,key,unused):
        result=original(node,key,unused)
        if node is focus and key=='AXDocument':focus['AXDocument']='https://example.test/two.pdf'
        return result
    monkeypatch.setattr(ax,'AXUIElementCopyAttributeValue',changing)
    assert backend.capture(menu_action=True,scene=True) is None
    assert backend.last_diagnostic['reason']=='page_changed'


def test_pdf_defaults_survive_restart_and_clearing_does_not_reinstall(route,monkeypatch):
    c,service,*_=route;bundle='com.apple.Safari';catalog=add(service,monkeypatch,bundle)
    assert catalog.configured_scenes()[bundle]==[PDF,VIDEO]
    catalog.selectScene(PDF)
    assert catalog.setCustomBinding(bundle,'swipe-left','我的翻页','Alt+Left')
    assert catalog.disableScene(VIDEO)
    restored=ApplicationMappingController(service)
    try:
        assert restored.configured_scenes()[bundle]==[PDF]
        assert restored.for_scene(bundle,PDF)['scene:swipe-left'].shortcut=='Alt+Left'
        restored.selectApplication(bundle);restored.clearApplicationBindings(bundle)
        assert set(restored.for_scene(bundle,PDF)) == {"scene:circle-clockwise", "scene:circle-counterclockwise"}
        restored._upgrade_scene_defaults()
        assert set(restored.for_scene(bundle,PDF)) == {"scene:circle-clockwise", "scene:circle-counterclockwise"}
    finally:restored.close()
