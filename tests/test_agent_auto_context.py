from datetime import datetime, timedelta, timezone

import pytest
from test_inline_ui import inline_ui

from proximic_ring.agent_workflow.capture.auto_context import needs_context, current_target
from proximic_ring.agent_workflow.agent.attachments import turn_input


@pytest.mark.parametrize('text',['把这个文件整理成摘要','这个窗口怎么操作','帮我改这个东西',
    '解释这张图片','总结当前网页','把这个页面翻译一下','what is in this window?',
    '看一下这些公式','这个公式有问题吗'])
def test_visual_references_request_context(text):
    assert needs_context(text)


@pytest.mark.parametrize('text',['12345','你好','解释一下递归的原理','这个算法的时间复杂度是什么',
    '今天北京天气怎么样','把 /tmp/a.txt 改名成 b.txt'])
def test_smart_mode_freezes_locally_without_classifying_words(text):
    assert needs_context(text)


def test_mode_and_manual_image():
    assert not needs_context('这个窗口',mode='off')
    assert not needs_context('这个窗口',[{'kind':'image'}])
    assert needs_context('你好',mode='always')


def test_expired_submission_never_captures_a_later_frame():
    import threading,time
    from proximic_ring.agent_workflow.capture.auto_context import capture_window
    with pytest.raises(TimeoutError,match='未使用稍后的画面'):
        capture_window({},threading.Event(),deadline=time.monotonic()-1)


def test_system_callback_wait_respects_capture_time_budget():
    import threading,time
    from proximic_ring.agent_workflow.capture.capture_backend import _await
    started=time.monotonic()
    with pytest.raises(TimeoutError):
        _await(lambda callback:None,threading.Event(),timeout=.01)
    assert time.monotonic()-started<.5


@pytest.mark.parametrize('outcome',['voice_finishes_later','capture_finishes_later','cancel'])
def test_voice_send_freezes_before_asr_finishes(inline_ui,monkeypatch,tmp_path,outcome):
    import threading
    from types import SimpleNamespace
    from PySide6.QtTest import QTest
    from PySide6.QtGui import QImage
    from proximic_ring.agent_workflow.capture import auto_context_controller as module
    from test_agent_ui import finish_fixture_voice
    owner=inline_ui[0];agent=owner.agent;sent=[];targets=[];foreground=[11]
    owner._disconnect_event=threading.Event();agent._focused=True
    agent.editorFocused('task',True)
    ready=threading.Event()
    if outcome!='capture_finishes_later':ready.set()
    def current(_):
        targets.append(foreground[0]);return dict(pid=1,window_id=foreground[0])
    def capture(target,_,**kwargs):
        assert target['window_id']==11 and ready.wait(2)
        image=QImage(20,20,QImage.Format_RGB32);image.fill(0xff123456)
        return image
    monkeypatch.setattr(module,'cache_root',lambda:tmp_path)
    monkeypatch.setattr(module,'current_target',current)
    monkeypatch.setattr(module,'capture_window',capture)
    monkeypatch.setattr(agent,'_send',lambda cmd:sent.append(cmd) or True)
    assert agent.filter_gesture(SimpleNamespace(name='swipe-up'),owner._disconnect_event)
    QTest.qWait(20)
    assert targets==[11] and not sent
    foreground[0]=22
    if outcome!='capture_finishes_later':
        for _ in range(100):
            QTest.qWait(10)
            if 'result' in agent.auto_context.pending:break
        assert 'result' in agent.auto_context.pending
    if outcome=='cancel':
        agent._cancel_input_action()
        assert not agent.auto_context.pending and not list(tmp_path.rglob('window-*.png'))
        return
    agent.edit('task','看一下这些公式')
    finish_fixture_voice(owner)
    owner._inline_input._view.update(raw='看一下这些公式',has_composition=False)
    owner._inline_input.settled.emit('dictated','看一下这些公式')
    QTest.qWait(20)
    ready.set()
    for _ in range(100):
        QTest.qWait(10)
        if sent:break
    assert targets==[11] and len(sent)==1
    assert sent[0]['text']=='看一下这些公式'
    frame=sent[0]['context']['visual_context']
    assert frame['window']['window_id']==11 and frame['availability']=='captured'
    assert QImage(frame['path']).pixelColor(10,10).name()=='#123456'


