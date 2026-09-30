"""One scene model for reading and playback, with foreground/voice boundaries."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication

from proximic_ring.activity_scenes import activity_context
from proximic_ring.scene_capabilities import (PDF, VIDEO, IMAGE, MUSIC, SCENE_LABELS,
    application_scene_profiles, installed_scene_profiles, activity_actions)
from proximic_ring.ui.application_mapping_controller import ApplicationMappingController
from test_app_gestures import route  # Required by the imported presentation fixture.
from test_gesture_scenes import presentation
from test_application_menus import configure
from test_ring_gestures import request
from test_application_catalog import application


def metadata(node, key):
    assert key not in {"AXValue", "AXSelectedText", "AXSelectedTextRange"}
    return node.get(key)


def content(scene):
    filename = {PDF: "test.pdf", IMAGE: "test.png", VIDEO: "test.mp4", MUSIC: "test.mp3"}[scene]
    window = dict(AXRole="AXWindow", AXDocument="file:///test/"+filename, AXSubrole="AXStandardWindow")
    focus = dict(AXRole="AXGroup", AXParent=window)
    window["AXChildren"] = [focus]
    if scene in {VIDEO, MUSIC}:
        focus["AXChildren"] = [dict(AXRole="AXButton", AXDescription="Play", AXEnabled=True),
                                dict(AXRole="AXSlider", AXDescription="Playback progress", AXEnabled=True)]
    return window, focus


@pytest.mark.parametrize("scene", [PDF, IMAGE, VIDEO, MUSIC])
@pytest.mark.parametrize("state", ["ready", "text", "missing_focus", "dialog", "sheet", "minimized", "timeout"])
def test_all_content_scenes_require_valid_window_and_preserve_text_or_unknown_focus(scene, state):
    window, focus = content(scene)
    expected = (scene, "nontext")
    if state == "text": focus = dict(AXRole="AXTextField", AXParent=window); expected = (scene, "text")
    if state == "missing_focus": focus = None; expected = (scene, "unknown")
    if state == "dialog": window["AXSubrole"] = "AXDialog"; expected = ("", "unknown")
    if state == "sheet": window["AXSheets"] = [dict(AXRole="AXSheet")]; expected = ("", "unknown")
    if state == "minimized": window["AXMinimized"] = True; expected = ("", "unknown")
    if state == "timeout": expected = ("", "unknown")
    assert activity_context("test.viewer", {s: "generic" for s in SCENE_LABELS}, window, focus, metadata,
                            budget=0 if state == "timeout" else .16) == expected


def test_one_viewer_switches_pdf_image_video_and_music_without_editor_selection():
    profiles = {s: "generic" for s in (PDF, IMAGE, VIDEO, MUSIC)}
    for scene in profiles:
        window, focus = content(scene)
        assert activity_context("test.viewer", profiles, window, focus, metadata) == (scene, "nontext")


@pytest.mark.parametrize("change", ["empty", "hidden", "disabled", "just_title", "fullscreen", "no_kind"])
def test_player_home_hidden_controls_and_unknown_content_do_not_activate_playback(change):
    window, focus = content(VIDEO)
    if change == "empty": focus["AXChildren"] = []
    if change == "hidden": focus["AXHidden"] = True
    if change == "disabled": focus["AXChildren"][0]["AXEnabled"] = False
    if change == "just_title": window.pop("AXDocument"); window["AXTitle"] = "movie.mp4"
    if change == "fullscreen": window.pop("AXDocument"); window["AXFullScreen"] = True
    if change == "no_kind": window.pop("AXDocument")
    assert activity_context("test.viewer", {VIDEO: "generic", MUSIC: "generic"}, window, focus, metadata) == ("", "unknown")


@pytest.mark.parametrize("scene", [VIDEO, MUSIC])
@pytest.mark.parametrize("label", ["Play", "Pause", "播放", "暂停"])
def test_pause_does_not_disable_the_resume_gesture(scene, label):
    window, focus = content(scene); focus["AXChildren"][0]["AXDescription"] = label
    assert activity_context("test.viewer", {scene: "generic"}, window, focus, metadata) == (scene, "nontext")


def test_music_player_controls_need_loaded_content_and_yield_to_search():
    window, focus = content(MUSIC); window.pop("AXDocument")
    assert activity_context("com.apple.Music", {MUSIC: "music"}, window, focus, metadata) == (MUSIC, "nontext")
    focus["AXRole"] = "AXSearchField"
    # Detection may lose positive evidence when focus becomes a text container;
    # it must never advertise nontext eligibility.
    assert activity_context("com.apple.Music", {MUSIC: "music"}, window, focus, metadata)[1] != "nontext"


def test_browser_document_url_and_media_controls_are_required_not_the_tab_title():
    window = dict(AXRole="AXWindow", AXTitle="paper.pdf")
    focus = dict(AXRole="AXWebArea", AXParent=window, AXEditable=False)
    window["AXChildren"] = [focus]
    profiles = application_scene_profiles("com.apple.Safari")
    assert not activity_context("com.apple.Safari", profiles, window, focus, metadata)[0]
    focus["AXURL"] = "https://example.test/paper.pdf?token=not-read-or-logged"
    assert activity_context("com.apple.Safari", profiles, window, focus, metadata) == (PDF, "nontext")
    focus["AXEditable"] = True
    assert activity_context("com.apple.Safari", profiles, window, focus, metadata) == (PDF, "text")


def test_unknown_apps_gain_all_declared_modes_and_custom_uti_conformance(tmp_path):
    path = application(tmp_path/'Universal.app', 'test.universal', CFBundleDocumentTypes=[
        {'CFBundleTypeRole': 'Viewer', 'CFBundleTypeExtensions': ['PDF', 'MP4', 'MP3']},
        {'CFBundleTypeRole': 'Editor', 'LSItemContentTypes': ['test.photo']}],
        UTExportedTypeDeclarations=[{'UTTypeIdentifier': 'test.photo', 'UTTypeConformsTo': ['public.image']}])
    assert set(installed_scene_profiles('test.universal', str(path))) == {PDF, VIDEO, IMAGE, MUSIC}
    assert installed_scene_profiles('test.wrong.identity', str(path)) == {}


@pytest.mark.parametrize("bundle,modes", [
    ("com.apple.Preview", {PDF, IMAGE}), ("org.videolan.vlc", {VIDEO, MUSIC}),
    ("com.colliderli.iina", {VIDEO, MUSIC}), ("com.spotify.client", {MUSIC}),
    ("com.netease.163music", {MUSIC}), ("com.tencent.QQMusic", {MUSIC}),
    ("com.apple.QuickTimePlayerX", {VIDEO, MUSIC}), ("com.apple.Safari", {PDF, VIDEO, IMAGE, MUSIC}),
])
def test_known_viewers_players_and_browsers_get_modes_without_being_running(bundle, modes):
    assert set(application_scene_profiles(bundle)) == modes


@pytest.mark.parametrize("scene", [PDF, IMAGE, VIDEO, MUSIC])
@pytest.mark.parametrize("mode", ["input", "operation"])
def test_scene_tap_routes_before_voice_in_both_modes_and_restores_after_leaving(presentation, monkeypatch, scene, mode):
    c, service, inline, backend, _, sent, _ = presentation
    bundle = "com.apple.Preview" if scene in {PDF, IMAGE} else "com.apple.QuickTimePlayerX"
    catalog = configure(service, monkeypatch, bundle)
    catalog.selectScene(scene)
    assert catalog.setCustomBinding(bundle, "tap", "测试动作", "Space")
    assert not catalog.canBind("index-pinch") and not catalog.canBind("middle-pinch")
    backend.target = replace(backend.target, bundle=bundle, scene=scene)
    c.ringGestures._mode = mode
    catalog.selectScene("regular")  # Editor selection never drives runtime.
    assert not request(c, "tap") and sent == [(bundle, "Space")]
    backend.target = replace(backend.target, input_context="text")
    request(c, "tap")
    assert sent == [(bundle, "Space")]


def test_same_app_scene_change_drops_queued_shortcut_and_persists_independent_bindings(presentation, monkeypatch):
    c, service, _, backend, _, sent, _ = presentation
    bundle = "com.apple.Preview"; catalog = configure(service, monkeypatch, bundle)
    for scene, shortcut in [(PDF, "Alt+Down"), (IMAGE, "PageDown")]:
        catalog.selectScene(scene)
        assert catalog.setCustomBinding(bundle, "swipe-right", "下一项", shortcut)
    backend.target = replace(backend.target, bundle=bundle, scene=PDF)
    assert not request(c, "swipe-right", deliver=False)
    backend.target = replace(backend.target, scene=IMAGE)
    QCoreApplication.processEvents()
    assert not sent
    assert not request(c, "swipe-right") and sent == [(bundle, "PageDown")]
    restored = ApplicationMappingController(service)
    try:
        assert restored.configured_scenes()[bundle] == [PDF, IMAGE]
        assert restored.for_scene(bundle, PDF)['scene:swipe-right'].shortcut == 'Alt+Down'
        assert restored.for_scene(bundle, IMAGE)['scene:swipe-right'].shortcut == 'PageDown'
        assert restored.regularBindings[bundle] == {}
    finally:
        restored.close()


def test_preset_shortcuts_are_scoped_to_the_actual_application():
    assert activity_actions('com.apple.Preview', PDF)[0]['shortcut'] == 'Alt+Up'
    assert activity_actions('com.apple.Music', MUSIC)[0]['shortcut'] == 'Space'
    assert activity_actions('com.apple.QuickTimePlayerX', VIDEO)[1]['shortcut'] == 'Cmd+Left'
    assert activity_actions('test.unknown.player', VIDEO) == []


@pytest.mark.parametrize('scene', [VIDEO, MUSIC])
def test_quicktime_native_toggle_is_a_playback_control_and_nontext_focus(scene):
    window, canvas = content(scene)
    toggle = canvas['AXChildren'][0]
    toggle.update(AXRole='AXCheckBox', AXSubrole='AXToggle', AXDescription='play/pause', AXParent=window)
    window['AXChildren'] = [toggle, canvas['AXChildren'][1]]
    profiles = application_scene_profiles('com.apple.QuickTimePlayerX')
    assert activity_context('com.apple.QuickTimePlayerX', profiles, window, toggle, metadata) == (scene, 'nontext')
    toggle.pop('AXEnabled')
    assert activity_context('com.apple.QuickTimePlayerX', profiles, window, toggle, metadata) == ('', 'unknown')


@pytest.mark.parametrize('scene', [PDF, VIDEO, IMAGE, MUSIC])
@pytest.mark.parametrize('state', ['nontext', 'text', 'unknown', 'blocked', 'unconfigured'])
def test_autofocus_only_yields_to_configured_nontext_scene(monkeypatch, scene, state):
    from proximic_ring import native_access_worker as worker
    from proximic_ring.app_shortcuts import ShortcutTarget
    from proximic_ring.mac_permissions import PermissionState
    monkeypatch.setattr(worker, 'read_permission_state', lambda: PermissionState(True, False))
    dispatcher = worker.Dispatcher()
    dispatcher.shortcuts = SimpleNamespace(capture=lambda **kw: ShortcutTarget('test.viewer', 42, 'test.viewer',
        scene=scene, input_context=state if state in {'text', 'unknown'} else 'nontext', blocked=state == 'blocked'))
    fallback = []
    dispatcher.text_focus = SimpleNamespace(plans={'old': object()},
        handle=lambda *a, **kw: fallback.append(True) or {'status': 'normal'})
    modes = [] if state == 'unconfigured' else [scene]
    result = dispatcher.handle({'operation': 'focus_apply', 'scene_apps': {'test.viewer': modes}, 'plan': 'old'})
    if state == 'nontext':
        assert result['result']['scene'] == scene and not fallback
        assert not dispatcher.text_focus.plans
    else:
        assert fallback
