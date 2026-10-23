import threading
import time
from types import SimpleNamespace
import pytest

from test_inline_ui import inline_ui
from test_agent_workspace import request
from test_ui_shell import visual_child
from test_stroke_session import stroke, wake, until


def finish_fixture_voice(owner):
    owner._utterance_active=False
    owner._inline_input._view.update(phase='dictated',ready=True,awaiting_readback=False,has_composition=False)


@pytest.mark.parametrize('switch_to_main', [False, True])
def test_retiring_agent_card_only_restores_focus_inside_agent(inline_ui, switch_to_main):
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtTest import QTest
    from proximic_ring.ui.agent.agent_overlay import AgentOverlay

    owner, _, _, main, _ = inline_ui
    agent = owner.agent
    finish_fixture_voice(owner)
    agent._visible = True
    agent._apply_state({'requests': [request('permission')]})
    overlay = AgentOverlay(agent, owner)
    try:
        overlay.focus('permission')
        QTest.qWait(40)
        assert agent.focused and QGuiApplication.focusWindow() is overlay.windows['permission']
        agent.edit('task', '保留的任务草稿')
        agent._apply_state({'requests': []})
        if switch_to_main:
            main.show()
            main.requestActivate()
        QTest.qWait(250)
        assert 'permission' not in overlay.windows
        assert agent.draft('task') == '保留的任务草稿'
        if switch_to_main:
            assert QGuiApplication.focusWindow() is main
            assert not agent.focused and not agent.owns_input_focus()
        else:
            assert QGuiApplication.focusWindow() is overlay.windows['task']
            assert agent.focused
    finally:
        overlay.close()
        main.hide()


def test_agent_question_choices_submit_with_swipe_up_and_permissions_are_mouse_only(inline_ui, monkeypatch):
    from PySide6.QtCore import QCoreApplication
    owner=inline_ui[0];agent=owner.agent;sent=[]
    agent._auto_context_mode='off'  # This test isolates routing, not desktop capture.
    finish_fixture_voice(owner)
    owner._inline_input._view['has_composition']=False
    monkeypatch.setattr(agent,'_send',sent.append)
    owner._disconnect_event=threading.Event()
    agent._visible=agent._focused=True
    agent._apply_state({'turnId':'turn','requests':[request('permission'),request('answer','question')]})
    agent.workspace.focus='permission'
    for name in ('tap','double-tap','swipe-up','swipe-down','swipe-left','swipe-right','middle-pinch'):
        assert agent.filter_gesture(SimpleNamespace(name=name),owner._disconnect_event)
    QCoreApplication.processEvents()
    assert sent==[] and agent.workspace.focus=='permission'
    agent.choose('permission',1)
    assert sent.pop()=={'type':'choose','token':'permission','index':1}
    agent.workspace.focus='task';agent.editorFocused('answer',True);agent.edit('answer','选第二个')
    assert not agent.filter_gesture(SimpleNamespace(name='middle-pinch'),owner._disconnect_event)
    QCoreApplication.processEvents();assert sent==[]
    assert agent.filter_gesture(SimpleNamespace(name='swipe-up'),owner._disconnect_event)
    QCoreApplication.processEvents()
    assert sent.pop()=={'type':'choose','token':'answer','index':1}
    agent._apply_state({'turnId':'turn','requests':[]})
    agent.edit('task','改为前20项');agent.submit('task')
    assert sent.pop()=={'type':'steer','targetTurn':'turn','text':'改为前20项'}


def test_typing_and_submitting_do_not_pause_running_task(inline_ui, monkeypatch):
    owner=inline_ui[0];agent=owner.agent;sent=[]
    agent._auto_context_mode='off'
    finish_fixture_voice(owner)
    monkeypatch.setattr(agent,'_send',sent.append)
    agent._focused=True
    agent._apply_state({'turnId':'turn'})
    agent.edit('task','修改')
    assert sent==[]
    agent.submit('task')
    assert sent==[{'type':'steer','targetTurn':'turn','text':'修改'}]
    assert not hasattr(agent,'pauseWhileInput') and not hasattr(agent,'pause')


