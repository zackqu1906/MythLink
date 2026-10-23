import copy
import base64
import json
import re

import pytest

from test_agent_recording import recorder, item, start, snapshot
from proximic_ring.agent_workflow.recording.records import episode_detail, export_html
from proximic_ring.agent_workflow.recording.step_view import build_step_view


def call(r, name, phase='completed', text=None, **fields):
    payload = {'id':name,'type':'mcpToolCall','server':'cua_repl','tool':'js',
               'arguments':{'code':f'{name}()'},
               'status':'inProgress' if phase=='started' else 'completed', **fields}
    if text is not None:
        payload['result']={'content':[{'type':'text','text':text}]}
    item(r,payload,method='item/'+phase)


def view(r, episode):
    return episode_detail(snapshot(r),episode)['step_view']


def state_result(text='Window: "Notes", App: Notes.\n0 standard window Notes'):
    return {'content':[{'type':'text','text':text}],
            '_meta':{'codex/toolSurface':{'kind':'computerUse','app':{'appId':'com.apple.Notes'}}}}


def test_steps_keep_observations_with_their_own_calls_and_export_exact_evidence(recorder):
    episode=start(recorder)
    call(recorder,'observe','started')
    call(recorder,'observe',result=state_result())
    item(recorder,{'id':'r','type':'reasoning','summary':['打开目标会话']})
    call(recorder,'click','started')
    call(recorder,'click',text='点击已返回')
    call(recorder,'inspect','started')
    call(recorder,'inspect',result=state_result('There has been no change in the accessibility tree for Window: "Notes".'))
    data=snapshot(recorder); original=copy.deepcopy(data)
    steps=build_step_view(data,episode)
    click=steps['steps'][1]
    assert click['granularity']=='tool_call'
    assert click['preceding_agent_messages']['entries'][0]['content']==['打开目标会话']
    assert click['tool_call']['data']['arguments']['code']=='click()'
    assert click['execution_result']['availability']=='recorded'
    assert click['recorded_observations']['availability']=='not_identified'
    assert click['recorded_observations']['entries']==[]
    assert steps['version']==2
    assert steps['observation_policy']=='explicit_returns_in_own_call_only'
    for step in steps['steps']:
        assert 'before_observation' not in step and 'after_observation' not in step
        for observation in step['recorded_observations']['entries']:
            assert observation['source_call_id']==step['call_id']
            assert observation['event_refs']==step['execution_result']['event_refs']
            assert observation['content']==step['execution_result']['data']['result']['content'][0]
    assert data==original
    assert build_step_view(data,episode)==steps
    journal=(recorder.directory/'events.jsonl').read_bytes()
    export_html(recorder.root)
    assert (recorder.directory/'events.jsonl').read_bytes()==journal
    saved=json.loads((recorder.directory/f'episode-{episode}.steps.json').read_text())
    assert saved==steps
    page=(recorder.directory/f'episode-{episode}.html').read_text()
    assert all(label in page for label in ('步骤视图','调用前 Agent 文字','执行结果','本次调用中的观察'))
    assert not any(label in page for label in ('动作前观察','后续观察','候选'))
    for seq in re.findall(r"href='#event-(\d+)'",page):
        assert f"id='event-{seq}'" in page
    click_html = page.split(f"<article id='step-{click['step_id']}'>",1)[1].split('</article>',1)[0]
    for source in (steps['steps'][0],steps['steps'][2]):
        assert f"href='#step-{source['step_id']}'" not in click_html
        assert f"来源：<a href='#step-{source['step_id']}'>步骤 {source['ordinal']} · cua_repl.js</a>" in page
    assert '来源消息标识：r' in click_html
    assert '不从相邻调用补齐前后状态' in page
    assert f'episode-{episode}.steps.json' in page
    assert click['goal_verification']=='unknown'


