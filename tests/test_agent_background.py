"""Regression contracts for background ownership, decisions and summary history."""
from copy import deepcopy
from pathlib import Path
import json
import queue
import subprocess
import threading

import pytest
from proximic_ring.agent_workflow.agent.background_codex import BackgroundCodex
from proximic_ring.agent_workflow.agent.background_service import BackgroundService, private_directory
from proximic_ring.agent_workflow.agent.native_codex import canonical_turns

TID='00000000-0000-4000-8000-000000000001'
class Transport:
    def __init__(self):self.inbox=queue.Queue();self.closed=threading.Event();self.sent=[];self.sock=None
    def connect(self):self.sock=True
    def close(self):self.closed.set()
    def send(self,message):self.sent.append(message)
    def broadcast(self,method,version,params):self.send(dict(type='broadcast',method=method,version=version,params=params))

class Service:
    def __init__(self):self.calls=[]
    def call(self,method,params):
        self.calls.append((method,deepcopy(params)))
        return {'thread':{'id':TID}}

def backend(tmp_path,service=None):
    return BackgroundCodex(runtime_dir=tmp_path,codex_path='/test/codex',transport=Transport(),
        service_factory=lambda _:service,opener=lambda *_a,**_k:pytest.fail('Must never open desktop'))

def test_first_submission_uses_background_owner_without_queue_or_startup_prompt(tmp_path):
    service=Service();rpc=backend(tmp_path,service)
    inputs=[{'type':'text','text':'任务'}, {'type':'localImage','path':'/tmp/attached.png'}]
    rpc.send('turn/start',{'input':inputs})
    assert [m for m,_ in service.calls]==['create','turn/start']
    assert service.calls[1][1]['input']==[{'type':'text','text':'任务','text_elements':[]},inputs[1]]
    assert inputs[0]=={'type':'text','text':'任务'}
    assert rpc.owned and rpc.tid==TID


def test_first_semantic_snapshot_reference_survives_background_ipc(tmp_path):
    from proximic_ring.agent_workflow.agent.attachments import turn_input
    service=Service();rpc=backend(tmp_path,service)
    inputs=turn_input('看一下这些公式',visual_context={
        'availability':'captured','path':'/tmp/frozen-formula.png','requested_at':'submit-time'})
    rpc.send('turn/start',{'input':inputs})
    actual=service.calls[1][1]['input']
    assert [item['type'] for item in actual]==['text','text']
    assert actual[0]['text']=='看一下这些公式'
    assert '/tmp/frozen-formula.png' in actual[1]['text'] and 'view_image' in actual[1]['text']
    assert all(item['text_elements']==[] for item in actual)

@pytest.mark.parametrize('owner', ['native-owner',None])
def test_attach_follows_existing_owner_or_resumes_without_renaming(tmp_path,owner):
    service=Service();rpc=backend(tmp_path,service)
    class Metadata:
        def call(self,*_,**__):return {'thread':{'id':TID,'ephemeral':False,'name':'existing'}}
        def close(self):pass
    rpc.metadata=Metadata;rpc.discover=lambda _:owner
    rpc.attach(TID)
    assert rpc.owner==owner
    assert [m for m,_ in service.calls]==([] if owner else ['resume'])
    assert rpc.attach_title=='existing'


def test_writer_conflict_does_not_replace_current_connection(tmp_path):
    rpc=backend(tmp_path)
    class Service:
        def call(self,*_):raise RuntimeError('already has an active writer')
    class Metadata:
        def call(self,*_,**__):return {'thread':{'id':TID,'ephemeral':False}}
        def close(self):pass
    rpc.metadata=Metadata;rpc.discover=lambda _:None;rpc.service_factory=lambda _:Service()
    with pytest.raises(RuntimeError,match='手动打开'):rpc.attach(TID)
    assert rpc.tid is None and rpc.owner is None


def live_state(request):
    return {'id':TID,'requests':[request], 'turnHistory':{'history':{
        'entitiesByKey':{'t':{'turnId':'turn','status':'inProgress','items':[]}},
        'islands':[{'entries':[{'value':'t'}]}]}}}


