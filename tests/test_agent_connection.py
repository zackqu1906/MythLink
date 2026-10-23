"""Connection setup verifies the runtime without submitting a user's task."""
import queue
from pathlib import Path

import pytest

from proximic_ring.agent_workflow.agent.connection import ConnectionIssue, verify_computer_use, verify_login
from test_inline_ui import inline_ui
from test_ui_shell import visual_child


class FakeRPC:
    def __init__(self, **_):
        self.calls=[]
        self.sent=[]
        self.inbox=queue.Queue()
        self.closed=False
        self.account={'requiresOpenaiAuth':True,'account':{'type':'chatgpt'}}
        self.servers={'data':[{'name':'cua_repl','runtimeStatus':'connected',
                              'authStatus':'unsupported','tools':{'js':{},'turn_ended':{},'js_reset':{}}}]}

    def initialize(self): pass

    def call(self, method, params=None, **_):
        self.calls.append((method,params))
        return {'account/read':self.account, 'thread/start':{'thread':{'id':'test-thread'}},
                'mcpServerStatus/list':self.servers}[method]

    def close(self): self.closed=True

    def send(self, method, params):
        self.sent.append((method,params))
        return len(self.sent)+100


@pytest.mark.parametrize('account,state',[
    ({'requiresOpenaiAuth':True,'account':None}, 'login'),
    ({}, 'error'),
    ({'requiresOpenaiAuth':False,'account':None}, None),
    ({'requiresOpenaiAuth':True,'account':{'type':'chatgpt'}}, None),
])
def test_login_check_does_not_confuse_missing_credentials_with_success(account,state):
    rpc=FakeRPC();rpc.account=account
    if state:
        with pytest.raises(ConnectionIssue) as error:verify_login(rpc)
        assert error.value.state==state
    else:verify_login(rpc)
    assert rpc.calls==[('account/read',{'refreshToken':False})]


@pytest.mark.parametrize('change',[
    {'runtimeStatus':'failed'}, {'runtimeStatus':'starting'}, {'runtimeStatus':'disabled'},
    {'tools':{}}, {'toolsError':'unavailable'}, {'authStatus':'notLoggedIn'},
])
def test_computer_check_rejects_cached_tools_when_server_is_not_usable(change):
    rpc=FakeRPC();rpc.servers['data'][0].update(change)
    with pytest.raises(ConnectionIssue,match='电脑操作工具'):verify_computer_use(rpc,'thread')
    assert rpc.calls[0][1]['threadId']=='thread'


def test_computer_check_follows_pagination_and_rejects_other_servers():
    rpc=FakeRPC()
    rpc.servers={'data':[{'name':'other','tools':{'js':{}}}]}
    with pytest.raises(ConnectionIssue):verify_computer_use(rpc,'thread')
    pages=iter([{'data':[],'nextCursor':'second'},
                {'data':[{'name':'cua_repl','tools':{'js':{},'turn_ended':{},'js_reset':{}}}]}])
    calls=[]
    def call(method,params):
        calls.append((method,params));return next(pages)
    rpc.call=call
    verify_computer_use(rpc,'thread')
    assert calls[1][1]['cursor']=='second'


def test_flow_setup_and_recheck_share_the_task_connection_without_starting_a_turn(tmp_path,monkeypatch):
    from proximic_ring.agent_workflow.agent import flow as module
    rpc=FakeRPC();monkeypatch.setattr(module,'RPC',lambda **_:rpc)
    flow=module.Flow(with_ring=False,runtime_dir=tmp_path,recording_dir=tmp_path/'records')
    try:
        flow.setup()
        assert flow.state['connectionState']=='ready'
        flow.command({'type':'check_connection'})
        assert [m for m,_ in rpc.calls]==['account/read','thread/start','mcpServerStatus/list',
                                         'account/read','mcpServerStatus/list']
        assert rpc.calls[1][1]['ephemeral'] is False
        assert flow.recorder is not None and flow.tid=='test-thread'
        flow.turn='running';before=list(rpc.calls)
        flow.command({'type':'check_connection'})
        assert rpc.calls==before and flow.state['connectionState']=='ready'
    finally:
        flow.close_recording();flow.events.close()


