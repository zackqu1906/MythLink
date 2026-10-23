"""Hub ownership, stable directions and real native editor handoff, no desktop tasks."""
import copy
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
import pytest

# Preserve legacy behavior tests behind an explicit test-only opt-in.
pytestmark = pytest.mark.usefixtures('legacy_workflow_pipeline')
from PySide6.QtCore import QCoreApplication, QObject, QMetaObject, Q_ARG, Qt
from PySide6.QtGui import QInputMethodEvent, QTextCursor
from PySide6.QtTest import QTest
from test_inline_ui import inline_ui
from test_recommendation_execution import catalog
from test_recommendation_ui import configure,wait
from test_agent_ui import finish_fixture_voice
from proximic_ring.agent_workflow.ui.recommendation_input import DIRECTIONS


@pytest.fixture
def hub(inline_ui,catalog,monkeypatch):
    from proximic_ring.agent_workflow.operations.operation_prototypes import seed
    owner=inline_ui[0];agent,runtime,captures=configure(owner,catalog,monkeypatch)
    first=catalog.rows('samples')[0]
    for i,name in enumerate(['播放音乐','打开课程页面','搜索附近餐厅']):
        flow=copy.deepcopy(first['flow']) if 'flow' in first else copy.deepcopy(catalog.versions()[first['version_id']])
        flow.update(name=name,goal=name,parameters=[],steps=[dict(action=name,method=name,check='目标可见')])
        catalog.ingest(dict(id='hub'+str(i),kind='user',flow=flow,source=dict(session_id='s',episode_id='hub'+str(i)),context={},occurred_at=first['occurred_at']))
        with catalog.db:seed(catalog,next(s for s in catalog.rows('samples') if s['id']=='hub'+str(i)))
    sent=[];monkeypatch.setattr(agent,'_send',lambda command:sent.append(command) or True)
    owner._disconnect_event=threading.Event()
    runtime.open('gesture');wait(lambda:runtime.snapshot)
    wait(lambda:all(c['exposure'] for c in runtime.dialog.cards))
    yield owner,agent,runtime,sent,inline_ui
    runtime.close()


def swipe(owner,name):
    result=owner._ring_gestures.filter(SimpleNamespace(name=name),False,owner._disconnect_event)
    QCoreApplication.processEvents();return result




def test_four_directions_are_frozen_and_selection_never_sends(hub):
    from PySide6.QtGui import QGuiApplication, QCursor
    owner,agent,runtime,sent,_=hub
    assert not agent.visible and not agent.focused
    assert runtime.dialog.parent() is None and len(runtime.dialog.cards)==4
    assert runtime._focus_channel is not agent._focus_channel
    assert runtime.dialog.windowFlags() & Qt.WindowStaysOnTopHint
    assert runtime.dialog.guard._input_workspace and runtime.dialog.guard._level_offset==1
    screen=QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
    assert runtime.dialog.geometry().center()==screen.geometry().center()
    snapshot=runtime.snapshot
    for gesture,position in DIRECTIONS.items():
        assert swipe(owner,gesture) is False
        assert runtime.dialog.position==position and runtime.input.stage=='parameters' and runtime.dialog.parameters.isVisible()
        assert not sent and runtime.snapshot is snapshot
        runtime.dialog.back();QCoreApplication.processEvents()
    # Two queued ups belong to the hub generation; neither may send the form.
    event=SimpleNamespace(name='swipe-up')
    owner._ring_gestures.filter(event,False,owner._disconnect_event)
    owner._ring_gestures.filter(event,False,owner._disconnect_event)
    QCoreApplication.processEvents();assert not sent


def test_main_window_activation_does_not_make_hub_a_gesture_owner(hub,monkeypatch):
    from PySide6.QtGui import QGuiApplication
    owner,_,runtime,sent,fixture=hub
    # Cocoa reports both Tool and main QWidget as active. Only the actual
    # QWindow receiving keyboard focus determines input/exposure ownership.
    monkeypatch.setattr(runtime.dialog,'isActiveWindow',lambda:True)
    monkeypatch.setattr(QGuiApplication,'focusWindow',lambda:fixture[3])
    runtime.dialog._settle_focus()
    assert not runtime.input.focused
    assert not runtime.filter_gesture(SimpleNamespace(name='swipe-up'),owner._disconnect_event)
    assert not sent