def test_composer_mouse_clicks_send_steer_and_cancel(inline_ui, monkeypatch, tmp_path):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtTest import QTest
    from proximic_ring.ui.agent.agent_overlay import AgentOverlay
    owner=inline_ui[0];agent=owner.agent;sent=[]
    agent._auto_context_mode='off'
    finish_fixture_voice(owner)
    monkeypatch.setattr(agent,'_send',sent.append)
    agent._visible=agent._focused=True
    overlay=AgentOverlay(agent,owner)
    overlay.reduce_motion=True
    try:
        overlay.focus('task');QTest.qWait(40)
        window=overlay.windows['task'];root=window.rootObject()
        editor=visual_child(root,'agentEditor')
        button=visual_child(root,'agentSendButton')
        icon=visual_child(root,'agentSendIcon')

        def click(item):
            QTest.qWait(30)
            assert item.isEnabled() and item.isVisible()
            point=item.mapToScene(QPointF(item.width()/2,item.height()/2)).toPoint()
            QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,point)
            QTest.qWait(30)

        def check_icon(action):
            QTest.qWait(30)
            assert button.property('actionKind')==icon.property('action')==action
            centre=icon.mapToItem(button,QPointF(icon.width()/2,icon.height()/2))
            assert centre.x()==pytest.approx(button.width()/2)
            assert centre.y()==pytest.approx(button.height()/2)
            grab=button.grabToImage();QTest.qWait(30)
            pixels=grab.image()
            dark=[(x,y) for x in range(pixels.width()) for y in range(pixels.height())
                  if pixels.pixelColor(x,y).alpha()>200 and pixels.pixelColor(x,y).red()<50]
            assert len(dark)>10  # The icon must actually render, not leave a blank circle.
            assert window.grabWindow().save(str(tmp_path/f'composer-{action}.png'))

        assert not button.isEnabled()
        editor.setProperty('text','查找昨天的论文')
        check_icon('send');click(button)
        assert sent==[{'type':'text','text':'查找昨天的论文','targetTurn':''}]
        assert editor.property('text')==''
        agent._apply_state({'turnId':'turn','status':'执行中','agent':'正在查找论文…'})
        assert visual_child(root,'agentPauseButton') is None
        check_icon('cancel')
        editor.setProperty('text','只保留前20项')
        check_icon('send');click(button)
        assert sent[-1]=={'type':'steer','targetTurn':'turn','text':'只保留前20项'}
        check_icon('cancel');click(button)
        assert sent[-1]=={'type':'cancel'}
        agent._apply_state({'status':'已完成','agent':'已保存笔记'})
        assert button.property('actionKind')=='send' and not button.isEnabled()
        editor.setProperty('text','新的任务')
        click(button)
        assert sent[-1]=={'type':'text','text':'新的任务','targetTurn':''}
        assert sum(command['type']=='cancel' for command in sent)==1
    finally:overlay.close()


@pytest.mark.parametrize('native', [False, True])
def test_agent_mouse_controls_keep_question_answers_and_permissions_separate(inline_ui, monkeypatch, native):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtTest import QTest
    from proximic_ring.ui.agent.agent_overlay import AgentOverlay
    owner=inline_ui[0];agent=owner.agent;sent=[]
    opened=[]
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda url: opened.append(url.toString()) or True)
    metadata={'turnId':'turn', 'nativeExecution':native,
              'codexThreadId':'00000000-0000-4000-8000-000000000001'}
    finish_fixture_voice(owner)
    monkeypatch.setattr(agent,'_send',sent.append)
    agent._visible=agent._focused=True
    overlay=AgentOverlay(agent,owner);overlay.reduce_motion=True
    try:
        agent._apply_state({**metadata,'requests':[request('question','question'),request('permission')]})
        overlay.focus('task');QTest.qWait(40)
        window=overlay.windows['task'];root=window.rootObject()
        editor=visual_child(root,'agentEditor')

        def click(target,name):
            QTest.qWait(30)
            item=visual_child(target.rootObject(),name)
            assert item.isVisible() and item.isEnabled()
            point=item.mapToScene(QPointF(item.width()/2,item.height()/2)).toPoint()
            QTest.mouseClick(target,Qt.LeftButton,Qt.NoModifier,point)
            QTest.qWait(30)

        editor.setProperty('text','选第二个')
        click(window,'agentSendButton')
        assert sent==[{'type':'choose','token':'question','index':1}]
        agent._apply_state({**metadata,'requests':[request('reply','question'),request('permission')]})
        editor.setProperty('text','请使用 Safari 查找论文')
        click(window,'agentSendButton')
        assert sent[-1]=={'type':'answer','questionToken':'reply','text':'请使用 Safari 查找论文'}
        click(window,'agentApprovalModeButton')
        if native:
            assert agent.autoApprove
            assert agent.approvalMode == 'all'
            label = visual_child(root, 'agentApprovalLabel')
            assert label.property('text') == '全部授权'
            assert label.property('color').name() == '#f5a45d'
            assert not opened
            click(window,'agentApprovalModeButton')
            assert agent.approvalMode == 'codex' and not agent.autoApprove
            assert label.property('color').name() == '#b9b9b9'
            click(window,'agentApprovalModeButton')
            assert agent.approvalMode == 'auto' and agent.autoApprove
        else:
            assert not agent.autoApprove
            assert not opened
        assert visual_child(root,'agentPauseButton') is None
        agent._apply_state({**metadata,'requests':[request('permission')]})
        click(overlay.windows['permission'],'agentOption1')
        assert sent[-1]=={'type':'choose','token':'permission','index':1}
        editor.setProperty('text', '')
        click(window,'agentSendButton')
        assert sent[-1]=={'type':'cancel'}
    finally:overlay.close()


