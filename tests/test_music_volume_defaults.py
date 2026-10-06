"""Music volume defaults and old-config migration, independent of scene routing."""
from copy import deepcopy
from dataclasses import replace
import json
import pytest
from PySide6.QtCore import QCoreApplication
from proximic_ring.scene_defaults import scene_defaults, resolve_action, migrate_music_volume_defaults
from proximic_ring.ui.application_mapping_controller import ApplicationMappingController, SETTINGS_KEY
from test_app_gestures import route
from test_gesture_scenes import presentation
from test_scene_defaults import add, menu
from test_ring_gestures import request

MUSIC = 'music'
BUNDLE = 'com.apple.Music'

@pytest.mark.parametrize('bundle,up,down', [(BUNDLE,'Cmd+Up','Cmd+Down'),
    ('org.videolan.vlc','Cmd+Up','Cmd+Down'), ('com.colliderli.iina','Up','Down'),
    ('com.apple.QuickTimePlayerX','Up','Down'), ('com.apple.Safari','Up','Down'),
    ('com.google.Chrome','Up','Down')])
def test_music_defaults_use_swipes_and_each_apps_volume_keys(bundle,up,down):
    from proximic_ring.scene_capabilities import application_scene_profiles
    profiles=application_scene_profiles(bundle)
    bindings,pending=scene_defaults(bundle,{MUSIC:profiles[MUSIC]})
    assert bindings[MUSIC]['swipe-up']['shortcut']==up
    assert bindings[MUSIC]['swipe-down']['shortcut']==down
    assert not any(g.startswith('circle-') for g in bindings[MUSIC])
    assert not any(g.startswith('circle-') for g in pending.get(MUSIC,{}))


def old_config(bundle=BUNDLE):
    profile='music' if bundle==BUNDLE else 'generic'
    return dict(sceneDefaultsVersion=1,sceneProfiles={MUSIC:profile},enabledScenes=[],
        scenes={MUSIC:{g:resolve_action(bundle,MUSIC,a,profile) for g,a in
            [('tap','play'),('circle-clockwise','volume-up'),('circle-counterclockwise','volume-down')]}},
        pendingDefaults={})

@pytest.mark.parametrize('case',['factory','custom_circle','custom_swipe','deleted','cleared','removed','empty','menu','pending'])
def test_migration_preserves_custom_keys_deletions_and_disabled_scenes(case):
    app=old_config();items=app['scenes'][MUSIC]
    custom=dict(id='custom:volume',label='Volume Up',path='Custom',shortcut='Alt+Up')
    if case=='custom_circle':items['circle-clockwise']=custom
    if case=='custom_swipe':items['swipe-up']=custom
    if case=='deleted':items.pop('circle-clockwise')
    if case=='cleared':app['defaultsCleared']=True
    if case=='removed':app['removed']=True
    if case=='empty':items.clear()
    if case=='menu':items['circle-clockwise']={k:v for k,v in menu('Volume Up','Alt+Up').items() if k!='available'}
    if case=='pending':
        items.pop('circle-clockwise');app['pendingDefaults']={MUSIC:{'circle-clockwise':'volume-up'}}
    before=deepcopy(app)
    assert migrate_music_volume_defaults(BUNDLE,app)
    assert app['sceneDefaultsVersion']==2 and app['enabledScenes']==[]
    if case in {'cleared','removed','empty'}:assert items==before['scenes'][MUSIC]
    else:
        assert items['swipe-down']['shortcut']=='Cmd+Down'
        assert 'circle-counterclockwise' not in items
        if case=='custom_circle':assert items['circle-clockwise']==custom and 'swipe-up' not in items
        elif case=='deleted':assert 'swipe-up' not in items
        elif case=='custom_swipe':assert items['swipe-up']==custom
        else:assert items['swipe-up']['shortcut']==('Alt+Up' if case=='menu' else 'Cmd+Up')
    after=deepcopy(app);assert not migrate_music_volume_defaults(BUNDLE,app) and app==after


