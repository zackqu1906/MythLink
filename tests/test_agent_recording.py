import base64
import io
import json
import queue
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from proximic_ring.agent_workflow.recording.recording import ExperienceRecorder
from proximic_ring.agent_workflow.recording.records import read_session, episode_detail, export_html
from proximic_ring.agent_workflow.agent.rpc import RPC


@pytest.fixture
def recorder(tmp_path):
    r = ExperienceRecorder(tmp_path)
    yield r
    r.close()


def snapshot(r):
    assert r.flush()
    assert not r.health['recordingError']
    return read_session(r.directory)


def test_default_recording_and_review_roots_are_isolated(tmp_path):
    from proximic_ring.agent_workflow.recording import records
    r=ExperienceRecorder()
    try:
        assert r.root==tmp_path/'agent-experience'
        r.new_episode('test-only task','test')
        assert r.flush()
        assert records.sessions()==[r.directory/'session.json']
        assert export_html()==r.root/'index.html'
    finally:
        r.close()


def start(r, goal='打开项目 A 群', request=1, turn='t1'):
    episode = r.new_episode(goal, 'ring_voice', {'app':'微信'})
    r.rpc('outgoing', {'id':request,'method':'turn/start','params':{'threadId':'thread',
          'input':[{'type':'text','text':goal}]}}, episode_id=episode, thread_id='thread')
    # Server notification can arrive before the RPC response.
    r.rpc('incoming', {'method':'turn/started','params':{'threadId':'thread','turn':{'id':turn,'status':'inProgress'}}})
    r.rpc('incoming', {'id':request,'result':{'turn':{'id':turn,'status':'inProgress'}}})
    return episode


def item(r, payload, turn='t1', method='item/completed'):
    r.rpc('incoming', {'method':method,'params':{'threadId':'thread','turnId':turn,
          'item':payload, 'completedAtMs':12345}})


def test_full_tool_evidence_attachments_and_unknown_goal_status(recorder):
    episode = start(recorder)
    picture = base64.b64encode(b'example-image-bytes').decode()
    initial = {'id':'call1','type':'mcpToolCall','server':'cua_repl','tool':'js',
               'arguments':{'code':'await app.click({target: 4})'},'status':'inProgress'}
    item(recorder, initial, method='item/started')
    item(recorder, {**initial, 'status':'completed','durationMs':123,
        'result':{'content':[{'type':'text','text':'项目 A 群已显示'},
                             {'type':'image','mimeType':'image/png','data':picture}],
                  'structuredContent':{'app':'微信','target':4},'_meta':{'source':'cua'}}})
    recorder.rpc('incoming', {'method':'turn/completed','params':{'threadId':'thread','turn':{'id':'t1','status':'completed'}}})
    data = snapshot(recorder)
    assert data['episodes'][0]['status'] == 'completed'
    assert data['episodes'][0]['goal_status'] == 'unknown'
    tool = data['items'][0]['payload']
    assert tool['arguments'] == initial['arguments']
    assert tool['result']['structuredContent']['app'] == '微信'
    assert tool['durationMs'] == 123
    image = tool['result']['content'][1]
    assert (recorder.directory/image['attachment']).read_bytes() == b'example-image-bytes'
    assert picture not in (recorder.directory/'events.jsonl').read_text()
    assert len(data['observations']) == 1
    assert all(e['episode_id']==episode for e in data['events'])
    assert episode_detail(data, episode)['interpretation']['semantic_workflow'] == 'not_generated'