def test_question_arrival_preserves_draft_without_control_commands(inline_ui, monkeypatch):
    owner=inline_ui[0];agent=owner.agent;sent=[]
    finish_fixture_voice(owner)
    monkeypatch.setattr(agent,'_send',sent.append)
    agent._focused=True
    agent._apply_state({'turnId':'turn'})
    agent.edit('task','草稿')
    agent._apply_state({'turnId':'turn','requests':[request('q','question')]})
    assert agent.inputKey=='q' and agent.draft('task')=='草稿'
    assert sent==[]


def test_old_gesture_cannot_confirm_new_card_or_connection(inline_ui, monkeypatch):
    owner=inline_ui[0];agent=owner.agent;sent=[]
    monkeypatch.setattr(agent,'_send',sent.append)
    owner._disconnect_event=threading.Event();agent._focused=True
    agent._apply_state({'requests':[request('old')]});agent.workspace.focus='old'
    agent._apply_state({'requests':[request('new')]})
    agent._gesture('tap','old',owner._disconnect_event,time.monotonic())
    agent._gesture('tap','new',threading.Event(),time.monotonic())
    assert sent==[]


def test_agent_merges_question_into_task_and_keeps_mouse_permissions_and_compact_progress(inline_ui, tmp_path):
    from PySide6.QtCore import QObject
    from PySide6.QtTest import QTest
    from proximic_ring.ui.agent.agent_overlay import AgentOverlay
    owner=inline_ui[0];agent=owner.agent
    finish_fixture_voice(owner)
    agent._visible=agent._focused=True
    overlay=AgentOverlay(agent,owner)
    try:
        agent._apply_state({'turnId':'turn','status':'执行中','agent':'正在整理论文重点。',
                            'requests':[request('permission'),request('question','question')]})
        overlay.refresh();overlay.layout();QTest.qWait(350)
        assert list(overlay.windows)==['task','permission']
        assert agent.inputKey=='question'
        assert visual_child(overlay.windows['task'].rootObject(),'agentChoiceText0').property('text').startswith('1.  ')
        assert visual_child(overlay.windows['task'].rootObject(),'agentOption0') is None
        assert overlay.windows['task'].y() > overlay.windows['permission'].y()
        button=visual_child(overlay.windows['permission'].rootObject(),'agentOption1')
        assert button is not None and button.property('text')=='始终允许'
        assert button.property('chosen') is False
        assert button.height() >= 32
        send=visual_child(overlay.windows['task'].rootObject(),'agentSendButton')
        assert send.property('contentItem').width()==send.width()==30
        assert send.property('contentItem').height()==send.height()==30
        icon=visual_child(overlay.windows['task'].rootObject(),'agentSendIcon')
        assert icon is not None
        grab=send.grabToImage();QTest.qWait(40)
        pixels=grab.image()
        colors=[pixels.pixelColor(x,y) for x in range(pixels.width()) for y in range(pixels.height())]
        assert sum(c.alpha()>200 and c.red()>245 and c.green()>245 for c in colors)>100
        assert sum(c.alpha()>200 and c.red()<50 and c.green()<50 for c in colors)>10
        assert overlay.windows['permission'].grabWindow().save(str(tmp_path/'agent-permission.png'))
        # A stored answer survives a focus-only collapse.
        agent.edit('question','保留答案');agent.workspaceFocused(False)
        QTest.qWait(80)
        assert 344 < overlay.windows['task'].width() < 440
        QTest.qWait(300)
        assert overlay.windows['task'].isVisible()
        assert overlay.windows['task'].width()==344
        assert agent.workspace.drafts['question']=='保留答案'
        agent._apply_state({'turnId':'turn','requests':[request('question','question')]})
        assert 'permission' in overlay.retiring
        assert not overlay.windows['permission'].rootObject().isEnabled()
        QTest.qWait(350)
        assert 'permission' not in overlay.windows
    finally:overlay.close()