def test_click_away_fades_half_second_and_invalidates_input_immediately(hub):
    from PySide6.QtWidgets import QWidget
    owner,_,runtime,sent,_=hub;dialog=runtime.dialog
    dialog.choose(1);QCoreApplication.processEvents()
    generation=runtime.input.generation
    other=QWidget();other.show()
    try:
        QTest.mouseClick(other,Qt.LeftButton)
        assert dialog.closing and dialog.isVisible()
        assert runtime.snapshot is None and not runtime.input.focused and runtime.input.stage=='closed'
        assert dialog.fade.duration()==500
        runtime.input.navigate('swipe-up',owner._disconnect_event,time.monotonic(),generation)
        assert not sent
        dialog.fade.pause();dialog.fade.setCurrentTime(250)
        assert .4<dialog.windowOpacity()<.6
        dialog.fade.setCurrentTime(500);dialog.finish_dismissal()
        assert not dialog.isVisible()
    finally:other.close()


def test_reopen_during_fade_creates_new_invocation(hub):
    _,_,runtime,sent,_=hub;dialog=runtime.dialog;previous=runtime.snapshot['request_id']
    dialog.dismiss_animated();assert dialog.closing
    runtime.open('gesture');wait(lambda:runtime.snapshot and runtime.snapshot['request_id']!=previous)
    wait(lambda:dialog.has_panel_focus())
    assert dialog.isVisible() and not dialog.closing and dialog.windowOpacity()==1.
    assert runtime.input.stage=='hub' and not sent


def test_ime_helper_focus_handoff_does_not_dismiss_parameters(hub,monkeypatch):
    _,_,runtime,_,_=hub;dialog=runtime.dialog
    dialog.choose(next(i['position'] for i in runtime.snapshot['items'] if i['workflow']['parameters']))
    monkeypatch.setattr(dialog,'has_panel_focus',lambda:False)
    monkeypatch.setattr('proximic_ring.agent_workflow.ui.recommendation_dialog.input_helper_active',lambda _:True)
    dialog._settle_focus()
    assert dialog.isVisible() and not dialog.closing and runtime.input.stage=='parameters'


def test_parameter_context_menu_belongs_to_hub(hub):
    from PySide6.QtWidgets import QMenu
    from proximic_ring.agent_workflow.ui.recommendation_focus import owned_popup
    _,_,runtime,sent,_=hub;dialog=runtime.dialog
    dialog.choose(next(i['position'] for i in runtime.snapshot['items'] if i['workflow']['parameters']))
    dialog.enter_word(0);dialog.begin_word_input();QCoreApplication.processEvents()
    editor=dialog.editors['title'];menu=QMenu(editor)
    action=menu.addAction('测试菜单');called=[];action.triggered.connect(lambda:called.append(True))
    menu.popup(dialog.mapToGlobal(dialog.rect().topLeft()))
    QTest.qWait(120)
    assert owned_popup(dialog) is menu and not dialog.closing
    QTest.mouseClick(menu,Qt.LeftButton,pos=menu.actionGeometry(action).center())
    QCoreApplication.processEvents()
    assert called and not dialog.closing and not sent
    menu.close()


def test_recommendation_binding_remains_persistent_outside_compact_agent_settings(inline_ui):
    owner,_,_,root,_=inline_ui;finish_fixture_voice(owner)
    combo=root.findChild(QObject,'workflowRecommendationGestureCombo')
    assert combo is None
    assert owner.ringGestures.globalBindings['workflow_recommendations']=='double-index-pinch'
    assert owner.ringGestures.setGlobalBinding('workflow_recommendations','snap')
    QCoreApplication.processEvents()
    assert owner.ringGestures.globalBindings['workflow_recommendations']=='snap'
    from proximic_ring.gestures.gesture_settings import GlobalGestureBindings,GLOBAL_BINDINGS_KEY
    assert GlobalGestureBindings.from_json(owner._settings.value(GLOBAL_BINDINGS_KEY)).workflow_recommendations=='snap'