@pytest.fixture
def connection_ui(inline_ui,monkeypatch,tmp_path):
    from proximic_ring.ui.agent import agent_controller as module
    from proximic_ring.agent_workflow.agent import flow as flow_module
    owner=inline_ui[0];agent=owner.agent;instances=[]
    # These fixtures exercise the standalone executor's readiness contract.
    # The shipped controller defaults to NativeFlow (covered separately).
    agent._flow_factory=flow_module.Flow
    def rpc_factory(**_):
        rpc=FakeRPC();instances.append(rpc);return rpc
    monkeypatch.setattr(module,'find_codex',lambda:'/test/codex')
    monkeypatch.setattr(module,'find_codex_app',lambda:Path('/Applications/Codex.app'))
    monkeypatch.setattr(module,'cache_root',lambda:tmp_path)
    monkeypatch.setattr(flow_module,'RPC',rpc_factory)
    yield inline_ui,agent,instances
    agent.close()


def wait_for(predicate):
    from PySide6.QtTest import QTest
    for _ in range(150):
        if predicate():return
        QTest.qWait(10)
    assert predicate()


def test_settings_connect_recheck_disconnect_and_retry_do_not_open_workspace(connection_ui):
    _,agent,instances=connection_ui
    agent.connectCodex();agent.connectCodex()
    wait_for(lambda:agent.connectionReady)
    assert len(instances)==1 and not agent.visible and not agent.busy
    assert agent.workspace.state['turnId']==''
    agent.connectCodex()
    wait_for(lambda:len(instances[0].calls)==5 and agent.connectionReady)
    assert len(instances)==1
    agent._apply_state({**agent.workspace.state,'turnId':'running'})
    agent.connectCodex()
    assert len(instances[0].calls)==5 and agent.connectionReady
    agent._apply_state({**agent.workspace.state,'turnId':''})
    instances[0].inbox.put({'transportClosed':1})
    wait_for(lambda:agent.connectionStatus=='已断开' and not agent._thread.is_alive())
    assert not agent.connectionReady and not agent.busy and instances[0].closed
    agent.connectCodex()
    wait_for(lambda:agent.connectionReady)
    assert len(instances)==2


@pytest.mark.parametrize('problem',['login','transport'])
def test_failed_connection_preserves_actionable_reason_and_closes_runtime(connection_ui,monkeypatch,problem):
    from proximic_ring.agent_workflow.agent import flow as module
    _,agent,_=connection_ui
    rpc=FakeRPC()
    if problem=='login':rpc.account['account']=None
    else:
        def fail():raise RuntimeError('internal path should not appear in the UI')
        rpc.initialize=fail
    monkeypatch.setattr(module,'RPC',lambda **_:rpc)
    agent.connectCodex()
    wait_for(lambda:agent._thread is not None and not agent._thread.is_alive())
    wait_for(lambda:not agent.connectionChecking)
    assert agent.connectionStatus=={'login':'待登录','transport':'连接失败'}[problem]
    assert agent.connectionNeedsHelp and not agent.connectionReady and not agent.busy
    assert rpc.closed and 'internal path' not in agent.connectionMessage
    assert agent._flow is None


def test_missing_computer_use_does_not_disable_agent_and_recheck_can_restore_it(connection_ui,monkeypatch):
    from proximic_ring.agent_workflow.agent import flow as module
    _,agent,_=connection_ui
    rpc=FakeRPC();available=rpc.servers;rpc.servers={'data':[]}
    monkeypatch.setattr(module,'RPC',lambda **_:rpc)
    agent.connectCodex()
    wait_for(lambda:agent.connectionReady)
    assert not rpc.closed and not agent.connectionNeedsHelp
    assert '其他可用工具任务' in agent.connectionMessage
    assert agent.workspace.state['computerUseState']=='unavailable'
    rpc.servers=available
    agent.connectCodex()
    wait_for(lambda:agent.connectionReady and agent.workspace.state.get('computerUseState')=='ready')
    assert agent.connectionReady and not rpc.closed


def test_non_ui_task_completes_and_is_recorded_without_computer_use(tmp_path,monkeypatch):
    from proximic_ring.agent_workflow.agent import flow as module
    from proximic_ring.agent_workflow.recording.records import read_session
    rpc=FakeRPC();rpc.servers={'data':[]}
    monkeypatch.setattr(module,'RPC',lambda **_:rpc)
    flow=module.Flow(with_ring=False,runtime_dir=tmp_path/'runtime',recording_dir=tmp_path/'records')
    try:
        flow.setup()
        flow.text('解释一下什么是递归',source='test')
        flow.handle({'method':'turn/started','params':{'threadId':flow.tid,'turn':{'id':'answer'}}})
        flow.handle({'method':'item/completed','params':{'threadId':flow.tid,'turnId':'answer',
            'item':{'type':'agentMessage','text':'递归是函数调用自身。'}}})
        flow.handle({'method':'turn/completed','params':{'threadId':flow.tid,'turn':{'id':'answer','status':'completed'}}})
        assert flow.state['status']=='本轮结束' and flow.state['connectionState']=='ready'
        assert flow.state['agent']=='递归是函数调用自身。'
        assert flow.state['computerControlState']=='idle'
        assert all(method!='mcpServer/tool/call' for method,_ in rpc.calls)
        assert flow.recorder.flush()
        assert any(e['method']=='computer_control_not_used' for e in read_session(flow.recorder.directory)['events'])
    finally:
        flow.close_recording();flow.events.close()