@pytest.mark.parametrize('key',['task','question'])
@pytest.mark.parametrize('gesture,phase,command',[
    ('swipe-left','dictated','cancel'),('swipe-right','listening','convert'),
    ('tap','dictated','cancel'),('double-tap','dictated','cancel'),
    ('snap','dictated','cancel'),('tap','listening','convert'),('snap','listening','convert')])
def test_agent_editor_uses_original_native_voice_actions(inline_ui,monkeypatch,key,gesture,phase,command):
    from PySide6.QtCore import QCoreApplication
    from proximic_ring.gestures.gesture_settings import GestureBindings
    owner,bridge,*_=inline_ui;agent=owner.agent
    owner._save_gesture_bindings(GestureBindings(confirm=('circle-clockwise',''), end=('circle-clockwise',''),
        undo=(gesture,'') if command=='cancel' else ('',''),
        switch_mode=(gesture,'') if command=='convert' else ('','')))
    owner._disconnect_event=threading.Event()
    agent._focused=True
    agent._apply_state({'requests':[request('question','question')]})
    agent.workspace.focus='task';agent.workspace.input_key=key;agent.editorFocused(key,True)
    owner._inline_input._view.update(phase=phase,ready=True,has_composition=True,awaiting_readback=False)
    monkeypatch.setattr(agent,'_send',lambda _:pytest.fail('input swipe must not control the Agent task'))
    monkeypatch.setattr(owner._app_gestures.backend,'capture',lambda **_:pytest.fail('owned editor must not be blocked as an AX dialog'))
    bridge.messages.clear()
    assert agent.filter_gesture(SimpleNamespace(name=gesture),owner._disconnect_event)
    QCoreApplication.processEvents()
    assert any(m['type']==command for m in bridge.messages)


def test_agent_gestures_follow_saved_changes_and_drop_queued_old_mapping(inline_ui, monkeypatch):
    from PySide6.QtCore import QCoreApplication
    from proximic_ring.gestures.gesture_settings import GestureBindings
    owner = inline_ui[0]; agent = owner.agent
    finish_fixture_voice(owner)
    owner._disconnect_event = threading.Event()
    agent._focused = True
    agent.editorFocused('task', True)
    actions = []
    monkeypatch.setattr(owner, '_apply_voice_action', lambda action, **_: actions.append(action))
    monkeypatch.setattr(owner._app_gestures.catalog, 'uses', lambda _: True)
    monkeypatch.setattr(owner._app_gestures.backend, 'capture', lambda **_: pytest.fail('known Agent editor'))
    assert owner.setGestureBinding('confirm', 0, 'tap')
    assert GestureBindings.from_json(owner._settings.value('input/gestureBindings')) == owner._gesture_bindings
    assert owner._ring_gestures.filter(SimpleNamespace(name='tap'), False, owner._disconnect_event)
    assert agent.filter_gesture(SimpleNamespace(name='swipe-left'), owner._disconnect_event)
    assert owner.setGestureBinding('undo', 0, 'snap')
    QCoreApplication.processEvents()
    assert actions == []  # Previous queued swipe cannot use the new settings.
    assert agent.filter_gesture(SimpleNamespace(name='snap'), owner._disconnect_event)
    QCoreApplication.processEvents()
    assert actions == ['undo']
    assert not agent.filter_gesture(SimpleNamespace(name='swipe-left'), owner._disconnect_event)


