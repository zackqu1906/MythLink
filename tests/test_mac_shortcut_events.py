"""Modifier-aware application regression and native delivery failure boundaries."""
from dataclasses import replace
import sys
import pytest
from proximic_ring.mac_shortcut_events import prepare_chord, MODIFIER_KEYS
from proximic_ring.scene_diagnostics import SceneActionError
from test_app_shortcuts import desktop


@pytest.mark.parametrize('bundle',['com.kingsoft.wpsoffice.mac','com.microsoft.Powerpoint','com.apple.Safari','other.app'])
def test_shortcut_reaches_apps_that_track_modifier_transitions(desktop,bundle):
    backend,state,quartz=desktop
    state.target=replace(state.target,bundle=bundle)
    held=set();actions=[]
    def receive(pid,event):
        assert pid==42
        code=event['code']
        if code==36 and event['down']:
            actions.append('start-current' if 55 in held else 'new-slide')
        if event['down']:held.add(code)
        else:held.discard(code)
        state.sent.append((pid,event))
    quartz.CGEventPostToPid=receive
    backend.post(state.target,'Cmd+Return')
    assert actions==['start-current'] and not held
    assert backend.last_diagnostic['modifier_keys']==['Cmd']
    assert backend.last_diagnostic['events_posted']==4
    assert backend.last_diagnostic['event_sequence']=='balanced_modifiers_v1'


@pytest.mark.parametrize('failed_index',range(6))
def test_any_allocation_failure_sends_nothing_including_modifiers(desktop,failed_index):
    backend,state,quartz=desktop
    make=quartz.CGEventCreateKeyboardEvent;calls=[]
    def allocate(source,code,down):
        calls.append((code,down))
        return None if len(calls)-1==failed_index else make(source,code,down)
    quartz.CGEventCreateKeyboardEvent=allocate
    with pytest.raises(SceneActionError,match='创建快捷键'):
        backend.post(state.target,'Cmd+Shift+Return')
    assert not state.sent


@pytest.mark.parametrize('failed_index',range(6))
def test_partial_delivery_releases_keys_and_never_replays_keydown(desktop,failed_index):
    backend,state,quartz=desktop
    attempts=[];held=set()
    def post(pid,event):
        code,down=event['code'],event['down']
        attempts.append((pid,code,down))
        # Worst case: the post takes effect and then raises.
        if down:held.add(code)
        else:held.discard(code)
        if len(attempts)-1==failed_index:raise RuntimeError('transport failed')
    quartz.CGEventPostToPid=post
    with pytest.raises(RuntimeError):backend.post(state.target,'Cmd+Shift+Return')
    assert not held
    assert all(not down for _,_,down in attempts[failed_index+1:])
    assert len([x for x in attempts if x[1:]==(36,True)])<=1
    assert backend.last_diagnostic['delivery']=='unknown'
    assert backend.last_diagnostic['cleanup_failures']==0


def test_foreground_change_during_allocation_cancels_before_modifier_down(desktop):
    backend,state,quartz=desktop
    original=state.target
    make=quartz.CGEventCreateKeyboardEvent
    def allocate(*args):
        state.target=replace(state.target,pid=999)
        return make(*args)
    quartz.CGEventCreateKeyboardEvent=allocate
    with pytest.raises(SceneActionError) as error:backend.post(original,'Cmd+Return')
    assert error.value.reason=='foreground_changed' and not state.sent


def test_modifier_only_flags_never_survive_a_completed_chord(desktop):
    backend,state,quartz=desktop
    backend.post(state.target,'Cmd+Ctrl+Alt+Shift+Fn+Return')
    codes=[MODIFIER_KEYS[k][0] for k in ['Cmd','Ctrl','Alt','Shift','Fn']]
    assert [e['code'] for _,e in state.sent]==codes+[36,36]+list(reversed(codes))
    assert state.sent[-1][1]['flags']==0
    assert len(state.sent)==12


@pytest.mark.skipif(sys.platform!='darwin',reason='macOS event construction without sending')
def test_real_coregraphics_creates_flags_changed_events_without_posting():
    import Quartz
    from proximic_ring.app_shortcuts import verify_native_api
    verify_native_api()
    events,_,_=prepare_chord(Quartz,'Cmd+Shift+Return')
    assert [Quartz.CGEventGetType(event) for _,_,event in events]==[12,12,10,11,12,12]
    assert [code for code,_,_ in events]==[55,56,36,36,56,55]
    assert Quartz.CGEventGetFlags(events[-1][2])==0


def test_cleanup_failure_still_attempts_remaining_modifier_releases(desktop):
    backend,state,quartz=desktop
    attempts=[]
    def post(pid,event):
        attempts.append((event['code'],event['down']))
        if event['code']==36:raise RuntimeError('failed key delivery')
    quartz.CGEventPostToPid=post
    with pytest.raises(RuntimeError):backend.post(state.target,'Cmd+Shift+Return')
    assert attempts==[(55,True),(56,True),(36,True),(36,False),(56,False),(55,False)]
    assert backend.last_diagnostic['cleanup_failures']==1
    assert backend.last_diagnostic['cleanup_events_posted']==2