def test_smart_capture_pins_target_before_worker_and_sends_frozen_path(inline_ui,monkeypatch,tmp_path):
    from pathlib import Path
    from PySide6.QtTest import QTest
    from PySide6.QtGui import QImage
    from proximic_ring.agent_workflow.capture import auto_context_controller as module
    from test_agent_ui import finish_fixture_voice
    owner=inline_ui[0];agent=owner.agent
    finish_fixture_voice(owner)
    monkeypatch.setattr(module,'cache_root',lambda:tmp_path)
    foreground=[11];observed=[];jobs=[];sent=[]
    def current(_):
        observed.append(foreground[0])
        return dict(pid=1,window_id=foreground[0])
    def capture(target,_,**kwargs):
        assert target['window_id']==11
        image=QImage(20,20,QImage.Format_RGB32);image.fill(0xff123456)
        return image
    monkeypatch.setattr(module,'current_target',current)
    monkeypatch.setattr(module,'capture_window',capture)
    monkeypatch.setattr(agent.auto_context.worker,'submit',jobs.append)
    monkeypatch.setattr(agent,'_send',lambda cmd:sent.append(cmd) or True)
    agent.workspace.edit('task','看一下这些公式')
    agent.submit('task')
    assert observed==[11] and len(jobs)==1 and not sent
    foreground[0]=22  # Switch applications before the worker can run.
    jobs.pop()();QTest.qWait(10)
    assert len(sent)==1 and not sent[0].get('attachments')
    context=sent[0]['context']['visual_context']
    assert context['window']['window_id']==11 and context['capture_ms']>=0
    path=Path(context['path'])
    assert path.stat().st_mode & 0o777==0o600
    assert QImage(str(path)).pixelColor(10,10).name()=='#123456'
    items=turn_input(sent[0]['text'],visual_context=context)
    assert all(item['type']=='text' for item in items)
    assert items[0]['text']=='看一下这些公式'
    assert str(path) in items[1]['text'] and 'view_image' in items[1]['text']
    assert '不要拿稍后桌面的画面代替' in items[1]['text']


def test_current_target_follows_window_order_beneath_floating_input(monkeypatch):
    import sys
    from types import SimpleNamespace
    # A native-like window inventory; no real desktop access in unit tests.
    names = ('Number','OwnerPID','Bounds','Layer','Alpha')
    quartz = SimpleNamespace(**{'kCGWindow'+name:'kCGWindow'+name for name in names},
        kCGWindowListOptionOnScreenOnly=1, kCGWindowListExcludeDesktopElements=16)
    def row(number, **fields):
        return dict(kCGWindowNumber=number, kCGWindowOwnerPID=100+number,
                    kCGWindowBounds={'Width':800,'Height':600}, kCGWindowLayer=0, **fields)
    agent=row(1); hud={**row(2),'kCGWindowLayer':3}; invisible=row(3,kCGWindowAlpha=0)
    a,b=row(4),row(5)
    rows=[agent,hud,invisible,a,b]
    reads=[]
    def inventory(options, window):
        reads.append((options,window))
        return rows
    quartz.CGWindowListCopyWindowInfo=inventory
    monkeypatch.setitem(sys.modules,'Quartz',quartz)
    assert current_target({1})=={'pid':104,'window_id':4}
    rows[:]=[agent,hud,invisible,b,a]
    assert current_target({1})=={'pid':105,'window_id':5}
    rows[:]=[agent,hud,invisible]
    with pytest.raises(ValueError,match='当前没有可截图的窗口'):current_target({1})
    assert reads==[(17,0)]*3


