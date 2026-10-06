"""Read-only scene feedback: freshness, consecutive proof and deduplicated entry."""
from types import SimpleNamespace
import time
import pytest
from PySide6.QtCore import QCoreApplication
from proximic_ring.ui.scene_notice_controller import SceneNoticeController


@pytest.fixture
def notice():
    app=QCoreApplication.instance() or QCoreApplication([])
    state=SimpleNamespace(front=dict(value='test.player',pid=12,path='/Player.app'),now=10.0,busy=False)
    logs=[]
    catalog=SimpleNamespace(configured_scenes=lambda:{'test.player':['presentation','pdf','image','music','video']},
                            _apps={'test.player':{'label':'Player'}})
    service=SimpleNamespace(catalog=catalog,_generation=1,
        _diagnostics=SimpleNamespace(record=lambda *args,**kw:logs.append((args,kw))))
    calls=[]
    channel=SimpleNamespace(call=lambda *args,**kw:calls.append((args,kw)),close=lambda:None)
    obj=SceneNoticeController(service,foreground=lambda:state.front,clock=lambda:state.now,
                              busy=lambda:state.busy,channel=channel)
    yield obj,state,logs,calls
    obj.close()
    app.processEvents()


def target(scene='presentation',**kw):
    return dict(bundle='test.player',pid=12,scene=scene,input_context='nontext',blocked=False,**kw)


def observe(obj,state,scene='presentation',**kw):
    value=target(scene);value.update(kw)
    obj._observe(value,state.front)


def entered(logs):
    return [item for item in logs if item[0][2]=='scene_entered']


@pytest.mark.parametrize('scene',['presentation','pdf','image','music','video'])
def test_each_mode_entry_is_confirmed_once_then_timeout_does_not_reannounce(notice,scene):
    obj,state,logs,calls=notice
    observe(obj,state,scene)
    assert not obj.visible
    observe(obj,state,scene)
    assert obj.visible and obj.notice['scene']==scene and len(entered(logs))==1
    obj.hide()
    for _ in range(5):observe(obj,state,scene)
    assert not obj.visible and len(entered(logs))==1 and not calls
    observe(obj,state,'');observe(obj,state,'')
    observe(obj,state,scene);observe(obj,state,scene)
    assert obj.visible and len(entered(logs))==2


def test_interleaved_modes_and_focus_interruptions_never_count_as_consecutive(notice):
    obj,state,logs,_=notice
    observe(obj,state);observe(obj,state);obj.hide()
    observe(obj,state,'music');observe(obj,state);observe(obj,state,'music')
    assert not obj.visible
    observe(obj,state,'music')
    assert obj.notice['scene']=='music' and len(entered(logs))==2
    for change in [dict(input_context='text'),dict(blocked=True),dict(input_context='unknown')]:
        observe(obj,state,'music',**change)
        observe(obj,state,'music');observe(obj,state,'music')
        assert not obj.visible and len(entered(logs))==2
    observe(obj,state,'');observe(obj,state,'music');observe(obj,state,'')
    assert obj._active[2]=='music'


@pytest.mark.parametrize('change',['bundle','pid','generation','age','busy','epoch','closed'])
def test_delayed_reply_never_announces_outdated_foreground(notice,change):
    obj,state,logs,_=notice
    front=dict(state.front);obj._reset(front,1);epoch=obj._epoch;obj._inflight=7
    observe(obj,state)
    if change=='bundle':state.front={**front,'value':'other.app'}
    if change=='pid':state.front={**front,'pid':13}
    if change=='generation':obj.service._generation+=1
    if change=='age':state.now+=3
    if change=='busy':state.busy=True
    if change=='epoch':obj._epoch+=1
    if change=='closed':obj.close()
    obj._receive(7,epoch,front,1,10.0,target(),'')
    assert not obj.visible and not entered(logs) and obj._inflight is None


def test_poll_is_read_only_single_flight_and_pauses_for_unconfigured_or_busy(notice,monkeypatch):
    import proximic_ring.ui.scene_notice_controller as module
    obj,state,logs,calls=notice
    pending=[]
    monkeypatch.setattr(module.threading,'Thread',lambda **kw:SimpleNamespace(start=lambda:pending.append(kw['target'])))
    state.busy=True;obj.poll()
    assert not pending
    state.busy=False;state.front={**state.front,'value':'other.app'};obj.poll()
    assert not pending
    state.front={**state.front,'value':'test.player'};obj.poll();obj.poll()
    assert len(pending)==1
    pending.pop()();QCoreApplication.processEvents()
    assert calls==[(('scene_observe',),dict(expected_bundle='test.player',expected_pid=12))]
    assert not obj.visible and not entered(logs)


def test_permission_errors_back_off_and_log_only_once(notice):
    obj,state,logs,calls=notice
    obj._reset(state.front,1)
    for i in range(2):
        obj._inflight=i
        obj._receive(i,obj._epoch,state.front,1,state.now,None,'permission_denied')
        obj.poll()
    assert not calls and sum(item[0][2]=='observation_unavailable' for item in logs)==1 and not obj.visible
    assert obj._next_read==15.0