def test_incomplete_and_completion_only_calls_do_not_invent_time_boundaries(recorder):
    episode=start(recorder)
    item(recorder,{'id':'r','type':'reasoning','summary':['说明先到，但不知道动作何时开始']})
    call(recorder,'only_end',text='返回数据')
    call(recorder,'only_start','started')
    result=view(recorder,episode)['steps']
    assert result[0]['recorded_observations']['reasons']==['no_explicit_observation']
    assert result[0]['preceding_agent_messages']['availability']=='missing'
    assert result[0]['started_at'] is None
    assert result[1]['execution_result']['availability']=='missing'
    assert result[1]['recorded_observations']['reasons']==['completion_event_missing']
    assert result[1]['ended_at'] is None


def test_explanation_uses_only_text_available_before_call_and_excludes_final_answer(recorder):
    episode=start(recorder)
    recorder.rpc('incoming',{'method':'item/reasoning/summaryTextDelta','params':{
        'threadId':'thread','turnId':'t1','itemId':'r','summaryIndex':0,'delta':'先查看'}})
    item(recorder,{'id':'final','type':'agentMessage','phase':'final_answer','text':'最终答案不可作为行动理由'})
    call(recorder,'act','started')
    item(recorder,{'id':'r','type':'reasoning','summary':['后来才有的完整说明']})
    call(recorder,'act',text='返回')
    messages=view(recorder,episode)['steps'][0]['preceding_agent_messages']['entries']
    assert len(messages)==1 and messages[0]['content']==['先查看']
    assert 'partial_explanation' in messages[0]['reasons']
    assert len(messages[0]['event_refs'])==1


def test_finished_explanation_overrides_deltas_without_duplication(recorder):
    episode=start(recorder)
    recorder.rpc('incoming',{'method':'item/reasoning/summaryTextDelta','params':{
        'threadId':'thread','turnId':'t1','itemId':'r','summaryIndex':0,'delta':'旧增量'}})
    item(recorder,{'id':'r','type':'reasoning','summary':['最终说明']})
    call(recorder,'act','started')
    message=view(recorder,episode)['steps'][0]['preceding_agent_messages']['entries'][0]
    assert message['content']==['最终说明']
    assert 'partial_explanation' not in message['reasons']


def test_parallel_calls_do_not_share_observations(recorder):
    episode=start(recorder)
    call(recorder,'observe','started');call(recorder,'observe',result=state_result())
    call(recorder,'a','started');call(recorder,'b','started')
    call(recorder,'a',result=state_result());call(recorder,'b',text='b 返回')
    steps=view(recorder,episode)['steps']
    assert len(steps)==3
    for step in steps[1:]:
        assert 'overlapping_calls' in step['warnings']
    observed=steps[1]['recorded_observations']['entries']
    assert len(observed)==1 and observed[0]['source_call_id']=='a'
    assert 'overlapping_calls' in observed[0]['reasons']
    assert steps[2]['recorded_observations']['entries']==[]


def test_controls_and_intervening_calls_do_not_reuse_old_observations(recorder):
    episode=start(recorder)
    call(recorder,'observe','started');call(recorder,'observe',result=state_result())
    call(recorder,'empty','started');call(recorder,'empty')
    recorder.local('control',{'action':'pause'},episode_id=episode)
    recorder.rpc('outgoing',{'id':9,'method':'turn/steer','params':{
        'threadId':'thread','expectedTurnId':'t1','input':[{'type':'text','text':'改成另一个会话'}]}})
    call(recorder,'act','started')
    assert view(recorder,episode)['steps'][-1]['recorded_observations']['entries']==[]


