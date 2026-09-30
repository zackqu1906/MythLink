"""Scene presets survive lifecycle changes without becoming a routing fallback."""
from dataclasses import replace
import json

import pytest

from proximic_ring.application_defaults import default_mappings
from proximic_ring.scene_defaults import TEMPLATES, menu_action, scene_defaults
from proximic_ring.scene_capabilities import PDF, IMAGE, VIDEO, MUSIC, application_scene_profiles
from proximic_ring.ui.application_mapping_controller import ApplicationMappingController, SETTINGS_KEY
from test_app_gestures import route
from test_gesture_scenes import presentation
from test_ring_gestures import request


@pytest.mark.parametrize('bundle,scene,expected', [
    ('com.apple.Music', MUSIC, ['Space', 'Left', 'Right', 'Cmd+Up', 'Cmd+Down']),
    ('org.videolan.vlc', MUSIC, ['Space', 'Cmd+Left', 'Cmd+Right', 'Cmd+Up', 'Cmd+Down']),
    ('org.videolan.vlc', VIDEO, ['Space', 'Cmd+Alt+Left', 'Cmd+Alt+Right', 'Cmd+Up', 'Cmd+Down']),
    ('com.colliderli.iina', MUSIC, ['Space', 'Cmd+Left', 'Cmd+Right', 'Up', 'Down']),
    ('com.colliderli.iina', VIDEO, ['Space', 'Left', 'Right', 'Up', 'Down']),
    ('com.apple.QuickTimePlayerX', VIDEO, ['Space', 'Cmd+Left', 'Cmd+Right', 'Up', 'Down']),
    ('com.apple.Preview', PDF, ['Alt+Up', 'Alt+Down']),
    ('com.apple.Preview', IMAGE, ['PageUp', 'PageDown']),
])
def test_scene_meanings_resolve_to_each_apps_own_shortcuts(bundle, scene, expected):
    defaults = default_mappings(bundle)
    assert list(defaults[scene]) == list(TEMPLATES[scene])
    assert [v['shortcut'] for v in defaults[scene].values()] == expected
    defaults[scene].clear()
    assert len(default_mappings(bundle)[scene]) == len(expected)  # No shared mutable state.


def add(service, monkeypatch, bundle='test.media', modes=None):
    catalog = service.catalog
    monkeypatch.setattr(catalog, '_request', lambda *args: None)
    catalog._candidates = [dict(value=bundle, label='Media', sceneProfiles=modes or {})]
    assert catalog.addApplication(bundle)
    return catalog


def menu(label, key, path='Playback'):
    return dict(id='native:' + label, label=label, shortcut=key, path=path, available=True)


@pytest.mark.parametrize('bundle,modes', [
    ('com.apple.Music', {MUSIC:'music'}), ('com.netease.163music', {MUSIC:'generic'}),
    ('com.tencent.QQMusic', {MUSIC:'generic'}), ('com.spotify.client', {MUSIC:'generic'}),
    ('org.videolan.vlc', {MUSIC:'generic', VIDEO:'generic'}),
    ('com.colliderli.iina', {MUSIC:'generic', VIDEO:'generic'}),
    ('test.any.viewer', {scene:'generic' for scene in TEMPLATES}),
    ('com.apple.Preview', {PDF:'preview', IMAGE:'preview'}),
])
def test_added_apps_save_every_supported_template_and_pending_intention(route, monkeypatch, bundle, modes):
    c, service, *_ = route
    catalog = add(service, monkeypatch, bundle, modes)
    saved = json.loads(c._settings.value(SETTINGS_KEY))[bundle]
    assert catalog.hasDefaultMappings and not catalog.regularBindings[bundle]
    for scene in modes:
        assert set(saved['scenes'][scene]) | set(saved['pendingDefaults'].get(scene, {})) == set(TEMPLATES[scene])
        assert not set(saved['scenes'][scene]) & set(saved['pendingDefaults'].get(scene, {}))
    restored = ApplicationMappingController(service)
    try:
        for scene in modes:
            assert restored._bindings_for(bundle, scene) == saved['scenes'][scene]
        assert restored._apps[bundle]['pendingDefaults'] == saved['pendingDefaults']
    finally:
        restored.close()