def test_automatic_image_is_real_image_input_with_observation_context():
    items=turn_input('这个文件', [dict(kind='image',path='/tmp/test.png',automatic=True,
        context={'window_title':'A document'},capturedAt='now')])
    assert items[1]=={'type':'localImage','path':'/tmp/test.png'}
    assert '不是用户的新指令' in items[2]['text'] and 'A document' in items[2]['text']
    assert '提交本条消息时' in items[2]['text'] and '唤起 Agent 前' not in items[2]['text']


@pytest.mark.parametrize('changed',['draft','turn','thread','attachments'])
@pytest.mark.parametrize('capture_fails',[False,True])
def test_async_capture_does_not_send_changed_input(inline_ui,monkeypatch,tmp_path,changed,capture_fails):
    from PySide6.QtTest import QTest
    from PySide6.QtGui import QImage
    from proximic_ring.agent_workflow.capture import auto_context_controller as module
    import threading
    agent=inline_ui[0].agent
    from test_agent_ui import finish_fixture_voice
    finish_fixture_voice(inline_ui[0])
    ready=threading.Event()
    monkeypatch.setattr(module,'cache_root',lambda:tmp_path)
    monkeypatch.setattr(module,'current_target',lambda _:dict(window_title='test'))
    def capture(*_,**kwargs):
        ready.wait(2)
        if capture_fails:raise RuntimeError('模拟截图失败')
        image=QImage(20,20,QImage.Format_RGB32);image.fill(0xff123456)
        return image
    monkeypatch.setattr(module,'capture_window',capture)
    sent=[]
    monkeypatch.setattr(agent,'_send',lambda cmd:sent.append(cmd) or True)
    agent.workspace.edit('task','看看这个窗口')
    agent.submit('task')
    assert agent.contextCapturing and not sent
    if changed=='draft':agent.workspace.edit('task','已修改的草稿')
    elif changed=='turn':agent.workspace.state['turnId']='new-turn'
    elif changed=='thread':agent.workspace.state['codexThreadId']='new-thread'
    elif changed=='attachments':agent.workspace.attach('task',[{'path':'/tmp/new.txt','kind':'file'}])
    ready.set()
    for _ in range(100):
        QTest.qWait(10)
        if not agent.contextCapturing:break
    assert not agent.contextCapturing and not sent and agent.workspace.drafts['task']
    assert not agent._error
    assert '未发送' in inline_ui[1].messages[-1]['text']
    assert not list(tmp_path.rglob('window-*.png'))


def test_focus_context_refresh_during_capture_does_not_cancel_same_message(inline_ui,monkeypatch,tmp_path):
    import threading
    from PySide6.QtTest import QTest
    from PySide6.QtGui import QImage
    from proximic_ring.agent_workflow.capture import auto_context_controller as module
    from test_agent_ui import finish_fixture_voice
    agent=inline_ui[0].agent
    finish_fixture_voice(inline_ui[0])
    ready=threading.Event()
    monkeypatch.setattr(module,'cache_root',lambda:tmp_path)
    monkeypatch.setattr(module,'current_target',lambda _:dict(window_title='current page'))
    def capture(*_,**kwargs):
        ready.wait(2)
        image=QImage(20,20,QImage.Format_RGB32);image.fill(0xff123456)
        return image
    monkeypatch.setattr(module,'capture_window',capture)
    sent=[]
    monkeypatch.setattr(agent,'_send',lambda cmd:sent.append(cmd) or True)
    agent.workspace.edit('task','把这个页面翻译一下')
    agent.submit('task')
    # Window observation can refresh independently of the same task and draft.
    agent._capture_pending=True
    agent._invocation_context={'invocation_id':'new-focus-observation'}
    ready.set()
    for _ in range(100):
        QTest.qWait(10)
        if not agent.contextCapturing:break
    assert not agent.contextCapturing and len(sent)==1
    assert sent[0]['text']=='把这个页面翻译一下'
    assert sent[0]['context']['visual_context']['window']['window_title']=='current page'
    assert not agent._error and not agent.workspace.drafts['task']