def test_no_cross_episode_or_turn_association(recorder):
    first=start(recorder,goal='first')
    call(recorder,'first','started');call(recorder,'first',result=state_result())
    second=start(recorder,goal='second',request=2,turn='t2')
    item(recorder,{'id':'second','type':'mcpToolCall','tool':'js','arguments':{}},turn='t2',method='item/started')
    steps=view(recorder,second)['steps']
    assert len(steps)==1
    assert steps[0]['recorded_observations']['entries']==[]
    first_observations=view(recorder,first)['steps'][0]['recorded_observations']['entries']
    assert len(first_observations)==1 and first_observations[0]['source_item_id']=='first'
    data=snapshot(recorder)
    for event in data['events']:
        if event.get('item_id')=='second':event['turn_id']=None
    for indexed in data['items']:
        if indexed['item_id']=='second':indexed['turn_id']=None
    assert build_step_view(data,second)['steps'][0]['preceding_agent_messages']['reasons']==['scope_missing']


def test_dynamic_request_reply_fallback_has_exact_references(recorder):
    episode=start(recorder)
    recorder.rpc('incoming',{'id':1,'method':'item/tool/call','params':{
        'threadId':'thread','turnId':'t1','callId':'dynamic',
        'tool':'custom_tool','arguments':{'value':7}}})
    recorder.rpc('outgoing',{'id':1,'result':{'success':False,'contentItems':[{'type':'inputText','text':'部分返回'}]}})
    steps=view(recorder,episode)['steps']
    assert len(steps)==1
    step=steps[0]
    assert 'request_lifecycle_only' in step['warnings']
    assert step['identity_kind']=='call_id' and step['item_id'] is None
    assert step['tool_call']['data']['arguments']=={'value':7}
    assert step['execution_result']['data']['success'] is False
    assert step['execution_result']['completion_received'] is False
    assert step['execution_result']['basis']=='client_reply_attempt'
    assert step['recorded_observations']['entries']==[]
    assert [r['method'] for r in step['source_event_refs']]==['item/tool/call','rpc/response']


def test_failure_retry_and_batch_code_remain_distinct_calls(recorder):
    episode=start(recorder)
    args={'code':'click(); type_text(); observe()'}
    call(recorder,'attempt1','started',arguments=args)
    call(recorder,'attempt1',result=state_result(),arguments=args,status='failed',error={'message':'失败'})
    call(recorder,'attempt2','started',arguments=args)
    call(recorder,'attempt2',text='返回',arguments=args)
    steps=view(recorder,episode)['steps']
    assert len(steps)==2 and steps[0]['step_id']!=steps[1]['step_id']
    assert steps[0]['execution_result']['reported_status']=='failed'
    assert steps[0]['tool_call']['data']['arguments']==args
    assert steps[1]['goal_verification']=='unknown'
    assert 'tool_failure_or_partial_result' in steps[0]['recorded_observations']['entries'][0]['reasons']
    assert steps[1]['recorded_observations']['entries']==[]


def test_missing_arguments_unknown_items_and_recording_gaps_are_visible(recorder):
    episode=start(recorder)
    item(recorder,{'id':'new','type':'futureTool','unknown':True})
    item(recorder,{'id':'missing','type':'mcpToolCall','tool':'js'},method='item/started')
    data=snapshot(recorder)
    data['session']['recordingDroppedEvents']=1
    result=build_step_view(data,episode)
    assert {'recording_incomplete','unassigned_evidence'} <= set(result['warnings'])
    assert result['unassigned_event_refs'][0]['item_id']=='new'
    assert 'call_arguments_missing' in result['steps'][0]['tool_call']['reasons']


def test_late_output_is_preserved_as_reference_but_does_not_replace_result(recorder):
    episode=start(recorder)
    call(recorder,'act','started');call(recorder,'act',text='原返回')
    call(recorder,'act',text='迟到的不同完成内容')
    step=view(recorder,episode)['steps'][0]
    assert step['execution_result']['data']['result']['content'][0]['text']=='原返回'
    assert 'late_item_events' in step['warnings']
    assert len(step['source_event_refs'])==3


