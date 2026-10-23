import time
import threading
from types import SimpleNamespace
from datetime import datetime,timezone
from PySide6.QtCore import QCoreApplication
from PySide6.QtTest import QTest
from test_inline_ui import inline_ui
from test_agent_ui import finish_fixture_voice
from test_recommendation_execution import catalog
from proximic_ring.agent_workflow.operations.operation_store import OperationStore

import pytest
pytestmark = pytest.mark.usefixtures('legacy_workflow_pipeline')


def wait(predicate):
    # Export/ranking runs on a Python worker; yield the GIL as well as Qt events.
    for _ in range(400):
        QTest.qWait(20)
        time.sleep(.001)
        if predicate():return
    assert predicate()


def configure(owner,catalog,monkeypatch):
    finish_fixture_voice(owner);agent=owner.agent;runtime=agent.recommendations
    agent.workspace.state['recordsRoot']=str(catalog.root)
    captures=[]
    def capture(*a,**kw):
        captures.append(bool(runtime.dialog and runtime.dialog.isVisible()))
        return dict(status='remembered',context=dict(bundle='com.apple.Music',captured_at=datetime.now(timezone.utc).isoformat()))
    monkeypatch.setattr(runtime._focus_channel,'call',capture)
    return agent,runtime,captures


def test_native_request_captures_before_display_and_selection_dispatches_parameters(inline_ui,catalog,monkeypatch):
    agent,runtime,captures=configure(inline_ui[0],catalog,monkeypatch);sent=[]
    monkeypatch.setattr(agent,'_send',lambda command:sent.append(command) or True)
    runtime.open('gesture');wait(lambda:runtime.dialog and runtime.dialog.isVisible())
    assert captures==[False]
    assert runtime.snapshot['context']['app']=='com.apple.Music'
    assert len(runtime.dialog.cards)==1
    runtime.dialog.activateWindow()
    wait(lambda:runtime.dialog.cards[0]['exposure'])
    runtime.dialog.cards[0]['choose'].click()
    assert not sent and runtime.input.stage=='parameters' and runtime.dialog.parameters.isVisible()
    runtime.dialog.editors['title'].setPlainText('课堂要点')
    runtime.dialog.handle_gesture('enter');wait(lambda:sent)
    assert len(sent)==1 and sent[0]['type']=='workflow_recommendation'
    assert '课堂要点' in sent[0]['prompt']
    assert not runtime.dialog.isVisible()


def test_capture_failure_does_not_reuse_last_agent_scene(inline_ui,catalog,monkeypatch):
    agent,runtime,_=configure(inline_ui[0],catalog,monkeypatch)
    agent._record_context=dict(bundle='com.apple.Safari',captured_at=datetime.now(timezone.utc).isoformat())
    monkeypatch.setattr(runtime._focus_channel,'call',lambda *a,**k:{'status':'unavailable'})
    runtime.open();wait(lambda:runtime.snapshot)
    assert runtime.snapshot['context']['app'] is None


def test_busy_agent_does_not_open_or_capture_and_old_gesture_is_ignored(inline_ui,catalog,monkeypatch):
    agent,runtime,captures=configure(inline_ui[0],catalog,monkeypatch)
    agent.workspace.state['turnId']='busy'
    runtime.open('gesture');QTest.qWait(100)
    assert not captures and not runtime.dialog
    agent._gesture('workflow-recommendations','task',threading.Event(),time.monotonic())
    assert not runtime.loading


def test_parameter_page_does_not_reorder_when_catalog_updates(inline_ui,catalog,monkeypatch):
    from proximic_ring.agent_workflow.operations.operation_prototypes import attach
    agent,runtime,_=configure(inline_ui[0],catalog,monkeypatch)
    runtime.open();wait(lambda:runtime.snapshot)
    original=runtime.snapshot
    runtime.dialog.activateWindow();wait(lambda:runtime.dialog.cards[0]['exposure'])
    runtime.dialog.choose(1);runtime.dialog.editors['title'].setPlainText('unsent')
    first=catalog.rows('samples')[0]
    catalog.ingest(dict(id='another',kind='user',source={'session_id':'s','episode_id':'e2'},context={},
        occurred_at=first['occurred_at'],flow=original['items'][0]['workflow']))
    with catalog.db:attach(catalog,next(s for s in catalog.rows('samples') if s['id']=='another'),
        first['operation_id'],original['items'][0]['workflow_revision'],'test')
    runtime.poll();wait(lambda:bool(runtime.dialog.update_notice.text()))
    assert runtime.snapshot is original and runtime.dialog.editors['title'].toPlainText()=='unsent'
    runtime.dialog.reject()
    runtime.open();wait(lambda:runtime.snapshot is not None and runtime.snapshot is not original)
    assert runtime.snapshot['evidence']['tasks']==2


def test_configured_voice_gesture_keeps_editor_priority(inline_ui,catalog,monkeypatch):
    from dataclasses import replace
    owner=inline_ui[0];agent,runtime,_=configure(owner,catalog,monkeypatch)
    calls=[];monkeypatch.setattr(runtime,'open',calls.append)
    owner._global_gesture_bindings=replace(owner._global_gesture_bindings,workflow_recommendations='double-tap')
    agent._focused=True;agent.workspace.focus='task'
    assert not agent.filter_gesture(SimpleNamespace(name='double-tap'),owner._disconnect_event)
    QCoreApplication.processEvents();assert not calls


def test_app_records_request_recaptures_current_app_instead_of_last_agent_task(inline_ui,catalog,monkeypatch):
    import json
    from urllib.request import Request,urlopen
    from proximic_ring.ui.agent import agent_controller as module
    agent,runtime,_=configure(inline_ui[0],catalog,monkeypatch)
    agent._record_context=dict(bundle='com.apple.Safari',captured_at=datetime.now(timezone.utc).isoformat())
    current=['com.apple.Music'];captures=[]
    def capture(*a,**kw):
        captures.append(current[0])
        return dict(status='remembered',context=dict(bundle=current[0],captured_at=datetime.now(timezone.utc).isoformat()))
    monkeypatch.setattr(runtime._focus_channel,'call',capture)
    monkeypatch.setattr(module.QDesktopServices,'openUrl',lambda _:True)
    agent.openRecords();wait(lambda:agent._records_server is not None or bool(agent.errorMessage))
    assert agent._records_server is not None,agent.errorMessage
    server=agent._records_server
    def request():
        data=Request(server.origin+server.prefix+'api/operations/recommend',data=b'{"scope":"user"}',
            headers={'Content-Type':'application/json','Origin':server.origin})
        with urlopen(data,timeout=3) as response:return json.load(response)
    assert request()['context']['app']=='com.apple.Music'
    current[0]='com.apple.Notes'
    assert request()['context']['app']=='com.apple.Notes'
    assert captures==['com.apple.Music','com.apple.Notes']
    assert not agent._commands and not runtime.dialog