def test_late_events_and_reused_rpc_ids_do_not_cross_tasks(recorder):
    first = start(recorder)
    second = start(recorder, '打开计算器', request=2, turn='t2')
    # Same numeric id as outgoing start, but this is a SERVER request.
    recorder.rpc('incoming', {'id':2,'method':'item/tool/call','params':{'threadId':'thread',
        'turnId':'t1','tool':'ask_ring_user','arguments':{'question':'标题？'}}}, episode_id=second)
    recorder.rpc('outgoing', {'id':2,'result':{'success':True,'contentItems':[{'type':'inputText','text':'项目 A'}]}},episode_id=second)
    item(recorder, {'id':'late','type':'agentMessage','text':'旧任务的尾部'}, turn='t1')
    item(recorder, {'id':'new','type':'agentMessage','text':'新任务'}, turn='t2')
    data=snapshot(recorder)
    assert next(e for e in data['events'] if e['direction']=='outgoing' and e['method']=='rpc/response')['episode_id']==first
    assert {i['item_id']:i['episode_id'] for i in data['items']} == {'late':first,'new':second}


def test_deltas_final_items_plans_and_token_usage_are_retained_without_double_count(recorder):
    start(recorder)
    for method, fields in [
        ('item/reasoning/summaryPartAdded', {'itemId':'r','summaryIndex':0}),
        ('item/reasoning/summaryTextDelta', {'itemId':'r','summaryIndex':0,'delta':'先确认'}),
        ('item/reasoning/summaryTextDelta', {'itemId':'r','summaryIndex':0,'delta':'目标'}),
        ('turn/plan/updated', {'plan':[{'step':'打开会话','status':'inProgress'}]}),
        ('thread/tokenUsage/updated', {'tokenUsage':{'total':{'totalTokens':20},'last':{'totalTokens':10}}}),
    ]:
        recorder.rpc('incoming', {'method':method,'params':{'threadId':'thread','turnId':'t1',**fields}})
    item(recorder, {'id':'r','type':'reasoning','summary':['确认目标群'], 'content':['private reasoning not needed']})
    recorder.rpc('incoming', {'method':'item/reasoning/summaryTextDelta','params':{
        'threadId':'thread','turnId':'t1','itemId':'r','summaryIndex':0,'delta':'late'}})
    data=snapshot(recorder)
    assert data['items'][0]['payload']['summary']==['确认目标群']
    assert 'content' not in data['items'][0]['payload']
    journal=(recorder.directory/'events.jsonl').read_text()
    assert 'private reasoning' not in journal
    assert 'turn/plan/updated' in journal and 'totalTokens' in journal
    assert '先确认' in journal and 'late' in journal


def test_credentials_secret_answers_and_account_data_are_not_persisted(recorder):
    start(recorder)
    recorder.rpc('outgoing',{'id':10,'method':'account/read','params':{'refreshToken':False}})
    recorder.rpc('incoming',{'id':10,'result':{'email':'private@example.test','accessToken':'account-secret'}})
    recorder.rpc('incoming',{'method':'account/updated','params':{'email':'account-notification@example.test'}})
    recorder.rpc('incoming',{'id':11,'method':'item/tool/requestUserInput','params':{
        'threadId':'thread','turnId':'t1','questions':[{'id':'secret','isSecret':True,'question':'密码'}]}})
    recorder.rpc('outgoing',{'id':11,'result':{'answers':{'secret':{'answers':['user-secret']}}}})
    item(recorder, {'id':'call','type':'mcpToolCall','tool':'js','server':'cua_repl','status':'completed',
        'arguments':{'password':'tool-secret','code':'Authorization: Bearer abcdef123456'},
        'result':{'content':[], 'structuredContent':{'tokenUsage':123,'api_key':'key-secret'}}})
    data=snapshot(recorder)
    journal=(recorder.directory/'events.jsonl').read_text()
    for secret in ('account-secret','private@example.test','account-notification@example.test','user-secret','tool-secret','abcdef123456','key-secret'):
        assert secret not in journal
        assert secret not in json.dumps(data)
    assert data['items'][0]['payload']['result']['structuredContent']['tokenUsage']==123