def editable(runtime,position=None):
    d=runtime.dialog
    position=position or next(i['position'] for i in runtime.snapshot['items'] if i['workflow']['parameters'])
    d.choose(position);QCoreApplication.processEvents()
    assert runtime.input.interaction.mode=='action' and not runtime.input.owns_input_focus()
    d.enter_word(0);QCoreApplication.processEvents()
    return d,d.editors[d.sentence.names[0]]


@pytest.mark.parametrize('rank',[1,2,3,4])
def test_action_direction_is_relative_and_does_not_steal_word_navigation(hub,monkeypatch,rank):
    owner,_,runtime,sent,_=hub;d=runtime.dialog;item=runtime.snapshot['items'][rank-1]
    item['workflow']['parameters']=[dict(name='title',description='标题'),dict(name='body',description='正文')]
    item['display']=dict(title='新建笔记',summary='创建一篇笔记',parameters={
        'title':dict(label='标题',placeholder='输入标题'),'body':dict(label='内容',placeholder='输入正文')},
        sentence=[dict(text='创建'),dict(parameter='title'),dict(text='，记下'),dict(parameter='body')])
    item['parameter_candidates']={'title':[dict(value='历史一'),dict(value='历史二')],'body':[]}
    calls=[];monkeypatch.setattr(runtime,'execute',lambda p,v:calls.append((p,v)))
    d.choose(rank);QCoreApplication.processEvents()
    assert d.editors['title'].toPlainText()=='历史一' and d.editors['body'].toPlainText()==''
    gestures=list(DIRECTIONS);outward=gestures[rank-1];inward=gestures[(rank+1)%4]
    for g in gestures:
        if g not in (outward,inward):swipe(owner,g);assert runtime.input.interaction.mode=='action' and not calls
    swipe(owner,'tap');assert runtime.input.interaction.mode=='word'
    assert d.editors['title'].selection_preview and not d.sentence.editing
    assert d.sentence.candidate_hint()=='候选 1/2 · 上下切换 · tap 语音'
    swipe(owner,'swipe-up');assert d.editors['title'].toPlainText()=='历史二'
    swipe(owner,'swipe-up');assert d.editors['title'].toPlainText()=='历史一'
    swipe(owner,'swipe-down');assert d.editors['title'].toPlainText()=='历史二'
    swipe(owner,'swipe-right');assert runtime.input.field=='body'
    swipe(owner,'swipe-up');assert d.editors['body'].toPlainText()=='' and not calls
    event=QInputMethodEvent();event.setCommitString('真实输入正文');QCoreApplication.sendEvent(d.editors['body'],event)
    swipe(owner,'swipe-right');assert runtime.input.interaction.mode=='action'
    assert runtime.input.interaction.slot==1
    swipe(owner,outward);assert calls==[(rank,{'title':'历史二','body':'真实输入正文'})]
    assert not sent


def test_actual_submit_keeps_definition_and_parameter_keys(hub,catalog):
    owner,_,runtime,sent,_=hub;d,editor=editable(runtime)
    editor.setPlainText('内容含 {{body}} 保持原文');d.confirm_word()
    d.handle_gesture('enter');wait(lambda:sent)
    assert len(sent)==1 and '内容含 {{body}} 保持原文' in sent[0]['prompt']
    assert not d.isVisible()
    import json
    assert json.loads(catalog.rows('recommendation_executions')[-1]['parameters'])=={'title':'内容含 {{body}} 保持原文'}