def test_old_input_swipe_cannot_undo_another_sentence_or_editor(inline_ui,monkeypatch):
    owner=inline_ui[0];agent=owner.agent
    owner._disconnect_event=threading.Event();agent._focused=True
    agent.editorFocused('task',True)
    context=(owner._app_gestures.sentence(),owner._gesture_bindings)
    monkeypatch.setattr(owner,'_apply_voice_action',lambda *a,**k:pytest.fail('stale input gesture'))
    owner._inline_input._utterance_id='next-sentence'
    agent._input_gesture('swipe-left','task',owner._disconnect_event,time.monotonic(),context)
    context=(owner._app_gestures.sentence(),owner._gesture_bindings)
    agent.editorFocused('task',False)
    agent._input_gesture('swipe-left','task',owner._disconnect_event,time.monotonic(),context)


def test_agent_strokes_keep_native_palette_and_original_candidate_gestures(stroke):
    c,bridge,tp,source,session=stroke
    agent=c.agent;agent._visible=agent._focused=True
    tp.setLocalStrokeInput(True);tp.setLocalStrokeFocus(True)
    agent.editorFocused('task',True)
    assert not tp._local_stroke_input and not tp._local_stroke_focus
    wake(stroke,context='你好')
    assert not session.local and session.phase=='running'
    assert any(m['type']=='stroke_begin' for m in bridge.messages)
    assert any(m['type']=='stroke_update' for m in bridge.messages)
    assert not agent.filter_gesture(SimpleNamespace(name='swipe-right'),c._disconnect_event)
    c._ring_gestures.filter(SimpleNamespace(name='swipe-right'),False,c._disconnect_event)
    until(lambda:tp.selectedCandidate==1)
    assert not agent.filter_gesture(SimpleNamespace(name='tap'),c._disconnect_event)
    c._ring_gestures.filter(SimpleNamespace(name='tap'),False,c._disconnect_event)
    until(lambda:any(m['type']=='stroke_commit' for m in bridge.messages))


@pytest.mark.parametrize('feedback_field', ['agent', 'detail'])
def test_agent_feedback_grows_upwards_to_limit_before_scrolling(inline_ui, feedback_field, tmp_path):
    from PySide6.QtCore import QObject, QPointF
    from PySide6.QtTest import QTest
    from proximic_ring.ui.agent.agent_overlay import AgentOverlay
    owner=inline_ui[0];agent=owner.agent
    finish_fixture_voice(owner)
    agent._visible=agent._focused=True
    overlay=AgentOverlay(agent,owner)
    try:
        def show(text):
            agent._apply_state({'turnId':'turn',feedback_field:text})
            QTest.qWait(400)
        show('正在整理论文。')
        window=overlay.windows['task'];root=window.rootObject()
        short=window.geometry()
        viewport=root.findChild(QObject,'agentMessageViewport')
        message=root.findChild(QObject,'agentMessage')
        agent._apply_state({'turnId':'turn',feedback_field:'\n'.join(['论文的研究问题与主要发现。'] * 6)})
        QTest.qWait(80)
        assert short.height() < window.height() < root.implicitHeight()
        assert window.geometry().bottom()==short.bottom()
        QTest.qWait(350)
        medium=window.geometry()
        assert medium.height() > short.height()
        assert medium.top() < short.top() and medium.bottom()==short.bottom()
        assert viewport.property('availableHeight') >= message.property('implicitHeight')-1
        show('\n'.join(['这是较长的回答或执行进度。'] * 60))
        maximum=window.geometry()
        assert maximum.height()==root.property('maximumExpandedHeight')
        assert maximum.bottom()==short.bottom()
        assert viewport.property('contentHeight') > viewport.property('availableHeight')
        button=visual_child(root,'agentSendButton')
        button_position=button.mapToScene(QPointF(0,0))
        assert 0 <= button_position.y() and button_position.y()+button.height() <= window.height()
        assert window.grabWindow().save(str(tmp_path/f'agent-expanded-{feedback_field}.png'))
        show('\n'.join(['继续追加的回答。'] * 120))
        assert window.geometry()==maximum
        show('已整理好。')
        assert window.geometry()==short
    finally:overlay.close()