def test_unknown_events_remote_images_and_missing_states_are_not_invented(recorder):
    episode=start(recorder)
    recorder.rpc('incoming', {'method':'future/newEvent','params':{'threadId':'thread','turnId':'t1','value':7}})
    item(recorder, {'id':'call','type':'mcpToolCall','tool':'js','server':'cua_repl','status':'failed',
        'arguments':{'code':'two actions'},'result':{'content':[{'type':'image','mimeType':'image/png','data':'!invalid!'},
        {'type':'resource_link','uri':'https://example.test/private.png'}]},'error':{'message':'partial failure'}})
    data=snapshot(recorder)
    assert any(e['method']=='future/newEvent' for e in data['events'])
    assert data['items'][0]['payload']['result']['content'][0]['availability']=='invalid_base64'
    assert not list((recorder.directory/'attachments').iterdir())
    assert data['episodes'][0]['goal_status']=='unknown'


def test_queue_limits_are_visible_and_shutdown_marks_unfinished_tasks(tmp_path):
    r=ExperienceRecorder(tmp_path,max_queue_bytes=1024)
    episode=start(r)
    assert r.flush()
    r.local('large',{'text':'x'*2048},episode_id=episode)
    r.local('small',{},episode_id=episode)
    assert r.flush()
    assert r.health['recordingDroppedEvents']>=1
    r.close()
    data=read_session(r.directory)
    assert data['episodes'][0]['status']=='unfinished'
    assert data['session']['recordingDroppedEvents']>=1
    assert 'recording_gap' in (r.directory/'events.jsonl').read_text()


def test_failed_start_does_not_poison_later_turn_association(recorder):
    first=recorder.new_episode('first','test')
    recorder.rpc('outgoing',{'id':1,'method':'turn/start','params':{'threadId':'thread'}},episode_id=first)
    recorder.rpc('incoming',{'id':1,'error':{'message':'rejected'}})
    second=start(recorder,'second',request=2,turn='t2')
    item(recorder,{'id':'m','type':'agentMessage','text':'second'},turn='t2')
    data=snapshot(recorder)
    assert data['episodes'][0]['status']=='start_failed'
    assert data['items'][0]['episode_id']==second


def test_session_configuration_is_taken_from_actual_server_responses(recorder):
    recorder.rpc('outgoing',{'id':1,'method':'initialize','params':{}})
    recorder.rpc('incoming',{'id':1,'result':{'userAgent':'codex/test-version','platformOs':'macos'}})
    recorder.rpc('outgoing',{'id':2,'method':'thread/start','params':{'developerInstructions':'test instructions'}})
    recorder.rpc('incoming',{'id':2,'result':{'thread':{'id':'thread'},'model':'selected-model','reasoningEffort':'medium'}})
    data=snapshot(recorder)
    assert data['session']['agent_configuration']=={'userAgent':'codex/test-version','platformOs':'macos',
        'model':'selected-model','reasoningEffort':'medium'}


def test_unmatched_turn_is_preserved_without_attaching_to_active_episode(recorder):
    active=start(recorder)
    recorder.rpc('incoming',{'method':'future/item','params':{'threadId':'thread','turnId':'unrelated'}},episode_id=active)
    event=snapshot(recorder)['events'][-1]
    assert event['episode_id'] is None and event['association']=='unresolved'


def test_html_is_local_escaped_and_includes_raw_evidence(recorder):
    start(recorder, '<script>alert(1)</script>')
    item(recorder,{'id':'m','type':'agentMessage','text':'<img src=x onerror=alert(1)>'})
    snapshot(recorder)
    index=export_html(recorder.root)
    assert '&lt;script&gt;' in index.read_text()
    page=next(recorder.directory.glob('episode-*.html')).read_text()
    assert '<script>' not in page and '&lt;img' in page
    assert '未收到推理摘要' in page and '未自动验证' in page
    assert 'events.jsonl' in page and '完整事件时间线' in page


