"""Browser navigation shares saved regular bindings; empty keys inherit global."""
from dataclasses import replace
import json
import pytest
from PySide6.QtCore import QCoreApplication
from proximic_ring.browser_shortcuts import NAVIGATION
from proximic_ring.ui.desktop.application_mapping_controller import ApplicationMappingController, SETTINGS_KEY
from test_app_gestures import route
from test_application_menus import configure
from test_gesture_scenes import presentation
from test_ring_gestures import request

BROWSERS = ["com.apple.Safari", "com.google.Chrome", "org.mozilla.firefox", "com.microsoft.edgemac", "com.brave.Browser"]
SCENES = ["", "video", "pdf", "image", "music", "presentation"]

@pytest.mark.parametrize("bundle", BROWSERS)
@pytest.mark.parametrize("scene", SCENES)
@pytest.mark.parametrize("context", ["nontext", "text", "unknown"])
def test_circles_switch_tabs_in_every_scene_and_input_context(presentation, monkeypatch, bundle, scene, context):
    c, service, inline, backend, _, sent, messages = presentation
    catalog = configure(service, monkeypatch, bundle)
    # Exercise all scene routes even for browsers without built-in capability metadata.
    catalog._apps[bundle]["sceneProfiles"].update({name:"generic" for name in SCENES if name})
    catalog._apps[bundle]["enabledScenes"] = [name for name in SCENES if name]
    catalog.selectScene("video")  # UI selection does not choose runtime actions.
    before = dict(inline._view)
    backend.target = replace(backend.target, bundle=bundle, scene=scene, input_context=context, page_key="one")
    for gesture in NAVIGATION:
        assert not request(c, gesture)
    # Circles do not overlap the default input bindings, so input focus does
    # not suppress them (the same policy exercised by test_gesture_ownership).
    assert sent == [(bundle,"Ctrl+Tab"),(bundle,"Ctrl+Shift+Tab")]
    assert not messages and inline._view == before

@pytest.mark.parametrize("mode", ["input", "operation"])
def test_regular_custom_navigation_is_shared_in_scene_editor_and_runtime(presentation, monkeypatch, mode):
    c, service, _, backend, _, sent, _ = presentation
    bundle=BROWSERS[0];catalog=configure(service,monkeypatch,bundle)
    catalog.selectScene("regular")
    assert catalog.setCustomBinding(bundle,"circle-clockwise","下一个 Chat","Cmd+Shift+]")
    for scene in ["video", "pdf"]:
        catalog.selectScene(scene)
        assert catalog.bindings[bundle]["circle-clockwise"]["shortcut"] == "Cmd+Shift+]"
        assert not catalog.canBind("circle-clockwise")
        assert not catalog.setCustomBinding(bundle,"circle-clockwise","音量","Up")
    c.ringGestures._mode=mode
    backend.target=replace(backend.target,bundle=bundle,scene="video",input_context="nontext")
    assert not request(c,"circle-clockwise")
    assert sent==[(bundle,"Cmd+Shift+]")]

@pytest.mark.parametrize("change", ["page", "tab", "player", "app", "pid", "window", "modal", "scene", "voice", "disconnect"])
def test_queued_navigation_rejects_changed_target_or_busy_voice(presentation, monkeypatch, change):
    c,service,inline,backend,_,sent,_=presentation
    bundle=BROWSERS[0];configure(service,monkeypatch,bundle)
    backend.target=replace(backend.target,bundle=bundle,scene="video",input_context="nontext",page_key="one",web_area="tab1",player="video1")
    assert not request(c,"circle-clockwise",deliver=False)
    changes={"page":dict(page_key="two"),"tab":dict(web_area="tab2"),"player":dict(player="video2"),
        "app":dict(bundle="other.app"),"pid":dict(pid=43),"window":dict(window="other"),
        "modal":dict(blocked=True),"scene":dict(scene="pdf")}
    if change in changes:backend.target=replace(backend.target,**changes[change])
    if change=="voice":inline._view["phase"]="listening"
    if change=="disconnect":c._disconnect_event.set()
    QCoreApplication.processEvents()
    assert not sent

def test_old_site_overrides_are_archived_and_never_route_or_break_loading(route,monkeypatch):
    c,service,*_=route;bundle=BROWSERS[0];catalog=configure(service,monkeypatch,bundle)
    saved=json.loads(c._settings.value(SETTINGS_KEY))
    saved[bundle]["websites"]={"broken old domain":{"bindings":{"tap":{"shortcut":"N"}}}}
    saved[bundle]["scenes"]["video"]["circle-clockwise"]={"id":"web-video:volume-up","label":"音量","path":"old","shortcut":"Up"}
    raw=json.dumps(saved);c._settings.setValue(SETTINGS_KEY,raw)
    restored=ApplicationMappingController(service)
    try:
        assert restored._config_valid and not hasattr(restored,"addWebsite")
        assert "websites" not in restored._apps[bundle]
        assert "circle-clockwise" not in restored._apps[bundle]["scenes"]["video"]
        assert restored.for_scene(bundle,"video")["scene:circle-clockwise"].shortcut=="Ctrl+Tab"
        assert restored.for_scene(bundle,"video")["scene:tap"].shortcut=="Space"
        assert c._settings.value(SETTINGS_KEY+"/beforeAutomaticBrowserScenes")==raw
        restored._save()
        assert "websites" not in json.loads(c._settings.value(SETTINGS_KEY))[bundle]
        restored.removeApplication(bundle)
        assert not restored.for_target(bundle) and not restored.for_scene(bundle,"video")
    finally:restored.close()

def test_cleared_browser_navigation_stays_empty_after_reload(route,monkeypatch):
    _,service,*_=route;bundle=BROWSERS[0];catalog=configure(service,monkeypatch,bundle)
    catalog.clearApplicationBindings(bundle)
    assert not catalog.for_target(bundle)
    for scene in ["video","pdf"]:
        assert not catalog.for_scene(bundle,scene)
    catalog.selectScene("regular")
    assert catalog.setBinding(bundle,"circle-clockwise","")
    restored = ApplicationMappingController(service)
    try:
        assert not restored.for_target(bundle)
        assert not restored.for_scene(bundle, "video")
    finally:
        restored.close()


@pytest.mark.parametrize("clear_all", [False, True])
def test_removed_browser_binding_falls_back_to_global(presentation, monkeypatch, clear_all):
    c, service, _, backend, _, sent, _ = presentation
    bundle = BROWSERS[0]
    catalog = configure(service, monkeypatch, bundle)
    backend.target = replace(backend.target, bundle=bundle, scene="video", input_context="nontext")
    starts = []
    monkeypatch.setattr(c.ringGestures._selector, "start", lambda *args: starts.append(True))
    assert c.ringGestures.setGlobalBinding("window_selector", "circle-clockwise")
    request(c, "circle-clockwise")
    assert sent == [(bundle, "Ctrl+Tab")] and not starts
    if clear_all:
        assert catalog.clearApplicationBindings(bundle)
    else:
        assert catalog.setBinding(bundle, "circle-clockwise", "")
        assert "menu:circle-counterclockwise" in catalog.for_target(bundle)
    request(c, "circle-clockwise")
    assert starts == [True] and len(sent) == 1