def test_agent_focus_motion_keeps_text_layout_stable_and_can_reverse(inline_ui, tmp_path):
    from PySide6.QtCore import QObject
    from PySide6.QtTest import QTest
    from proximic_ring.ui.agent.agent_overlay import AgentOverlay
    owner=inline_ui[0];agent=owner.agent
    finish_fixture_voice(owner)
    agent._visible=agent._focused=True
    agent._apply_state({'taskOutcome':'completed','agent':'这段较长的任务结果在焦点切换过程中不应重复换行。'*8})
    overlay=AgentOverlay(agent,owner)
    try:
        overlay.focus('task');QTest.qWait(400)
        window=overlay.windows['task'];root=window.rootObject()
        body=root.findChild(QObject,'agentCardBody')
        # Cocoa's first rich-text layout can retarget the entrance animation.
        # Begin the focus check only once that independent entrance has settled.
        for _ in range(100):
            QTest.qWait(20)
            animation=overlay.animations.get(('task','geometry'))
            if animation and window.geometry()==animation.endValue() and not overlay._layout_timer.isActive():
                break
        assert window.geometry()==animation.endValue()
        expanded=window.geometry();body_width=body.width()
        for focused in (False, True):
            agent.workspaceFocused(focused)
            samples=[]
            for _ in range(22):
                QTest.qWait(16)
                samples.append((window.width(),root.implicitHeight(),body.width(),window.geometry().bottom()))
            widths=[row[0] for row in samples]
            import json
            (tmp_path/('expand-frames.json' if focused else 'compact-frames.json')).write_text(json.dumps({
                'samples':samples,'initial':expanded.getRect(),
                'target':overlay.animations['task','geometry'].endValue().getRect(),
                'screen':window.screen().availableGeometry().getRect()},indent=2))
            assert widths==sorted(widths,reverse=not focused)
            assert len({row[1] for row in samples})==1  # Target height must not chase resize-induced wrapping.
            assert all(row[2]==body_width and row[3]==expanded.bottom() for row in samples), (samples,expanded,body_width)
            assert window.width()==(440 if focused else 344)
            assert body.property('opacity')==(1 if focused else 0)
            assert window.grabWindow().save(str(tmp_path/('focused.png' if focused else 'compact.png')))
        agent.workspaceFocused(False);QTest.qWait(65)
        before=window.geometry()
        assert 344<before.width()<440
        agent.workspaceFocused(True)
        assert window.geometry()==before  # Reverse from the current frame, without a reset.
        QTest.qWait(350)
        assert window.geometry()==expanded and body.property('opacity')==1
    finally:
        overlay.close();agent.close()


def test_agent_new_questions_and_permission_windows_have_independent_sizes(inline_ui):
    from PySide6.QtCore import QObject
    from PySide6.QtTest import QTest
    from proximic_ring.ui.agent.agent_overlay import AgentOverlay
    owner=inline_ui[0];agent=owner.agent
    finish_fixture_voice(owner)
    agent._visible=agent._focused=True
    overlay=AgentOverlay(agent,owner)
    try:
        long_question=request('long-question','question')
        long_question['message']='需要确认的具体问题。\n'*80
        agent._apply_state({'turnId':'turn','requests':[long_question]})
        QTest.qWait(400)
        window=overlay.windows['task'];root=window.rootObject()
        viewport=root.findChild(QObject,'agentMessageViewport')
        maximum=window.height()
        assert maximum==root.property('maximumExpandedHeight')
        viewport.property('contentItem').setProperty('contentY',70)
        assert viewport.property('contentItem').property('contentY') > 0

        short_question=request('new-question','question')
        agent._apply_state({'turnId':'turn','requests':[short_question]})
        QTest.qWait(400)
        assert overlay.windows['task'] is window  # A reused view still resets its own content.
        assert window.height() < maximum
        assert viewport.property('contentItem').property('contentY')==0
        short_height=window.height()

        long_permission=request('long-permission')
        long_permission['message']='需要允许的详细操作。\n'*60
        agent._apply_state({'turnId':'turn','requests':[short_question,long_permission]})
        QTest.qWait(400)
        assert window.height()==short_height
        permission_height=overlay.windows['long-permission'].height()
        agent._apply_state({'turnId':'turn','requests':[short_question,request('new-permission')]})
        QTest.qWait(400)
        assert window.height()==short_height
        assert overlay.windows['new-permission'].height() < permission_height
        assert 'long-permission' not in overlay.windows
    finally:overlay.close()