@pytest.mark.parametrize('scene,label,shortcut', [(MUSIC,'下一首','Cmd+Right'), (PDF,'下一页','Alt+Down'),
    (IMAGE,'Next Photo','Right'), (VIDEO,'Skip Forward 10 Seconds','Alt+Right'),
    (MUSIC,'提高音量','Cmd+Up'), (VIDEO,'播放/暂停','Space')])
def test_menu_discovery_resolves_only_matching_scene_intent(scene, label, shortcut):
    action = 'next' if scene in {MUSIC,PDF,IMAGE} else 'forward'
    if label == '提高音量': action = 'volume-up'
    if label == '播放/暂停': action = 'play'
    assert menu_action(scene, action, [menu(label, shortcut)])['shortcut'] == shortcut


@pytest.mark.parametrize('scene,action,items', [
    (MUSIC,'next',[menu('Next Tab','Ctrl+Tab')]),
    (PDF,'next',[menu('Next Slide','Right')]),
    (MUSIC,'next',[menu('下一首','Cmd+Right'),menu('Next Track','Ctrl+Right')]),
    (VIDEO,'play',[menu('Play','bad key')]),
    (MUSIC,'next',[menu('Next Track','Cmd+Right','Video Track')]),
])
def test_ambiguous_or_wrong_context_menu_items_never_become_defaults(scene,action,items):
    assert menu_action(scene,action,items) is None


def test_delayed_menus_fill_pending_save_once_and_preserve_user_edits(route, monkeypatch):
    c, service, *_ = route
    catalog=add(service,monkeypatch,modes={MUSIC:'generic'})
    catalog.selectScene(MUSIC)
    assert catalog.pendingBindings['swipe-right']=='下一首'
    assert catalog.pendingDefaultCount==5 and catalog.defaultMappingNotice
    assert not catalog.for_scene('test.media', MUSIC)
    catalog.setCustomBinding('test.media','swipe-left','我的上一首','Ctrl+Left')
    catalog.setBinding('test.media','circle-clockwise','')  # Cancel this unresolved intention.
    actions=[menu('Next Track','Cmd+Right'),menu('Previous Track','Cmd+Left'),
             menu('Play/Pause','Space'),menu('Volume Up','Cmd+Up')]
    catalog._apply_result('menu',catalog._generation['menu'],'test.media',dict(actions=actions), '')
    assert catalog.bindings['test.media']['swipe-left']['shortcut']=='Ctrl+Left'
    assert catalog.bindings['test.media']['swipe-right']['shortcut']=='Cmd+Right'
    assert catalog.bindings['test.media']['tap']['shortcut']=='Space'
    assert catalog.pendingBindings=={'circle-counterclockwise':'降低音量'}
    assert 'circle-clockwise' not in catalog.bindings['test.media']
    saved=c._settings.value(SETTINGS_KEY)
    catalog._apply_result('menu',catalog._generation['menu'],'test.media',dict(actions=actions), '')
    assert c._settings.value(SETTINGS_KEY)==saved
    restored=ApplicationMappingController(service)
    try:
        assert restored._apps['test.media']['pendingDefaults']=={MUSIC:{'circle-counterclockwise':'volume-down'}}
        assert restored._bindings_for('test.media',MUSIC)==catalog.bindings['test.media']
    finally: restored.close()


@pytest.mark.parametrize('operation',['clear','remove'])
def test_clears_and_removals_survive_late_menu_scan_and_restart(route,monkeypatch,operation):
    c,service,*_=route
    catalog=add(service,monkeypatch,modes={MUSIC:'generic',VIDEO:'generic'})
    catalog.selectScene(MUSIC)
    if operation=='clear': catalog.clearApplicationBindings('test.media')
    else: catalog.removeApplication('test.media')
    catalog._apply_result('menu',catalog._generation['menu'],'test.media',dict(actions=[menu('Play','Space')]),'')
    catalog._apply_result('apps',catalog._generation['apps'],'',catalog._candidates,'')
    restored=ApplicationMappingController(service)
    try:
        assert not restored._bindings_for('test.media',MUSIC)
        assert not restored._apps['test.media'].get('pendingDefaults') or operation=='remove'
        if operation=='clear':
            assert restored._apps['test.media']['defaultsCleared']
            assert restored.restoreDefaultMappings('test.media')
            assert restored._apps['test.media']['pendingDefaults'][MUSIC]==TEMPLATES[MUSIC]
        else:
            assert not restored.apps
    finally: restored.close()