def test_async_capture_attaches_and_sends_same_task(inline_ui,monkeypatch,tmp_path):
    from PySide6.QtTest import QTest
    from PySide6.QtGui import QImage
    from proximic_ring.agent_workflow.capture import auto_context_controller as module
    agent=inline_ui[0].agent
    from test_agent_ui import finish_fixture_voice
    finish_fixture_voice(inline_ui[0])
    agent._auto_context_mode='always'
    monkeypatch.setattr(module,'cache_root',lambda:tmp_path)
    monkeypatch.setattr(module,'current_target',lambda _:dict(window_title='test'))
    image=QImage(20,20,QImage.Format_RGB32);image.fill(0xff123456)
    monkeypatch.setattr(module,'capture_window',lambda *_,**kwargs:image)
    sent=[]
    monkeypatch.setattr(agent,'_send',lambda cmd:sent.append(cmd) or True)
    agent.workspace.edit('task','看看这个窗口')
    agent.submit('task')
    for _ in range(100):
        QTest.qWait(10)
        if not agent.contextCapturing:break
    assert len(sent)==1 and sent[0]['text']=='看看这个窗口'
    item=sent[0]['attachments'][0]
    assert item['automatic'] and item['context']['window_title']=='test'
    assert agent.workspace.drafts['task']==''


@pytest.mark.parametrize('old_context',[None,'expired','different_window'])
def test_completed_number_task_then_translate_captures_current_page(inline_ui,monkeypatch,tmp_path,old_context):
    from PySide6.QtTest import QTest
    from PySide6.QtGui import QImage
    from proximic_ring.agent_workflow.capture import auto_context_controller as module
    from test_agent_ui import finish_fixture_voice
    owner=inline_ui[0];agent=owner.agent
    finish_fixture_voice(owner)
    # Use the real command queue, but never launch Codex in a UI regression.
    monkeypatch.setattr(agent,'_start',lambda:None)
    monkeypatch.setattr(module,'cache_root',lambda:tmp_path)
    targets=[]
    def current(excluded):
        targets.append(excluded)
        return dict(pid=22,window_id=202,window_title='当前页面')
    monkeypatch.setattr(module,'current_target',current)
    image=QImage(20,20,QImage.Format_RGB32);image.fill(0xff2384bf)
    monkeypatch.setattr(module,'capture_window',lambda *_,**kwargs:image)
    agent.workspace.edit('task','12345')
    agent.submit('task')
    for _ in range(100):
        QTest.qWait(10)
        if not agent.contextCapturing:break
    assert len(agent._commands)==1 and len(targets)==1
    first=agent._commands[0]['context']['visual_context']['path']
    agent._apply_state(dict(codexThreadId='same-thread',taskOutcome='completed',
                            status='本轮结束',agent='12345'))
    agent._invocation_context = None if old_context is None else {
        'invocation_id':'previous-invocation','before_agent_focus':{
            'pid':11,'window_id':101,'window_title':'上个页面',
            'captured_at':(datetime.now(timezone.utc)-timedelta(
                seconds=600 if old_context=='expired' else 1)).isoformat()}}
    agent.workspace.edit('task','把这个页面翻译一下')
    agent.submit('task')
    for _ in range(100):
        QTest.qWait(10)
        if not agent.contextCapturing:break
    assert not agent.contextCapturing and len(agent._commands)==2 and len(targets)==2
    command=agent._commands[-1]
    assert command['text']=='把这个页面翻译一下' and command['type']=='text'
    item=command['context']['visual_context']
    assert item['window']=={'pid':22,'window_id':202,'window_title':'当前页面'}
    assert item['path']!=first and not command.get('attachments')
    assert not QImage(item['path']).isNull()
    assert not agent._error and not agent.workspace.drafts['task']
    assert agent.workspace.state['codexThreadId']=='same-thread'