def test_focus_context_and_user_answers_are_not_agent_observations(recorder):
    episode=start(recorder)
    call(recorder,'act','started');call(recorder,'act')
    item(recorder,{'id':'q','type':'dynamicToolCall','tool':'ask_ring_user','arguments':{},
        'success':True,'contentItems':[{'type':'inputText','text':'明天'}]})
    steps=view(recorder,episode)['steps']
    assert all(step['recorded_observations']['entries']==[] for step in steps)
    context=episode_detail(snapshot(recorder),episode)['episode']['context']
    assert context['app']=='微信'
    assert 0 <= context['time_context']['local_hour'] < 24
    assert 'utc_offset_minutes' in context['time_context']


def test_non_mcp_tools_are_visible_and_html_escapes_all_step_content(recorder):
    episode=start(recorder)
    command={'id':'cmd','type':'commandExecution','command':'<script>bad()</script>','status':'inProgress'}
    item(recorder,command,method='item/started')
    item(recorder,{**command,'status':'completed','aggregatedOutput':'<img src=x onerror=x>','exitCode':0})
    snapshot(recorder);export_html(recorder.root)
    page=(recorder.directory/f'episode-{episode}.html').read_text()
    assert '<script>bad()' not in page and '&lt;script&gt;bad()' in page
    assert '<img src=x' not in page and '&lt;img src=x' in page
    step=view(recorder,episode)['steps'][0]
    assert step['execution_result']['data']['exitCode']==0
    assert step['recorded_observations']['availability']=='not_identified'


def test_call_id_and_item_lifecycle_are_one_step_with_no_reply_double_count(recorder):
    episode=start(recorder)
    payload={'id':'dynamic','type':'dynamicToolCall','tool':'custom','arguments':{'n':1}}
    item(recorder,payload,method='item/started')
    recorder.rpc('incoming',{'id':8,'method':'item/tool/call','params':{
        'threadId':'thread','turnId':'t1','callId':'dynamic','tool':'custom','arguments':{'n':1}}})
    recorder.rpc('outgoing',{'id':8,'result':{'success':True,'contentItems':[{'type':'inputText','text':'完成'}]}})
    interim=view(recorder,episode)['steps']
    assert len(interim)==1 and interim[0]['execution_result']['basis']=='client_reply_attempt'
    item(recorder,{**payload,'status':'completed','success':True,'contentItems':[{'type':'inputText','text':'完成'}]})
    final=view(recorder,episode)['steps']
    assert len(final)==1 and final[0]['step_id']==interim[0]['step_id']
    assert final[0]['execution_result']['basis']=='item_completion'
    assert len(final[0]['source_event_refs'])==4


def test_start_arriving_after_completion_is_not_used_as_a_before_boundary(recorder):
    episode=start(recorder)
    call(recorder,'act',text='已返回')
    item(recorder,{'id':'r','type':'reasoning','summary':['动作之后的说明']})
    call(recorder,'act','started')
    step=view(recorder,episode)['steps'][0]
    assert step['started_at'] is None
    assert step['preceding_agent_messages']['availability']=='missing'
    assert 'late_item_events' in step['warnings']


def test_step_images_reuse_saved_attachments_and_never_embed_external_paths(recorder):
    episode=start(recorder)
    call(recorder,'observe','started')
    call(recorder,'observe',result={'content':[
        {'type':'image','mimeType':'image/png','data':base64.b64encode(b'returned-image').decode()},
        {'type':'resource_link','uri':'https://example.test/remote.png'},
        {'attachment':'../../outside.png','mime_type':'image/png'},
    ]})
    data=snapshot(recorder)
    returned=data['items'][0]['payload']['result']['content'][0]
    attachments=list((recorder.directory/'attachments').iterdir())
    export_html(recorder.root)
    page=(recorder.directory/f'episode-{episode}.html').read_text()
    assert f"src='{returned['attachment']}'" in page
    assert "src='../../outside.png'" not in page and "src='https://" not in page
    assert list((recorder.directory/'attachments').iterdir())==attachments
    step=view(recorder,episode)['steps'][0]
    assert step['execution_result']['data']['result']['content'][0]==returned
    assert step['recorded_observations']['entries']==[]  # An arbitrary image is not a screenshot.