def test_question_id_zero_projects_once_and_retires_with_server(tmp_path):
    rpc=backend(tmp_path);rpc.tid=TID;rpc.owner='owner'
    q={'id':0,'method':'item/tool/requestUserInput','params':{'threadId':TID,'turnId':'turn','questions':[]}}
    rpc.state=live_state(q);rpc._project();rpc._project()
    events=list(rpc.inbox.queue)
    assert len([e for e in events if e.get('method')=='item/tool/requestUserInput'])==1
    rpc.write({'id':0,'result':{'answers':{}}})
    assert rpc.ipc.sent[-1]['params']['requestId']==0
    with pytest.raises(ValueError,match='重复'):rpc.write({'id':0,'result':{'answers':{}}})
    rpc.state['requests']=[];rpc._project()
    assert list(rpc.inbox.queue)[-1]['method']=='serverRequest/resolved'
    with pytest.raises(ValueError,match='已结束'):rpc.write({'id':0,'result':{'answers':{}}})


def test_private_service_endpoint_rejects_public_directory(tmp_path):
    tmp_path.chmod(0o755)
    with pytest.raises(PermissionError):private_directory(tmp_path)


def test_remodex_summary_preserves_command_and_zero_request_is_removed():
    node=Path('/Applications/ChatGPT.app/Contents/Resources/cua_node/bin/node')
    if not node.is_file():pytest.skip('Requires installed Node runtime')
    module=Path(__file__).resolve().parents[1]/'src/proximic_ring/agent_workflow/agent/desktop_owner/desktop-ipc-conversation-adapter.js'
    code=r'''
const assert=require('node:assert/strict');
const {buildConversationTurn,applyAppServerMessageToConversationState}=require(process.argv[1]);
const old={turnId:'t',items:[{id:'cmd',type:'commandExecution',command:'printf OK',status:'completed',exitCode:0}],completedItemIds:{cmd:true}};
const result=buildConversationTurn({id:'t',status:'completed',itemsView:'summary',items:[{id:'answer',type:'agentMessage',text:'OK'}]},{previousTurn:old});
assert.deepEqual(result.items.map(i=>i.id),['cmd','answer']);assert.equal(result.completedItemIds.cmd,true);
const conversations=new Map([['thread',{id:'thread',requests:[{id:0}]}]]);
applyAppServerMessageToConversationState({message:{method:'serverRequest/resolved',params:{threadId:'thread',requestId:0}},conversations,shouldOwnThread:()=>true});
assert.deepEqual(conversations.get('thread').requests,[]);
'''
    result=subprocess.run([str(node),'-e',code,str(module)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_optimistic_turn_is_never_an_interrupt_or_steer_target(tmp_path):
    rpc=backend(tmp_path);rpc.tid=TID;rpc.owner='owner';rpc.first_start_id=1
    rpc.state=live_state({'id':1,'method':'ignore','params':{}})
    entity=rpc.state['turnHistory']['history']['entitiesByKey']['t']
    entity['turnId']='remodex-pending-turn:thread:1';entity['remodexSyntheticTurnId']=True
    rpc._project()
    assert rpc.active_turn() is None
    assert rpc.first_start_id==1 and rpc.inbox.empty()


@pytest.mark.parametrize('field,value', [('handledByClientId','other-owner'),('method','other-method')])
def test_control_receipt_requires_exact_owner_and_method(tmp_path,field,value):
    rpc=backend(tmp_path);rpc.tid=TID;rpc.owner='owner';rpc.state=live_state({})
    local_id=rpc.send('turn/steer',{'threadId':TID,'expectedTurnId':'turn','input':[]})
    sent=rpc.ipc.sent[-1]
    assert sent['params']['expectedTurnId']=='turn'
    receipt={'type':'response','requestId':sent['requestId'],'method':sent['method'],
        'handledByClientId':'owner','resultType':'success','result':{}}
    rpc.ipc.inbox.put({**receipt,field:value});rpc.poll()
    assert sent['requestId'] in rpc.pending and rpc.inbox.empty()
    rpc.ipc.inbox.put(receipt);rpc.poll()
    assert sent['requestId'] not in rpc.pending
    assert rpc.inbox.get_nowait()=={'id':local_id,'result':{}}


def test_question_receipt_cannot_be_consumed_by_another_method(tmp_path):
    rpc=backend(tmp_path);rpc.tid=TID;rpc.owner='owner'
    rpc.state=live_state({'id':0,'method':'item/tool/requestUserInput',
        'params':{'threadId':TID,'turnId':'turn','questions':[]}})
    rpc._project();rpc.write({'id':0,'result':{'answers':{}}})
    sent=rpc.ipc.sent[-1]
    receipt={'type':'response','requestId':sent['requestId'],'method':sent['method'],
        'handledByClientId':'owner','resultType':'success'}
    rpc.ipc.inbox.put({**receipt,'method':'thread-follower-approve-command'});rpc.poll()
    assert sent['requestId'] in rpc.decision_pending
    rpc.ipc.inbox.put(receipt);rpc.poll()
    assert not rpc.decision_pending
