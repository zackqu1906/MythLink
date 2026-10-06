"""Consent, latest-foreground ownership, lifecycle and single-purpose routing."""
from dataclasses import replace
import json
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication

from proximic_ring.application_scene_policy import primary_scene, enabled_profiles
from proximic_ring.scene_capabilities import application_scene_profiles
from proximic_ring.ui.application_onboarding import ApplicationOnboarding, DECISIONS_KEY
from proximic_ring.ui.application_mapping_controller import ApplicationMappingController, SETTINGS_KEY
from test_app_gestures import route
from test_scene_defaults import add
from test_gesture_scenes import presentation
from test_ring_gestures import request


@pytest.mark.parametrize('bundle,scene', [
    ('com.microsoft.Powerpoint','presentation'), ('com.kingsoft.wpsoffice.mac','presentation'),
    ('com.apple.Music','music'), ('com.spotify.client','music'), ('com.apple.Preview','pdf'),
    ('com.apple.Photos','image'), ('org.videolan.vlc','video'), ('com.colliderli.iina','video'),
])
def test_primary_purpose_is_one_scene_and_does_not_reduce_detection_capabilities(bundle, scene):
    profiles = application_scene_profiles(bundle)
    before = dict(profiles)
    assert primary_scene(bundle, profiles) == scene
    assert list(enabled_profiles(bundle, profiles)) == [scene]
    assert profiles == before
    if scene=='presentation':
        assert primary_scene(bundle, {**profiles,'pdf':'generic'}, 'pdf')=='pdf'


def test_unknown_apps_use_declared_category_or_explicit_purpose_and_browsers_keep_scenes():
    profiles = {'pdf':'generic', 'music':'generic', 'video':'generic'}
    assert primary_scene('test.player', profiles, category='public.app-category.music') == 'music'
    assert primary_scene('test.player', profiles, 'video') == 'video'
    assert enabled_profiles('com.apple.Safari', profiles) == {'pdf':'generic','video':'generic'}
    metadata=dict(CFBundleIdentifier='test.music', LSApplicationCategoryType='public.app-category.music')
    assert application_scene_profiles('test.music',metadata)=={'music':'generic'}
    assert application_scene_profiles('other.app',metadata)=={}


@pytest.fixture
def onboarding(route, monkeypatch):
    c, service, *_ = route
    monkeypatch.setattr(service.catalog, '_request', lambda *args: None)
    state = SimpleNamespace(now=0.0, front=dict(value='test.player',pid=123,path='/Applications/Player.app'), busy=False)
    candidate = dict(value='test.player', label='Player', path='/Applications/Player.app',
                     sceneProfiles={'music':'music','video':'generic'},primaryScene='music')
    obj = ApplicationOnboarding(service.catalog, foreground=lambda: state.front,
        metadata=lambda path: dict(candidate),clock=lambda:state.now,busy=lambda:state.busy)
    yield obj, state, candidate, service.catalog, c
    obj.close()


def show(obj, state):
    obj.poll()
    state.now += 2.1
    obj.poll()
    for _ in range(100):
        QCoreApplication.processEvents()
        if obj.visible: return
        time.sleep(.001)
    assert obj.visible


def test_discovery_does_not_add_until_confirmed_then_persists_only_primary_scene(onboarding):
    obj,state,candidate,catalog,c = onboarding
    before=c.gestureBindings
    obj.poll()
    assert not obj.visible and not catalog.apps
    show(obj,state)
    assert not catalog.apps and len(obj.preview)==5
    assert obj.offer['primaryScene']=='music'
    assert obj.accept()
    assert not obj.visible and catalog._is_added('test.player')
    saved=json.loads(c._settings.value(SETTINGS_KEY))['test.player']
    assert set(saved['scenes'])=={'music'} and saved['primaryScene']=='music'
    assert catalog.configured_scenes()=={'test.player':['music']}
    assert c.gestureBindings==before
    restored=ApplicationMappingController(catalog.service)
    try: assert restored.configured_scenes()==catalog.configured_scenes()
    finally: restored.close()


@pytest.mark.parametrize('change',['app','pid','path','unknown','already_added','removed','busy'])
def test_stale_offer_never_configures_a_different_or_removed_app(onboarding, change):
    obj,state,candidate,catalog,c=onboarding
    show(obj,state)
    if change=='app': state.front={**state.front,'value':'other.app'}
    if change=='pid': state.front={**state.front,'pid':456}
    if change=='path': state.front={**state.front,'path':'/Applications/Other.app'}
    if change=='unknown': state.front=None
    if change=='busy': state.busy=True
    if change in {'already_added','removed'}:
        assert catalog.addDiscoveredApplication(candidate)
        if change=='removed': catalog.removeApplication(candidate['value'])
    saved=c._settings.value(SETTINGS_KEY)
    assert not obj.accept() and not obj.visible
    assert c._settings.value(SETTINGS_KEY)==saved


