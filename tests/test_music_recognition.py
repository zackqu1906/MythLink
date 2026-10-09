"""Playback evidence and app purpose must not be confused with file support."""
import plistlib
from types import SimpleNamespace
import pytest
from proximic_ring.scene_capabilities import (MUSIC_APPS, MUSIC, VIDEO, BROWSERS,
    installed_application_category, installed_scene_profiles)
from proximic_ring.scenes.recognition.engine import detect_scene
from test_activity_scenes import content, metadata
from test_application_catalog import application
from test_presentation_portability import native_backend


def player():
    window, focus = content(MUSIC)
    window.pop('AXDocument')
    return window, focus


def recognize(bundle, window, focus, category=''):
    return detect_scene(bundle, {MUSIC: 'generic', VIDEO: 'generic'}, window, focus, metadata,
                        application_category=category)


@pytest.mark.parametrize('bundle', sorted(MUSIC_APPS))
@pytest.mark.parametrize('play_label', ['play', 'pause', '播放', '暂停'])
def test_music_apps_with_video_capability_recognize_loaded_or_paused_player(bundle, play_label):
    window, focus = player()
    focus['AXChildren'][0]['AXDescription'] = play_label
    result = recognize(bundle, window, focus)
    assert (result.scene, result.input_context) == (MUSIC, 'nontext')
    assert result.diagnostic['evidence'] == 'music_player_controls'
    assert result.diagnostic['music_application'] is True


def test_unknown_declared_music_app_uses_same_rule_even_with_movie_files(tmp_path):
    bundle = 'test.new.music'
    path = application(tmp_path/'New Music.app', bundle,
        LSApplicationCategoryType='public.app-category.music', CFBundleDocumentTypes=[
            dict(CFBundleTypeRole='Viewer', CFBundleTypeExtensions=['mp3', 'mp4'])])
    profiles = installed_scene_profiles(bundle, str(path))
    category = installed_application_category(bundle, str(path))
    window, focus = player()
    result = detect_scene(bundle, profiles, window, focus, metadata, application_category=category)
    assert set(profiles) == {VIDEO, MUSIC} and result.scene == MUSIC
    assert installed_application_category('test.wrong.identity', str(path)) == ''
    info = path/'Contents/Info.plist'
    data = plistlib.loads(info.read_bytes()); data['LSApplicationCategoryType'] = 'public.app-category.video'
    info.write_bytes(plistlib.dumps(data))
    assert installed_application_category(bundle, str(path)) == 'public.app-category.video'
    assert installed_application_category(bundle, str(tmp_path/'Missing.app')) == ''


@pytest.mark.parametrize('case', ['no_controls', 'disabled_play', 'hidden_controls', 'no_seek', 'only_volume',
                                  'modal', 'sheet', 'minimized', 'unrelated_document'])
def test_music_app_identity_alone_never_activates_music(case):
    window, focus = player(); controls = focus['AXChildren']
    if case == 'no_controls': focus['AXChildren'] = []
    if case == 'disabled_play': controls[0]['AXEnabled'] = False
    if case == 'hidden_controls': focus['AXHidden'] = True
    if case == 'no_seek': focus['AXChildren'] = controls[:1]
    if case == 'only_volume': controls[1]['AXDescription'] = 'Volume'
    if case == 'modal': window['AXModal'] = True
    if case == 'sheet': window['AXSheets'] = [dict(AXRole='AXSheet')]
    if case == 'minimized': window['AXMinimized'] = True
    if case == 'unrelated_document': window['AXDocument'] = 'file:///test.pdf'
    assert not recognize('com.apple.Music', window, focus).scene


@pytest.mark.parametrize('bundle', ['com.apple.Music', 'test.new.music'])
@pytest.mark.parametrize('case', ['video_document', 'video_surface'])
def test_music_purpose_never_overrides_current_video_evidence(bundle, case):
    window, focus = player()
    if case == 'video_document': window['AXDocument'] = 'file:///test.mp4'
    if case == 'video_surface': focus['AXChildren'].append(dict(AXRole='AXGroup', AXDescription='Video view'))
    assert recognize(bundle, window, focus, 'public.app-category.music').scene == VIDEO


@pytest.mark.parametrize('case', ['search', 'missing', 'stale', 'foreign', 'menu'])
def test_music_player_still_yields_to_text_or_unconfirmed_focus(case):
    window, focus = player()
    if case == 'search': focus = dict(AXRole='AXSearchField', AXParent=window)
    if case == 'missing': focus = None
    if case == 'stale': focus['AXFocused'] = False
    if case == 'foreign': focus = dict(AXRole='AXGroup', AXParent=dict(AXRole='AXWindow'))
    if case == 'menu': focus = dict(AXRole='AXMenu', AXParent=window)
    result = recognize('com.apple.Music', window, focus)
    assert result.input_context == ('text' if case == 'search' else 'unknown')


@pytest.mark.parametrize('bundle', ['org.videolan.vlc', 'com.colliderli.iina', 'com.apple.QuickTimePlayerX', 'test.viewer'])
def test_general_players_still_require_media_kind(bundle):
    window, focus = player()
    assert not recognize(bundle, window, focus).scene


@pytest.mark.parametrize('bundle', sorted(BROWSERS))
def test_browser_never_uses_music_application_fallback(bundle):
    window, focus = player()
    focus.update(AXRole='AXWebArea', AXEditable=False, AXURL='https://example.test/')
    assert not recognize(bundle, window, focus, 'public.app-category.music').scene


def test_native_capture_passes_verified_installed_category_to_recognizer(monkeypatch, tmp_path):
    bundle = 'test.new.music'
    path = application(tmp_path/'Player.app', bundle, LSApplicationCategoryType='public.app-category.music',
        CFBundleDocumentTypes=[dict(CFBundleTypeRole='Viewer', CFBundleTypeExtensions=['mp3', 'mp4'])])
    window, focus = player()
    backend, _, _ = native_backend(monkeypatch, window, focus, bundle=bundle)
    from proximic_ring.mac_workspace import frontmost_application
    frontmost_application().bundleURL = lambda: SimpleNamespace(path=lambda: str(path))
    result = backend.capture(menu_action=True, scene=True)
    assert result.scene == MUSIC and result.input_context == 'nontext' and not result.blocked
    assert set(result.diagnostic['capabilities']) == {VIDEO, MUSIC}


@pytest.mark.parametrize('state', ['ready', 'editable', 'text_child', 'hidden', 'disabled', 'stale', 'foreign'])
def test_album_grid_uses_shared_focus_checks(state):
    window, focus = player()
    grid = dict(AXRole='AXGrid', AXParent=window, AXEnabled=True, AXFocused=True)
    if state == 'editable': grid['AXEditable'] = True
    if state == 'text_child': grid = dict(AXRole='AXTextField', AXParent=grid)
    if state == 'hidden': grid['AXHidden'] = True
    if state == 'disabled': grid['AXEnabled'] = False
    if state == 'stale': grid['AXFocused'] = False
    if state == 'foreign': grid['AXParent'] = dict(AXRole='AXWindow')
    result = recognize('com.apple.Music', window, grid)
    expected = 'nontext' if state == 'ready' else 'text' if state in {'editable', 'text_child'} else 'unknown'
    assert result.scene == MUSIC and result.input_context == expected