def test_existing_scopes_are_user_owned_but_new_capabilities_receive_defaults(route,monkeypatch):
    c,service,*_=route
    old={'test.media':dict(label='Viewer',bindings={},sceneProfiles={MUSIC:'music',PDF:'preview'},
                          scenes={MUSIC:{}})}
    c._settings.setValue(SETTINGS_KEY,json.dumps(old))
    catalog=ApplicationMappingController(service)
    try:
        assert catalog._bindings_for('test.media',MUSIC)=={}  # Explicitly cleared.
        assert len(catalog._bindings_for('test.media',PDF))==2
        assert not catalog._apps['test.media']['pendingDefaults']
        catalog._apply_result('apps',0,'',[dict(value='test.media',label='Viewer',sceneProfiles={IMAGE:'preview'})],'')
        assert len(catalog._bindings_for('test.media',IMAGE))==2
        assert set(json.loads(c._settings.value(SETTINGS_KEY))['test.media']['initializedScenes'])=={MUSIC,PDF,IMAGE}
    finally: catalog.close()


@pytest.mark.parametrize('bundle,scene,gesture,shortcut',[
    ('com.apple.Music',MUSIC,'swipe-right','Right'),
    ('com.colliderli.iina',VIDEO,'swipe-left','Left'),
    ('org.videolan.vlc',MUSIC,'circle-clockwise','Cmd+Up'),
    ('com.apple.Preview',PDF,'swipe-right','Alt+Down'),
    ('com.apple.Preview',IMAGE,'swipe-left','PageUp'),
])
def test_default_bindings_use_existing_scene_and_foreground_guards(presentation,monkeypatch,bundle,scene,gesture,shortcut):
    c,service,_,backend,_,sent,_=presentation
    catalog=add(service,monkeypatch,bundle)
    backend.target=replace(backend.target,bundle=bundle,profile=bundle,scene=scene,input_context='nontext')
    assert not request(c,gesture)
    assert sent==[(bundle,shortcut)]
    for changes in [dict(bundle='other.app'),dict(scene=''),dict(input_context='text'),dict(input_context='unknown'),dict(blocked=True)]:
        baseline=backend.target
        backend.target=replace(baseline,**changes)
        service._last_dispatch = None
        request(c,gesture)
        backend.target=baseline
        assert sent==[(bundle,shortcut)]


def test_restore_reuses_only_the_selected_apps_verified_menu_snapshot(route,monkeypatch):
    _,service,*_=route
    catalog=add(service,monkeypatch,modes={MUSIC:'generic'})
    actions=[menu('Play','Space'),menu('Next Track','Cmd+Right'),menu('Previous Track','Cmd+Left'),
             menu('Volume Up','Cmd+Up'),menu('Volume Down','Cmd+Down')]
    catalog._apply_result('menu',catalog._generation['menu'],'test.media',dict(actions=actions),'')
    assert catalog.bindingCount('test.media')==5
    catalog.clearApplicationBindings('test.media')
    assert catalog.restoreDefaultMappings('test.media')
    assert catalog.bindingCount('test.media')==5 and not catalog.pendingDefaultCount
    catalog._candidates.append(dict(value='test.second',label='Other',sceneProfiles={MUSIC:'generic'}))
    catalog.addApplication('test.second')
    assert catalog.bindingCount('test.second')==0 and catalog.pendingDefaultCount==5
    assert catalog.restoreDefaultMappings('test.media')  # The other app's snapshot cannot supply keys.
    assert catalog.bindingCount('test.media')==0