def test_explicit_returned_state_blocks_exclude_docs_and_keep_exact_result_paths(recorder):
    episode=start(recorder)
    result=state_result()
    result['content'].insert(0,{'type':'text','text':'## API\ngetScreenshot(); getAXState();'})
    result['content'].append({'type':'text','text':'操作成功'})
    args={'code':'let app = await cua.getApp("Notes");'}
    call(recorder,'select','started',arguments=args)
    call(recorder,'select',arguments=args,result=result)
    step=view(recorder,episode)['steps'][0]
    observations=step['recorded_observations']['entries']
    assert len(observations)==1
    assert observations[0]['kind']=='accessibility_tree'
    assert observations[0]['result_path']==['result','content',1]
    assert observations[0]['content']==result['content'][1]
    assert observations[0]['position']=='within_call_unlocated'
    assert step['execution_result']['data']['result']==result


@pytest.mark.parametrize('code',[
    'await app.getScreenshot();',
    'await app.getAXStateAndScreenshot();',
])
def test_screenshot_requires_actual_returned_image_and_stays_in_its_call(recorder,code):
    episode=start(recorder)
    args={'code':code}
    call(recorder,'capture','started',arguments=args)
    assert view(recorder,episode)['steps'][0]['recorded_observations']['entries']==[]
    result=state_result()
    result['content']=[{'type':'image','mimeType':'image/png','data':base64.b64encode(b'screenshot').decode()}]
    call(recorder,'capture',arguments=args,result=result)
    call(recorder,'click','started');call(recorder,'click',text='点击已返回')
    steps=view(recorder,episode)['steps']
    observation=steps[0]['recorded_observations']['entries'][0]
    assert observation['kind']=='screenshot' and observation['source_call_id']=='capture'
    assert observation['content']==steps[0]['execution_result']['data']['result']['content'][0]
    assert (recorder.directory/observation['content']['attachment']).read_bytes()==b'screenshot'
    assert steps[1]['recorded_observations']['entries']==[]


@pytest.mark.parametrize('code',[
    'await app.click(1);',
    '// await app.getScreenshot();\nawait app.click(1);',
    'nodeRepl.write("await app.getScreenshot();");',
    'if (false) { await app.getScreenshot(); }',
])
def test_images_and_code_mentions_do_not_invent_screenshot_execution(recorder,code):
    episode=start(recorder)
    result=state_result()
    result['content']=[{'type':'image','mimeType':'image/png','data':base64.b64encode(b'arbitrary-image').decode()}]
    call(recorder,'call','started',arguments={'code':code})
    call(recorder,'call',arguments={'code':code},result=result)
    step=view(recorder,episode)['steps'][0]
    assert step['recorded_observations']['entries']==[]
    assert step['execution_result']['data']['result']['content'][0]['attachment']


@pytest.mark.parametrize('mode',['no_return','ordinary_text','no_surface','other_tool','docs','invalid_image'])
def test_unknown_outputs_and_unfulfilled_observation_calls_are_not_promoted(recorder,mode):
    episode=start(recorder)
    args={'code':'await app.getScreenshot();','title':'观察当前状态'}
    result=state_result()
    fields={}
    if mode=='no_return':result=None
    if mode=='ordinary_text':result=state_result('已执行截图')
    if mode=='no_surface':result.pop('_meta')
    if mode=='other_tool':fields={'server':'other_server','tool':'other_tool'}
    if mode=='docs':result=state_result('## Documentation\nWindow: "Notes", App: Notes.\n0 standard window Notes')
    if mode=='invalid_image':result['content']=[{'type':'image','mimeType':'image/png','data':'bad-base64'}]
    call(recorder,'call','started',arguments=args,**fields)
    call(recorder,'call',arguments=args,result=result,**fields)
    assert view(recorder,episode)['steps'][0]['recorded_observations']['entries']==[]