def test_legacy_pending_config_loads_and_resolves_swipes_after_menu_discovery(route,monkeypatch):
    c,service,*_=route;bundle='test.music';catalog=add(service,monkeypatch,bundle,{MUSIC:'generic'})
    saved=json.loads(c._settings.value(SETTINGS_KEY));app=saved[bundle]
    app['sceneDefaultsVersion']=1
    pending=app['pendingDefaults'][MUSIC]
    pending['circle-clockwise']=pending.pop('swipe-up');pending['circle-counterclockwise']=pending.pop('swipe-down')
    c._settings.setValue(SETTINGS_KEY,json.dumps(saved))
    restored=ApplicationMappingController(service)
    try:
        monkeypatch.setattr(restored,'_request',lambda *args:None)
        assert restored._config_valid
        restored.selectApplication(bundle);restored.selectScene(MUSIC)
        assert restored.pendingBindings['swipe-up']=='提高音量'
        assert 'circle-clockwise' not in restored.pendingBindings
        restored._apply_result('menu',restored._generation['menu'],bundle,
            dict(actions=[menu('Volume Up','Alt+Up'),menu('Volume Down','Alt+Down')]),'')
        assert restored.for_scene(bundle,MUSIC)['scene:swipe-up'].shortcut=='Alt+Up'
        assert restored.for_scene(bundle,MUSIC)['scene:swipe-down'].shortcut=='Alt+Down'
        assert json.loads(c._settings.value(SETTINGS_KEY))[bundle]['sceneDefaultsVersion']==2
    finally:restored.close()


def test_existing_browser_music_gets_volume_swipes_and_keeps_chat_circles(route,monkeypatch):
    c,service,*_=route;bundle='com.apple.Safari';catalog=add(service,monkeypatch,bundle)
    assert catalog.addScene(MUSIC)
    saved=json.loads(c._settings.value(SETTINGS_KEY));app=saved[bundle];app['sceneDefaultsVersion']=1
    for g in ['swipe-up','swipe-down']:app['scenes'][MUSIC].pop(g)
    c._settings.setValue(SETTINGS_KEY,json.dumps(saved))
    restored=ApplicationMappingController(service)
    try:
        assert restored._config_valid
        actions=restored.for_scene(bundle,MUSIC)
        assert actions['scene:swipe-up'].shortcut=='Up' and actions['scene:swipe-down'].shortcut=='Down'
        assert actions['scene:circle-clockwise'].shortcut=='Ctrl+Tab'
        assert actions['scene:circle-counterclockwise'].shortcut=='Ctrl+Shift+Tab'
    finally:restored.close()

@pytest.mark.parametrize('mode',['input','operation'])
@pytest.mark.parametrize('bundle,keys',[(BUNDLE,['Cmd+Up','Cmd+Down']),('com.apple.Safari',['Up','Down'])])
def test_swipes_dispatch_volume_in_music_scene_without_voice_or_scroll(presentation,monkeypatch,mode,bundle,keys):
    c,service,inline,backend,_,sent,messages=presentation
    catalog=add(service,monkeypatch,bundle);catalog.addScene(MUSIC)
    c.ringGestures._mode=mode
    before=deepcopy(inline._view)
    backend.target=replace(backend.target,bundle=bundle,scene=MUSIC,input_context='nontext')
    for gesture in ['swipe-up','swipe-down']:assert not request(c,gesture)
    assert sent==[(bundle,key) for key in keys] and not messages
    assert inline._view==before and not c.ringGestures._scroll.pending.is_set()

@pytest.mark.parametrize('change',[dict(scene=''),dict(input_context='text'),dict(bundle='other.app'),dict(blocked=True)])
def test_queued_music_volume_does_not_run_after_context_changes(presentation,monkeypatch,change):
    c,service,_,backend,_,sent,_=presentation
    add(service,monkeypatch,BUNDLE)
    backend.target=replace(backend.target,bundle=BUNDLE,scene=MUSIC,input_context='nontext')
    assert not request(c,'swipe-up',deliver=False)
    backend.target=replace(backend.target,**change);QCoreApplication.processEvents()
    assert not sent