def test_missing_preedit_stale_and_stroke_ownership(hub,monkeypatch):
    owner,_,runtime,sent,_=hub;d=runtime.dialog
    d.choose(next(i['position'] for i in runtime.snapshot['items'] if i['workflow']['parameters']))
    d.handle_gesture('enter');assert not sent and runtime.input.interaction.mode=='word'
    editor=d.editors['title'];editor.setPlainText('已确认')
    runtime.input._composing=True;d.handle_gesture('enter');assert runtime.input.interaction.mode=='word'
    runtime.input._composing=False
    tp=owner._touchpad;tp.strokeSession.phase='running';tp._code='hsp'
    for g in ['tap',*DIRECTIONS]:assert not runtime.filter_gesture(SimpleNamespace(name=g),owner._disconnect_event)
    tp._code='';tp.strokeSession.cancel('测试结束')
    token=runtime.input.generation;runtime.input.set_focus(False)
    runtime.input.navigate('swipe-up',owner._disconnect_event,time.monotonic(),token)
    assert not sent


def test_tap_voice_replaces_all_commits_and_confirms_without_sending(hub,monkeypatch):
    from proximic_ring.voice.utterance_commands import UtteranceCommands
    owner,_,runtime,sent,fixture=hub;d,editor=editable(runtime);editor.setPlainText('旧词')
    owner._recognition_event.set();owner._utterance_commands=UtteranceCommands(owner._disconnect_event)
    def prepare(complete,connection):
        if complete is not None:complete(True)
    monkeypatch.setattr(owner,'_prepare_inline_gesture_start',prepare)
    calls=[]
    class Audio:
        def request_gesture_toggle(self,prepare,**kw):calls.append(kw);prepare(lambda ok:calls.append(ok));return 'start'
    swipe(owner,'tap');assert runtime.input.interaction.mode=='voice'
    assert editor.textCursor().selectedText()=='旧词'
    owner._utterance_commands.drain(Audio(),allowed=True);QCoreApplication.processEvents();QCoreApplication.processEvents()
    assert runtime.input._voice_token['ready']
    for gesture in DIRECTIONS:swipe(owner,gesture);assert runtime.input.interaction.mode=='voice'
    owner._utterance_active=True;owner._inline_input._view.update(phase='listening',ready=True)
    swipe(owner,'tap');assert runtime.input._voice_token['finishing'] and not sent
    assert owner._finish_utterance_event.is_set() and any(m['type']=='finish' for m in fixture[1].messages)
    commit=QInputMethodEvent();commit.setCommitString('新词');QCoreApplication.sendEvent(editor,commit)
    finish_fixture_voice(owner);owner._inline_input.settled.emit('dictated','新词')
    wait(lambda:runtime.input.interaction.mode=='action')
    assert editor.toPlainText()=='新词' and not sent and runtime.input._voice_token is None
    assert calls==[{'allow_start':True,'allow_end':False,'explicit':True},True]


def test_voice_cancel_restores_word_and_late_start_cannot_steal_new_editor(hub,monkeypatch):
    from proximic_ring.voice.utterance_commands import UtteranceCommands
    owner,_,runtime,sent,_=hub;d,editor=editable(runtime);editor.setPlainText('原词')
    owner._recognition_event.set();owner._utterance_commands=UtteranceCommands(owner._disconnect_event)
    swipe(owner,'tap');token=runtime.input._voice_token
    editor.setPlainText('尚未确认的识别');runtime.input.cancel_voice()
    assert editor.toPlainText()=='原词' and runtime.input.interaction.mode=='action'
    accepted=[];runtime.input._prepare_voice(accepted.append,token)
    assert accepted==[False] and not sent