def test_dismiss_is_persistent_without_adding_and_manual_add_still_works(onboarding):
    obj,state,candidate,catalog,c=onboarding
    show(obj,state); obj.dismiss()
    assert not catalog.apps and json.loads(c._settings.value(DECISIONS_KEY))==['test.player']
    another=ApplicationOnboarding(catalog,foreground=lambda:state.front,clock=lambda:state.now)
    try:
        another.poll(); state.now+=10; another.poll()
        assert not another.visible and not another._loading
    finally: another.close()
    assert catalog.addDiscoveredApplication(candidate)


def test_late_metadata_and_switching_apps_do_not_show_old_offer(onboarding):
    obj,state,candidate,catalog,c=onboarding
    obj.poll(); epoch=obj._epoch; target=dict(state.front)
    state.front={**state.front,'value':'other.app'}; obj.poll()
    obj._ready(epoch,target,candidate)
    assert not obj.visible and not catalog.apps


def test_busy_and_existing_apps_suppress_prompt_and_timeout_does_not_reprompt(onboarding):
    obj,state,candidate,catalog,c=onboarding
    state.busy=True
    obj.poll(); state.now+=4; obj.poll()
    assert not obj.visible and not obj._loading
    state.busy=False; show(obj,state)
    state.now+=46; obj.poll(); obj.poll()
    assert not obj.visible and not obj._loading and not catalog.apps


def test_can_choose_another_purpose_before_accepting(onboarding):
    obj,state,candidate,catalog,c=onboarding
    show(obj,state); obj.chooseScene('video')
    assert obj.offer['primaryScene']=='video'
    assert obj.accept()
    assert catalog.configured_scenes()['test.player']==['video']
    assert set(catalog._apps['test.player']['scenes'])=={'video'}


def test_adding_purpose_keeps_existing_scene_and_user_keys(route, monkeypatch):
    _,service,*_=route
    catalog=add(service,monkeypatch,'com.apple.Preview')
    assert catalog.availableScenes==[dict(value='regular',label='默认配置'),dict(value='pdf',label='PDF 阅读')]
    catalog.selectScene('pdf')
    catalog.setCustomBinding('com.apple.Preview','swipe-right','我的翻页','Cmd+Right')
    assert catalog.setPrimaryScene('image')
    assert catalog.configured_scenes()['com.apple.Preview']==['pdf','image']
    assert catalog.for_scene('com.apple.Preview','pdf')
    assert catalog._bindings_for('com.apple.Preview','pdf')['swipe-right']['shortcut']=='Cmd+Right'
    assert catalog.setPrimaryScene('pdf')
    assert catalog.for_scene('com.apple.Preview','pdf')['scene:swipe-right'].shortcut=='Cmd+Right'


def test_complete_application_menu_replaces_only_untouched_factory_keys(route, monkeypatch):
    _,service,*_=route
    bundle='com.apple.Music'
    catalog=add(service,monkeypatch,bundle)
    catalog.selectScene('music')
    catalog.setCustomBinding(bundle,'swipe-left','我的上一首','Ctrl+Left')
    catalog.setBinding(bundle,'tap','')
    actions=[dict(id='menu-next',label='Next Track',path='Playback',shortcut='Alt+Right')]
    catalog._apply_result('menu',catalog._generation['menu'],bundle,dict(actions=actions,partial=True),'')
    assert catalog.bindings[bundle]['swipe-right']['shortcut']=='Right'
    catalog._apply_result('menu',catalog._generation['menu'],bundle,dict(actions=actions),'')
    assert catalog.bindings[bundle]['swipe-right']['shortcut']=='Alt+Right'
    assert catalog.bindings[bundle]['swipe-left']['shortcut']=='Ctrl+Left' and 'tap' not in catalog.bindings[bundle]


@pytest.mark.parametrize('scene,bundle,gesture,shortcut', [
    ('presentation','com.microsoft.Powerpoint','swipe-right','Right'),
    ('music','com.apple.Music','swipe-right','Right'),
    ('video','com.colliderli.iina','swipe-right','Right'),
    ('pdf','com.apple.Preview','swipe-right','Alt+Down'),
    ('image','com.apple.Photos','swipe-right','Cmd+Right'),
])
def test_each_primary_scene_routes_only_live_nontext_target(presentation,monkeypatch,scene,bundle,gesture,shortcut):
    c,service,inline,backend,_,sent,_=presentation
    catalog=add(service,monkeypatch,bundle)
    assert catalog.setPrimaryScene(scene)
    if scene=='image': catalog.setCustomBinding(bundle,gesture,'下一张',shortcut)
    backend.target=replace(backend.target,bundle=bundle,scene=scene,input_context='nontext')
    before=c.gestureBindings
    assert not request(c,gesture) and sent==[(bundle,shortcut)]
    original=backend.target
    for change in [dict(bundle='other.app'),dict(input_context='text'),dict(input_context='unknown'),dict(blocked=True),dict(scene='')]:
        backend.target=replace(original,**change)
        service._last_dispatch=None
        request(c,gesture)
        assert sent==[(bundle,shortcut)]
    assert c.gestureBindings==before