def test_agent_all_animated_frames_are_bounded_and_long_choices_remain_scrollable(inline_ui, monkeypatch):
    from PySide6.QtCore import QObject
    from PySide6.QtTest import QTest
    from proximic_ring.ui.agent.agent_overlay import AgentOverlay
    owner=inline_ui[0];agent=owner.agent
    finish_fixture_voice(owner)
    agent._visible=agent._focused=True
    overlay=AgentOverlay(agent,owner)
    samples=[]
    apply_frame=overlay._apply_frame
    def sample(window,rect):
        apply_frame(window,rect)
        samples.append((window.geometry(),window.screen().availableGeometry()))
    monkeypatch.setattr(overlay,'_apply_frame',sample)
    try:
        agent._apply_state({})
        overlay.refresh();QTest.qWait(350)
        initial=overlay.windows['task'].height()
        agent._apply_state({'turnId':'turn','agent':'正在执行'})
        QTest.qWait(80)
        target=int(overlay.windows['task'].rootObject().implicitHeight())
        assert initial < overlay.windows['task'].height() < target
        QTest.qWait(350)
        question=request('long','question')
        question['message']='这里是一个很长的问题。'*100
        question['options']=[dict(index=i,label='较长的真实服务端选项 '+str(i),enabled=True) for i in range(15)]
        agent._apply_state({'turnId':'turn','requests':[question]})
        QTest.qWait(400)
        body=overlay.windows['task'].rootObject().findChild(QObject,'agentQuestionOptions')
        assert body.property('contentHeight') > body.property('availableHeight')
        message=overlay.windows['task'].rootObject().findChild(QObject,'agentMessage')
        assert message.property('implicitHeight') > 108
        assert message.width() <= overlay.windows['task'].width()-32
        assert list(overlay.windows)==['task']
        body.property('contentItem').setProperty('contentY',50)
        assert body.property('contentItem').property('contentY') > 0
        agent.workspaceFocused(False);QTest.qWait(350)
        agent.workspaceFocused(True);QTest.qWait(350)
        agent._apply_state({'agent':'已完成'});QTest.qWait(350)
        assert len(samples)>20
        assert all(area.adjusted(8,8,-8,-8).contains(rect) for rect,area in samples)
    finally:overlay.close()


@pytest.mark.parametrize('gesture,operation',[('swipe-up','send'),('swipe-down','polish')])
def test_agent_voice_finishes_before_send_or_native_polish(inline_ui,monkeypatch,gesture,operation):
    from PySide6.QtCore import QCoreApplication
    owner,bridge,*_=inline_ui;agent=owner.agent;sent=[]
    agent._auto_context_mode='off'
    owner._disconnect_event=threading.Event();agent._focused=True
    agent.editorFocused('task',True)
    monkeypatch.setattr(agent,'_send',sent.append)
    bridge.messages.clear()
    assert agent.filter_gesture(SimpleNamespace(name=gesture),owner._disconnect_event)
    QCoreApplication.processEvents()
    assert sent==[] and any(m['type']=='finish' for m in bridge.messages)
    assert owner._finish_utterance_event.is_set()
    agent.edit('task','完整的后半句话')
    finish_fixture_voice(owner)
    owner._inline_input._view.update(raw='完整的后半句话',has_composition=False)
    owner._inline_input.settled.emit('dictated','完整的后半句话')
    QCoreApplication.processEvents()
    if operation=='send':
        assert sent==[{'type':'text','text':'完整的后半句话','targetTurn':''}]
    else:
        assert sent==[] and any(m['type']=='polish' and not m['restore_raw_on_undo'] for m in bridge.messages)