def test_worker_scene_observation_requires_ax_only_and_never_keeps_send_targets(monkeypatch):
    from proximic_ring import native_access_worker as worker
    from proximic_ring.mac_permissions import MacPermissionError,PermissionState
    from proximic_ring.app_shortcuts import ShortcutTarget
    monkeypatch.setattr(worker,'read_permission_state',lambda:PermissionState(True,False))
    dispatcher=worker.Dispatcher();captures=[]
    value=ShortcutTarget('test.player',12,'generic',scene='video',input_context='nontext')
    dispatcher.shortcuts=SimpleNamespace(capture=lambda **kw:captures.append(kw) or value)
    message=dict(operation='scene_observe',expected_bundle='test.player',expected_pid=12)
    for _ in range(100):
        result=dispatcher.handle(message)['result']
        assert result['scene']=='video' and 'remote_id' not in result
    assert not dispatcher.targets and captures[0]==dict(menu_action=True,scene=True,scene_observation=True)
    assert dispatcher.handle({**message,'expected_pid':13})['result'] is None
    assert dispatcher.handle({**message,'expected_bundle':'other.app'})['result'] is None
    monkeypatch.setattr(worker,'read_permission_state',lambda:PermissionState(False,True))
    with pytest.raises(MacPermissionError):dispatcher.handle(message)


def test_background_polls_show_entry_without_any_gesture(notice):
    obj,state,logs,calls=notice
    obj._channel.call=lambda *args,**kw:calls.append((args,kw)) or target('music')
    for _ in range(2):
        obj.poll()
        for _ in range(200):
            QCoreApplication.processEvents()
            if obj._inflight is None:break
            time.sleep(.001)
        assert obj._inflight is None
    assert obj.visible and obj.notice['scene']=='music' and len(entered(logs))==1
    assert all(call[0]==('scene_observe',) for call in calls)


def test_pause_reason_is_recorded_once_and_resume_is_observable(notice):
    obj,state,logs,_=notice
    state.busy='permission_prompt'
    obj.poll();obj.poll();obj.poll()
    paused=[item for item in logs if item[0][2]=='observation_paused']
    assert len(paused)==1 and paused[0][1]['reason_code']=='permission_prompt'
    state.busy=False;obj.poll()
    assert any(item[0][2]=='observation_resumed' for item in logs)


def test_confirmation_uses_short_timer_but_respects_new_busy_state(notice,monkeypatch):
    from PySide6.QtTest import QTest
    obj,state,logs,calls=notice
    obj._channel.call=lambda *args,**kw:calls.append(kw) or target()
    obj.poll()
    for _ in range(100):
        QCoreApplication.processEvents()
        if obj._inflight is None:break
        time.sleep(.001)
    assert not obj.visible and obj._confirm_timer.isActive()
    deadline=time.monotonic()+1.0
    while not obj.visible and time.monotonic()<deadline:
        QCoreApplication.processEvents()
        time.sleep(.001)
    assert obj.visible and len(calls)==2 and not obj._confirm_timer.isActive()
    obj._reset(state.front,1);obj.poll()
    for _ in range(100):
        QCoreApplication.processEvents()
        if obj._inflight is None:break
        time.sleep(.001)
    state.busy='permission_prompt';QTest.qWait(240)
    assert not obj.visible and not obj._confirm_timer.isActive()
    assert len(calls)==3


def test_real_permission_controller_pending_does_not_block_background_scene_read(notice,tmp_path):
    from PySide6.QtCore import QSettings
    from PySide6.QtTest import QTest
    from test_permission_setup import Permissions,PermissionApp
    from proximic_ring.ui.permission_setup_controller import PermissionSetupController
    from proximic_ring.ui.feedback_availability import feedback_block_reason
    obj,state,logs,calls=notice
    perms=Permissions();perms.states.update(bluetooth=True,microphone=True)
    setup=PermissionSetupController(perms,QSettings(str(tmp_path/'permissions.ini'),QSettings.IniFormat),
        enabled=True,permission_app=PermissionApp(perms),is_active=lambda:False)
    owner=SimpleNamespace(permissionSetup=setup,
        ringGestures=SimpleNamespace(speech_busy=lambda:False,windowSelector=SimpleNamespace(phase='closed')),
        appGestures=SimpleNamespace(recording=False))
    try:
        setup.startIfNeeded();QTest.qWait(20)
        assert setup.active and not setup.blocksFeedback
        obj._busy=lambda:feedback_block_reason(owner)
        obj._channel.call=lambda *args,**kw:calls.append(kw) or target()
        obj.poll()
        deadline=time.monotonic()+1.0
        while not obj.visible and time.monotonic()<deadline:
            QCoreApplication.processEvents()
            time.sleep(.001)  # Let the observation worker deliver its queued reply.
        assert obj.visible and len(entered(logs))==1 and not perms.requests
    finally:setup.close()


def test_failed_observations_log_reason_once_and_record_recovery(notice):
    obj,state,logs,_=notice
    failed=dict(diagnostic={'reason':'no_content_evidence','recognition':{'play_control':True,'seek_control':True}})
    for _ in range(5):observe(obj,state,'',**failed)
    results=[item for item in logs if item[0][2]=='observation_result']
    assert len(results)==1 and results[0][1]['reason_code']=='no_content_evidence'
    assert results[0][1]['native']['recognition']['play_control']
    observe(obj,state,'music',input_context='text')
    observe(obj,state,'music');observe(obj,state,'music')
    results=[item for item in logs if item[0][2]=='observation_result']
    assert [item[1]['reason_code'] for item in results]==['no_content_evidence','text_focus','ready']
    assert obj.visible and obj.notice['scene']=='music'