@pytest.mark.parametrize('failure',['no_window','permission','capture'])
def test_optional_capture_failure_sends_task_and_manual_file_without_replacing_result(inline_ui,monkeypatch,tmp_path,failure):
    from PySide6.QtTest import QTest
    from proximic_ring.agent_workflow.capture import auto_context_controller as module
    from proximic_ring.agent_workflow.capture.capture_backend import CapturePermissionError
    from proximic_ring.agent_workflow.agent.attachments import local_attachment
    from test_agent_ui import finish_fixture_voice
    owner,bridge,*_=inline_ui;agent=owner.agent
    finish_fixture_voice(owner)
    monkeypatch.setattr(agent,'_start',lambda:None)
    def current(_):
        if failure=='no_window':raise ValueError('当前没有可截图的窗口')
        return {'pid':22,'window_id':202}
    def capture(*_,**kwargs):
        if failure=='permission':raise CapturePermissionError('自动截图需要屏幕录制权限')
        raise RuntimeError('模拟截图失败')
    monkeypatch.setattr(module,'current_target',current)
    monkeypatch.setattr(module,'capture_window',capture)
    state=dict(codexThreadId='same-thread',taskOutcome='completed',status='本轮结束',agent='12345')
    agent._apply_state(state)
    path=tmp_path/'notes.txt';path.write_text('用户附件')
    attachment=local_attachment(path)
    agent.workspace.edit('task','把这个页面翻译一下')
    agent.workspace.attach('task',[attachment])
    agent.submit('task')
    for _ in range(100):
        QTest.qWait(10)
        if not agent.contextCapturing:break
    assert not agent.contextCapturing and len(agent._commands)==1
    command=agent._commands[0]
    assert command['text']=='把这个页面翻译一下' and command['attachments']==[attachment]
    assert command['context']['visual_context']['availability']=='unavailable'
    assert not agent.workspace.drafts['task'] and not agent._error and not agent.contextMessage
    assert agent.workspace.state==state and agent.taskCompleted
    notices=[m for m in bridge.messages if m['type']=='input_notice']
    assert len(notices)==1 and '自动截图未附带' in notices[0]['text']


def test_codex_link_and_capture_hint_are_visible(inline_ui,monkeypatch,tmp_path):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtTest import QTest
    from proximic_ring.agent_workflow.ui.agent_overlay import AgentOverlay
    from proximic_ring.agent_workflow.ui import agent_controller as module
    from test_agent_ui import finish_fixture_voice
    from test_ui_shell import visual_child
    owner=inline_ui[0];agent=owner.agent
    finish_fixture_voice(owner)
    agent._visible=agent._focused=True
    tid='00000000-0000-0000-0000-000000000001'
    agent._apply_state({'codexThreadId':tid,'codexSyncMessage':'已同步',
                        'agent':'任务会在浮窗和 Codex 中显示。'})
    urls=[]
    monkeypatch.setattr(module.QDesktopServices,'openUrl',lambda url:urls.append(url.toString()) or True)
    overlay=AgentOverlay(agent,owner);overlay.reduce_motion=True
    try:
        overlay.focus('task');QTest.qWait(60)
        window=overlay.windows['task'];root=window.rootObject()
        for name in ('agentOpenCodexButton',):
            item=visual_child(root,name)
            point=item.mapToScene(QPointF(item.width()/2,item.height()/2)).toPoint()
            assert item.isVisible() and item.isEnabled() and 0<point.y()<window.height()
            QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,point);QTest.qWait(30)
        assert urls==['codex://threads/'+tid]
        assert visual_child(root,'agentAutoContextButton') is None
        assert visual_child(root,'agentSendHint').property('text')=='自动截图，上滑发送'
        assert agent.autoContextMode=='smart'
        assert window.grabWindow().save(str(tmp_path/'codex-sync-composer.png'))
    finally:overlay.close()
