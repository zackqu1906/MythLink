"""An initial recommendation is independent of persisted, user-enabled scopes."""
import json
import pytest

from proximic_ring.application_scene_policy import migrate_enabled_scenes, primary_scene
from proximic_ring.scene_capabilities import application_scene_profiles, MUSIC_APPS
from proximic_ring.ui.application_mapping_controller import ApplicationMappingController, SETTINGS_KEY
from test_app_gestures import route
from test_scene_defaults import add


@pytest.mark.parametrize('bundle', sorted(MUSIC_APPS))
def test_music_players_prefer_audio_despite_video_and_image_document_types(route, monkeypatch, bundle):
    _, service, *_ = route
    catalog = add(service, monkeypatch, bundle, {'video':'generic','image':'generic','music':'generic'})
    assert catalog.primaryScene == 'music'
    assert catalog.configured_scenes()[bundle] == ['music']
    assert {item['value'] for item in catalog.addableScenes} == {'video','image'}
    assert set(catalog._apps[bundle]['scenes']) == {'music'}


def test_unknown_music_category_beats_file_type_order():
    bundle = 'test.audio.app'
    profiles = application_scene_profiles(bundle, dict(CFBundleIdentifier=bundle,
        LSApplicationCategoryType='public.app-category.music', CFBundleDocumentTypes=[
            dict(CFBundleTypeRole='Viewer', CFBundleTypeExtensions=['mp4','png','mp3'])]))
    assert primary_scene(bundle, profiles, category='public.app-category.music') == 'music'


def test_adding_disabling_reenabling_and_restart_preserve_independent_user_keys(route, monkeypatch):
    c, service, *_ = route
    bundle = 'com.apple.Preview'
    catalog = add(service, monkeypatch, bundle)
    catalog.selectScene('pdf')
    assert catalog.setCustomBinding(bundle,'swipe-right','自定义翻页','Ctrl+Right')
    pdf = dict(catalog._bindings_for(bundle,'pdf'))
    generation = service._generation
    assert catalog.addScene('image')
    assert service._generation > generation
    assert catalog.selectedScene == 'image'
    assert catalog.configured_scenes()[bundle] == ['pdf','image']
    assert catalog.setBinding(bundle,'swipe-left','')  # Explicit deletion is durable.
    image = dict(catalog._bindings_for(bundle,'image'))
    assert catalog.addScene('image') and catalog._apps[bundle]['enabledScenes'] == ['pdf','image']
    assert catalog.disableScene('image') and catalog.selectedScene == 'regular'
    assert not catalog.for_scene(bundle,'image')
    assert catalog._bindings_for(bundle,'image') == image
    saved = json.loads(c._settings.value(SETTINGS_KEY))[bundle]
    assert saved['enabledScenes'] == ['pdf'] and saved['scenes']['image'] == image
    restored = ApplicationMappingController(service)
    try:
        restored.selectApplication(bundle)
        assert restored.addScene('image')
        assert restored.for_scene(bundle,'pdf')['scene:swipe-right'].shortcut == 'Ctrl+Right'
        assert restored._bindings_for(bundle,'pdf') == pdf
        assert restored._bindings_for(bundle,'image') == image
        assert not restored.addableScenes
        assert not restored.addScene('video') and not restored.addScene('not-a-mode')
        assert restored.disableScene('pdf') and restored.disableScene('image')
        assert restored.availableScenes == [dict(value='regular',label='默认配置')]
        assert bundle not in restored.configured_scenes()
        again = ApplicationMappingController(service)
        try:
            assert bundle not in again.configured_scenes()
            assert again._apps[bundle]['enabledScenes'] == []
        finally: again.close()
    finally: restored.close()


def test_cleared_scene_stays_empty_but_explicit_new_scene_gets_defaults(route, monkeypatch):
    _, service, *_ = route
    bundle='com.apple.Preview'
    catalog=add(service,monkeypatch,bundle)
    catalog.clearApplicationBindings(bundle)
    assert catalog.addScene('image') and catalog.for_scene(bundle,'image')
    assert not catalog.for_scene(bundle,'pdf')
    catalog.selectScene('image')
    for gesture in list(catalog.bindings[bundle]):
        assert catalog.setBinding(bundle,gesture,'')
    assert catalog.disableScene('image') and catalog.addScene('image')
    assert not catalog.for_scene(bundle,'image')