@pytest.mark.parametrize('rank',[1,2,3,4])
def test_reference_geometry_long_values_and_keyboard(hub,tmp_path,rank):
    owner,_,runtime,sent,_=hub;d=runtime.dialog;d.resize(1332,752);QTest.qWait(30)
    item=runtime.snapshot['items'][rank-1]
    item['workflow']['parameters']=[dict(name='body',description='课堂要点')]
    item['display']=dict(title='记录课堂要点',summary='把课堂要点写入备忘录',parameters={'body':dict(label='要点',placeholder='说出要点')},
        sentence=[dict(text='在备忘录记下'),dict(parameter='body')])
    d.choose(rank);QTest.qWait(50)
    assert abs(d.parameters.width()-420*d.wheel.scale)<2
    assert d.wheel.rect().contains(d.parameters.geometry())
    shots=Path(os.environ.get('MYTHLINK_SCREENSHOT_DIR',str(tmp_path)));shots.mkdir(exist_ok=True,parents=True)
    d.grab().save(str(shots/f'hub-action-{rank}.png'))
    d.enter_word(0);editor=d.editors['body'];editor.setPlainText('记录用户操作经验；提取可复用流程；通过手势触发 Agent 执行。\n'*4)
    QTest.qWait(60);assert not d.sentence.expanded and editor.selection_preview
    QTest.keyClick(editor,Qt.Key_F2)
    QTest.qWait(20);assert d.sentence.expanded and editor.isVisible() and not editor.selection_preview
    assert d.wheel.rect().contains(d.parameters.geometry())
    d.grab().save(str(shots/f'hub-word-{rank}.png'))
    editor.moveCursor(QTextCursor.End);QTest.keyClick(editor,Qt.Key_Return,Qt.ShiftModifier)
    assert editor.toPlainText().endswith('\n\n')
    QTest.keyClick(editor,Qt.Key_Escape);assert runtime.input.interaction.mode=='action'
    original=editor.toPlainText();QTest.keyClick(d,Qt.Key_Escape);assert runtime.input.stage=='hub'
    d.choose(rank);assert d.editors['body'].toPlainText()==original and not sent


def test_pointer_swipe_does_not_trigger_followup_cancel_and_token_click_enters_word(hub):
    from PySide6.QtCore import QPoint
    owner,_,runtime,sent,_=hub;d=runtime.dialog
    center=d.wheel.map_rect(650,360,0,0).center();end=center-QPoint(0,round(80*d.wheel.scale))
    QTest.mousePress(d.wheel,Qt.LeftButton,pos=center);QTest.mouseMove(d.wheel,end);QTest.mouseRelease(d.wheel,Qt.LeftButton,pos=end)
    assert d.panel_direction==1 and runtime.input.interaction.mode=='action' and not sent
    d.back();d.choose(next(i['position'] for i in runtime.snapshot['items'] if i['workflow']['parameters']))
    QTest.qWait(40);rect=d.sentence.rects['title']
    QTest.mouseClick(d.sentence,Qt.LeftButton,pos=rect.center().toPoint())
    assert runtime.input.interaction.mode=='word' and d.editors['title'].hasFocus()


def test_transparent_wheel_entrance_and_title_boundaries(hub,tmp_path):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QImage,QPainter,QColor,QFontMetricsF
    from proximic_ring.agent_workflow.ui.recommendation_wheel import fitted_core_title,CORE_TEXT
    _,_,runtime,_,_=hub;d=runtime.dialog;d.resize(1332,752);QTest.qWait(60);w=d.wheel
    assert d.testAttribute(Qt.WA_TranslucentBackground) and not d.mask().contains(QPoint(8,8))
    center=w.map_rect(650,360,0,0).center()
    w.open_from_center();w.entrance.pause();assert all((t.geometry().center()-center).manhattanLength()<=2 for t in w.tiles)
    w.entrance.setCurrentTime(420);assert abs((w.tiles[0].geometry().center()-center).manhattanLength()-158*w.scale)<2
    shots=Path(os.environ.get('MYTHLINK_SCREENSHOT_DIR',str(tmp_path)));shots.mkdir(exist_ok=True,parents=True)
    overlay=d.grab().toImage();overlay.save(str(shots/'hub-overlay.png'))
    canvas=QImage(overlay.size(),QImage.Format_ARGB32_Premultiplied);canvas.fill(QColor('#edf0f4'))
    painter=QPainter(canvas);painter.drawImage(0,0,overlay);painter.end();canvas.save(str(shots/'hub-preview.png'))
    for title in ['播放音乐','打开备忘录并记录今天课堂的全部重点内容','W'*1000,'👩🏽\u200d💻📚'*20]:
        face,lines=fitted_core_title(title);assert 1<=len(lines)<=2 and 15<=face.pixelSize()<=19
        for line,rect in lines:assert CORE_TEXT.contains(rect) and QFontMetricsF(face).horizontalAdvance(line)<=CORE_TEXT.width()