@pytest.mark.parametrize('change',['cancel','focus','question','connection','sentence'])
def test_agent_pending_send_does_not_survive_input_changes(inline_ui,monkeypatch,change):
    from PySide6.QtCore import QCoreApplication
    owner=inline_ui[0];agent=owner.agent;sent=[]
    owner._disconnect_event=threading.Event();agent._focused=True
    finish_fixture_voice(owner)
    agent._apply_state({'requests':[request('q','question')]})
    agent.editorFocused('q',True)
    owner._utterance_active=True;owner._inline_input._view['phase']='listening'
    monkeypatch.setattr(agent,'_send',sent.append)
    agent.filter_gesture(SimpleNamespace(name='swipe-up'),owner._disconnect_event)
    QCoreApplication.processEvents()
    assert agent._pending_input_action is not None
    agent.edit('q','旧答案')
    if change=='cancel':owner._inline_input.actionRequested.emit('cancel')
    elif change=='focus':agent.editorFocused('q',False)
    elif change=='question':agent._apply_state({'requests':[request('next','question')]})
    elif change=='connection':owner._disconnect_event=threading.Event()
    else:owner._inline_input._utterance_id='other'
    finish_fixture_voice(owner);owner._inline_input._view['has_composition']=False
    owner._inline_input.settled.emit('dictated','旧答案');QCoreApplication.processEvents()
    assert sent==[] and agent.workspace.drafts['q']=='旧答案'


@pytest.mark.parametrize('voice_busy',[False,True])
def test_qml_composition_keeps_original_target_when_question_arrives(inline_ui,monkeypatch,voice_busy):
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtGui import QInputMethodEvent
    from PySide6.QtTest import QTest
    from proximic_ring.ui.agent.agent_overlay import AgentOverlay
    owner=inline_ui[0];agent=owner.agent;sent=[]
    agent._auto_context_mode='off'
    finish_fixture_voice(owner);owner._inline_input._view['has_composition']=False
    agent._visible=agent._focused=True
    monkeypatch.setattr(agent,'_send',sent.append)
    overlay=AgentOverlay(agent,owner)
    try:
        overlay.focus('task');QTest.qWait(350)
        window=overlay.windows['task'];root=window.rootObject()
        editor=visual_child(root,'agentEditor')
        if voice_busy:
            owner._utterance_active=True
            owner._inline_input._view.update(phase='listening',has_composition=True)
        QCoreApplication.sendEvent(editor,QInputMethodEvent('完整的输入',[]))
        assert editor.property('preeditText')=='完整的输入'
        agent._apply_state({'requests':[request('new','question')]})
        assert agent.inputKey=='task'
        commit=QInputMethodEvent();commit.setCommitString('完整的输入')
        QCoreApplication.sendEvent(editor,commit);QTest.qWait(30)
        assert agent.workspace.drafts['task']=='完整的输入'
        if voice_busy:
            assert agent.inputKey=='task' and editor.property('text')=='完整的输入'
            finish_fixture_voice(owner)
            inline_ui[-1](phase='dictated',has_composition=False,awaiting_readback=True)
            QTest.qWait(20)
            assert agent.inputKey=='task'  # Do not switch before native readback.
            inline_ui[-1](phase='dictated',has_composition=False,awaiting_readback=False)
            QTest.qWait(20)
        assert agent.inputKey=='new' and list(overlay.windows)==['task']
        assert editor.property('text')==''
        agent.submit('task');QTest.qWait(30)
        assert sent==[]  # A delayed submission cannot send the parked draft.
        editor.setProperty('text','选第一个')
        agent.submit('new')
        assert sent[-1]=={'type':'choose','token':'new','index':0}
        assert editor.property('text')==''
        agent._apply_state({'requests':[]});QTest.qWait(30)
        assert agent.inputKey=='task' and editor.property('text')=='完整的输入'
        agent.submit('task')
        assert sent[-1]=={'type':'text','text':'完整的输入','targetTurn':''}
    finally:overlay.close()


@pytest.mark.parametrize('path',[
    '/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex',
    '/Applications/Codex.app/Contents/Resources/codex',
    '/usr/local/bin/codex'])
def test_agent_locates_nested_legacy_and_path_cli(monkeypatch,path):
    from proximic_ring.ui.agent import agent_controller as module
    monkeypatch.setattr(module.os,'access',lambda candidate,_:candidate==path)
    monkeypatch.setattr(module.shutil,'which',lambda _: '/usr/local/bin/codex')
    assert module.find_codex()==path