def test_new_browser_starts_with_video_and_pdf_and_manual_scopes_are_independent(route, monkeypatch):
    _, service, *_=route
    bundle='com.apple.Safari'; catalog=add(service,monkeypatch,bundle)
    assert catalog.configured_scenes()[bundle] == ['pdf','video']
    assert catalog.addScene('pdf') and catalog.addScene('music')
    assert catalog.configured_scenes()[bundle] == ['pdf','video','music']
    assert catalog.disableScene('video')
    assert catalog.configured_scenes()[bundle] == ['pdf','music']


@pytest.mark.parametrize('bundle,stored,expected',[
    ('com.apple.Safari', {}, ['pdf','video','image','music']),
    ('com.apple.Preview', {'primaryScene':'pdf'}, ['pdf']),
    ('com.apple.Music', {'primaryScene':'video'}, ['video','music']),
    ('com.apple.Music', {'primaryScene':'video','enabledScenes':['video']}, ['video']),
    ('com.apple.Preview', {'enabledScenes':[]}, []),
])
def test_legacy_migration_preserves_old_scopes_and_explicit_choices(bundle,stored,expected):
    modes={name:'generic' for name in ['pdf','video','image','music']}
    assert migrate_enabled_scenes(bundle,modes,stored) == expected


def test_legacy_music_video_config_gains_music_without_losing_custom_video_keys(route,monkeypatch):
    c,service,*_=route;bundle='com.apple.Music'
    catalog=add(service,monkeypatch,bundle,{'video':'generic'})
    assert catalog.addScene('video')
    assert catalog.setCustomBinding(bundle,'swipe-right','跳转','Ctrl+Right')
    saved=json.loads(c._settings.value(SETTINGS_KEY))
    saved[bundle].pop('enabledScenes');saved[bundle]['primaryScene']='video'
    c._settings.setValue(SETTINGS_KEY,json.dumps(saved))
    restored=ApplicationMappingController(service)
    try:
        restored.selectApplication(bundle)
        assert restored.primaryScene=='music'
        assert restored.configured_scenes()[bundle]==['video','music']
        assert restored.for_scene(bundle,'video')['scene:swipe-right'].shortcut=='Ctrl+Right'
        assert restored.for_scene(bundle,'music')
    finally:restored.close()


def test_adding_generic_presentation_keeps_regular_keys_and_waits_for_real_start_shortcut(route,monkeypatch):
    _,service,*_=route;catalog=service.catalog
    monkeypatch.setattr(catalog,'_request',lambda *args:None)
    bundle='test.multiformat.editor'
    catalog._candidates=[dict(value=bundle,label='编辑器',primaryScene='pdf',
        sceneProfiles={'pdf':'generic','presentation':'generic'},presentationProfile='generic')]
    assert catalog.addApplication(bundle)
    assert catalog.configured_scenes()[bundle]==['pdf']
    assert catalog.addScene('presentation')
    assert catalog.configured_scenes()[bundle]==['presentation','pdf']
    assert catalog._apps[bundle]['pendingStart']
    assert catalog.for_scene(bundle,'presentation')['scene:swipe-right'].shortcut=='Right'
    action=dict(id='start-current',label='From Current Slide',path='Slide Show',shortcut='Cmd+Return',available=True)
    catalog._apply_result('menu',catalog._generation['menu'],bundle,dict(actions=[action]),'')
    assert catalog.regularBindings[bundle]['snap']['shortcut']=='Cmd+Return'
    assert not catalog._apps[bundle]['pendingStart']


def test_cleared_presentation_is_not_refilled_when_reenabled(route,monkeypatch):
    _,service,*_=route;bundle='com.microsoft.Powerpoint'
    catalog=add(service,monkeypatch,bundle)
    assert catalog.clearApplicationBindings(bundle)
    assert catalog.disableScene('presentation')
    assert catalog.addScene('presentation')
    assert not catalog.for_scene(bundle,'presentation')
    assert not catalog.regularBindings[bundle]