def test_word_preview_looks_and_navigates_like_candidate_chip(hub,monkeypatch,tmp_path):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QImage,QPainter,QColor
    owner,_,runtime,sent,_=hub;d=runtime.dialog;d.resize(1332,752)
    item=next(i for i in runtime.snapshot['items'] if i['workflow']['parameters'])
    item['display']=dict(title='创建笔记',summary='记录内容',parameters={'title':dict(label='标题',placeholder='输入或说出笔记标题')},
        sentence=[dict(text='创建笔记'),dict(parameter='title')])
    item['parameter_candidates']={'title':[dict(value='课堂要点'),dict(value='周会记录'),dict(value='项目计划')]}
    d.choose(item['position']);swipe(owner,'tap');QTest.qWait(60)
    editor=d.editors['title'];assert editor.selection_preview and not d.sentence.editing
    assert editor.hasFocus() and runtime.input.owns_input_focus() and not d.sentence.expanded
    width=d.sentence.rects['title'].width()
    # A drag starting over the word must not be swallowed by the native text
    # widget as cursor/selection movement while this is a candidate preview.
    start=editor.viewport().rect().center();end=start-QPoint(0,round(80*d.wheel.scale))
    QTest.mousePress(editor.viewport(),Qt.LeftButton,pos=start)
    QTest.mouseMove(editor.viewport(),end);QTest.mouseRelease(editor.viewport(),Qt.LeftButton,pos=end)
    assert editor.toPlainText()=='周会记录' and editor.selection_preview
    assert '2/3' in d.sentence.candidate_hint() and d.sentence.rects['title'].width()==width
    QTest.keyClick(editor,Qt.Key_Down);assert editor.toPlainText()=='课堂要点'
    shots=Path(os.environ.get('MYTHLINK_SCREENSHOT_DIR',str(tmp_path)));shots.mkdir(exist_ok=True,parents=True)
    foreground=d.grab().toImage();canvas=QImage(foreground.size(),QImage.Format_ARGB32_Premultiplied);canvas.fill(QColor('#edf0f4'))
    painter=QPainter(canvas);painter.drawImage(0,0,foreground);painter.end();canvas.save(str(shots/'word-candidates.png'))
    calls=[];monkeypatch.setattr(runtime.input,'start_voice',lambda:calls.append(True))
    QTest.mouseClick(editor.viewport(),Qt.LeftButton)
    assert calls==[True] and not sent


def test_empty_history_is_explicit_and_does_not_open_manual_editor(hub,tmp_path):
    owner,_,runtime,sent,_=hub;d,editor=editable(runtime)
    assert editor.selection_preview and not d.sentence.editing and not d.sentence.expanded
    assert d.sentence.candidate_hint()=='暂无历史候选 · tap 语音输入'
    for direction in ('swipe-up','swipe-down'):
        swipe(owner,direction)
        assert editor.toPlainText()=='' and editor.selection_preview and not d.sentence.editing
    shots=Path(os.environ.get('MYTHLINK_SCREENSHOT_DIR',str(tmp_path)));shots.mkdir(exist_ok=True,parents=True)
    d.grab().save(str(shots/'word-no-history.png'))
    preedit=QInputMethodEvent('笔画候选',[]);QCoreApplication.sendEvent(editor,preedit)
    assert runtime.input._composing and d.sentence.editing and not editor.selection_preview
    commit=QInputMethodEvent();commit.setCommitString('已选文字');QCoreApplication.sendEvent(editor,commit)
    assert editor.toPlainText()=='已选文字' and not sent