def test_rpc_observes_both_directions_once_and_preserves_setup_deferred_events():
    observed=[]
    rpc=RPC.__new__(RPC);rpc.observer=lambda direction,msg:observed.append((direction,msg))
    rpc.inbox=queue.Queue();rpc.next_id=0
    rpc.proc=SimpleNamespace(stdin=io.StringIO(),stdout=io.StringIO(
        '{"method":"notice","params":{}}\n{"id":1,"result":{"ready":true}}\n'),poll=lambda:0)
    rpc._read()
    assert rpc.call('initialize')=={'ready':True}
    assert rpc.inbox.get()['method']=='notice'
    assert len([x for x in observed if x[0]=='incoming'])==2
    assert len([x for x in observed if x[0]=='outgoing'])==1


def test_observer_failure_does_not_break_transport():
    rpc=RPC.__new__(RPC)
    def fail(*_): raise OSError('disk unavailable')
    rpc.observer=fail;rpc.proc=SimpleNamespace(stdin=io.StringIO())
    rpc.write({'id':3,'result':{'action':'decline'}})
    assert json.loads(rpc.proc.stdin.getvalue())['result']['action']=='decline'


def test_recorder_initialization_failure_is_reported_not_raised(tmp_path):
    occupied=tmp_path/'file';occupied.write_text('not a directory')
    r=ExperienceRecorder(occupied);r._ready.wait(2)
    assert r.health['recordingState']=='error'
    r.close()


def test_flow_queue_steer_cancel_and_source_are_recorded_separately(tmp_path):
    from proximic_ring.agent_workflow.agent.flow import Flow
    flow=Flow(with_ring=False,runtime_dir=tmp_path/'runtime')
    flow.recorder=ExperienceRecorder(tmp_path/'records');flow.tid='thread'
    class Wire:
        counter=0
        def send(self,method,params):
            self.counter+=1
            flow._record_rpc('outgoing',{'id':self.counter,'method':method,'params':params})
            return self.counter
    flow.rpc=Wire()
    def feed(msg): flow._record_rpc('incoming',msg);flow.handle(msg)
    try:
        flow.command({'type':'text','text':'first','source':'agent_editor','context':{'trigger':'swipe-up'}})
        feed({'id':1,'result':{'turn':{'id':'t1'}}})
        feed({'method':'turn/started','params':{'threadId':'thread','turn':{'id':'t1'}}})
        flow.command({'type':'text','text':'second','source':'ring_voice'})
        flow.command({'type':'steer','text':'change first','targetTurn':'t1','source':'agent_editor'})
        flow.control('cancel','test')
        data=snapshot(flow.recorder)
        assert [e['goal'] for e in data['episodes']]==['first','second']
        assert data['episodes'][0]['source']=='agent_editor'
        assert data['episodes'][0]['context']['trigger']=='swipe-up'
        assert data['episodes'][1]['status']=='cancelled_before_start'
        assert {e['payload']['text']:e['episode_id'] for e in data['events'] if e['method']=='input'}=={
            'first':data['episodes'][0]['id'],'second':data['episodes'][1]['id'],
            'change first':data['episodes'][0]['id']}
        steer=next(e for e in data['events'] if e['method']=='turn/steer')
        assert steer['episode_id']==data['episodes'][0]['id']
    finally:
        flow.close_recording();flow.events.close()


def test_secret_answer_command_is_omitted_from_both_logs(tmp_path):
    from proximic_ring.agent_workflow.agent.flow import Flow
    flow=Flow(with_ring=False,runtime_dir=tmp_path/'runtime')
    flow.recorder=ExperienceRecorder(tmp_path/'records')
    flow.pending[1]={'id':1,'token':'question-token','kind':'question','secret':True,'blocked':'请在原应用输入'}
    command={'type':'answer','questionToken':'question-token','text':'private-answer-value'}
    try:
        flow.command(command)
        data=snapshot(flow.recorder)
        assert 'private-answer-value' not in json.dumps(data)
        assert 'private-answer-value' not in (flow.root/'events.jsonl').read_text()
        assert command['text']=='private-answer-value'
    finally:
        flow.close_recording();flow.events.close()