def test_optional_computer_use_probe_does_not_hide_transport_errors(tmp_path,monkeypatch):
    from proximic_ring.agent_workflow.agent import flow as module
    rpc=FakeRPC();original=rpc.call
    def call(method,params):
        if method=='mcpServerStatus/list':raise TimeoutError('transport unavailable')
        return original(method,params)
    rpc.call=call
    monkeypatch.setattr(module,'RPC',lambda **_:rpc)
    flow=module.Flow(with_ring=False,runtime_dir=tmp_path/'runtime',recording_dir=tmp_path/'records')
    try:
        with pytest.raises(TimeoutError):flow.setup()
        assert flow.state['connectionState']!='ready'
    finally:
        flow.close_recording();flow.events.close()


@pytest.mark.parametrize('size',[(1200,780),(940,700)])
def test_agent_setup_lives_only_in_settings_with_clickable_actions(connection_ui,monkeypatch,tmp_path,size):
    from PySide6.QtCore import QObject, QMetaObject, QPointF, Qt
    from PySide6.QtTest import QTest
    from proximic_ring.ui.agent import agent_controller as module
    fixture,agent,instances=connection_ui;root=fixture[3]
    root.resize(*size);root.show()
    sidebar=root.findChild(QObject,'mainSidebar')
    assert visual_child(sidebar,'agentWorkspaceButton') is None
    dialog=root.findChild(QObject,'runtimeSettingsDialog')
    QMetaObject.invokeMethod(dialog,'open');QTest.qWait(150)
    group=root.findChild(QObject,'settingsAgentGroup')
    scroll=root.findChild(QObject,'runtimeSettingsScroll').property('contentItem')
    scroll.setProperty('contentY',group.mapToItem(scroll.property('contentItem'),QPointF()).y()-16)
    QTest.qWait(50)

    def item(name):return visual_child(group,name)
    def click(name):
        button=item(name)
        assert button.isVisible() and button.isEnabled()
        centre=button.mapToScene(QPointF(button.width()/2,button.height()/2))
        assert 0 < centre.x() < root.width() and 0 < centre.y() < root.height()
        QTest.mouseClick(root,Qt.LeftButton,Qt.NoModifier,centre.toPoint());QTest.qWait(40)

    assert item('agentWorkspaceButton') is None and not instances
    assert not item('agentConnectionHelpButton').isVisible()
    click('agentConnectButton')
    wait_for(lambda:agent.connectionReady)
    assert item('agentConnectionStatus').property('text')=='已连接'
    assert item('agentConnectionStatus').property('color').name()=='#16875c'
    assert root.grabWindow().save(str(tmp_path/f'agent-settings-{size[0]}.png'))
    agent._apply_state({**agent.workspace.state,'turnId':'running'})
    assert not item('agentConnectButton').isEnabled()
    agent._apply_state({**agent.workspace.state,'turnId':''})
    assert not agent.visible
    QMetaObject.invokeMethod(dialog,'close')

    # Missing installation has a single useful action, not an inert Connect button.
    agent._flow.stop.set();wait_for(lambda:not agent._thread.is_alive())
    monkeypatch.setattr(module,'find_codex',lambda:None)
    monkeypatch.setattr(module,'find_codex_app',lambda:None)
    agent._connection_state='idle'
    QMetaObject.invokeMethod(dialog,'open');QTest.qWait(150)
    scroll.setProperty('contentY',group.mapToItem(scroll.property('contentItem'),QPointF()).y()-16)
    QTest.qWait(40)
    assert item('agentConnectButton').property('text')=='安装 Codex'
    assert not item('agentConnectionHelpButton').isVisible()
    urls=[]
    monkeypatch.setattr(module.QDesktopServices,'openUrl',lambda url:urls.append(url.toString()) or True)
    click('agentConnectButton')
    assert urls==['https://developers.openai.com/codex/app']

