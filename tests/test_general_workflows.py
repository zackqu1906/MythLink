"""General-task recording, grounded single-step extraction and downstream reuse."""
import copy
import json
import pytest

# Preserve legacy behavior tests behind an explicit test-only opt-in.
pytestmark = pytest.mark.usefixtures('legacy_workflow_pipeline')

from test_agent_recording import recorder, start, item, snapshot
from test_workflow_operation_compatibility import store, sample, decision
from proximic_ring.agent_workflow.recording.records import episode_detail, export_html
from proximic_ring.agent_workflow.extraction.workflow_evidence import prepare_evidence, validate_workflows
from proximic_ring.agent_workflow.extraction.workflow_input import prepare_model_input
from proximic_ring.agent_workflow.extraction.workflow_compaction import expand_model_packet
from proximic_ring.agent_workflow.extraction.workflow_artifacts import compact_workflows, workflow_steps
from proximic_ring.agent_workflow.extraction.workflow_store import WorkflowStore
from proximic_ring.agent_workflow.extraction.workflows import process_one
from proximic_ring.agent_workflow.operations.operation_ingest import sample_from_audit
from proximic_ring.agent_workflow.operations.operation_catalog import regroup, review_one
from proximic_ring.agent_workflow.operations.operation_embeddings import workflow_texts
from proximic_ring.agent_workflow.operations.operation_identity import application_conflict
from proximic_ring.agent_workflow.operations.operation_library import library
from proximic_ring.agent_workflow.operations.operation_revision_validation import validate_update
from proximic_ring.agent_workflow.recommendation.operation_recommendations import recommend
from proximic_ring.agent_workflow.recommendation.recommendation_service import request_recommendations
from proximic_ring.agent_workflow.execution.recommendation_execution import prepare_execution, dispatch_command


def finish(recorder, *, tools=False):
    episode=start(recorder, '用代码计算平方和并存入文件' if tools else '解释 TF-IDF 的原理与局限')
    if tools:
        item(recorder, dict(id='cmd',type='commandExecution',command='python compute.py',cwd='/tmp',
            status='completed',aggregatedOutput='338350',exitCode=0,durationMs=20))
    item(recorder, dict(id='reason',type='reasoning',summary=['返回的摘要']))
    item(recorder, dict(id='user',type='userMessage',content=[dict(type='inputText',text='用简明中文回答')]))
    item(recorder, dict(id='comment',type='agentMessage',phase='commentary',text='准备回答'))
    item(recorder, dict(id='final',type='agentMessage',phase='final_answer',text='词频乘以逆文档频率衡量词的区分度，难以理解同义改写。'))
    recorder.rpc('incoming',dict(method='turn/completed',params=dict(threadId='thread',turn=dict(id='t1',status='completed'))))
    return episode,prepare_evidence(snapshot(recorder),episode)


def answer(packet, *, tools=False):
    final=next(e['id'] for e in packet['evidence'] if isinstance(e['content'],dict) and e['content'].get('id')=='final')
    user=next(e['id'] for e in packet['evidence'] if isinstance(e['content'],dict) and e['content'].get('id')=='user')
    profile=dict(task_types=['question_answering'],output_kinds=['answer'],sources=[user,final])
    result=dict(format='agent-workflow-v6',task_profile=profile,
        workflows=[dict(name='解释信息检索方法',goal='解释 {{method}} 的原理与局限',task_profile=copy.deepcopy(profile),
            parameters=[dict(name='method',description='需要解释的信息检索方法',sources=[user])],
            steps=[dict(kind='response',instruction='用简明中文解释 {{method}} 的原理，并说明其局限；针对本次输入重新生成回答。',sources=[final])],
            completion=dict(check='已提供原理与局限，未声称执行工具或验证外部结果。',sources=[final]),
            conditions=[],fallbacks=[])],extraction_notes=[])
    if tools:
        call=next(e['id'] for e in packet['evidence'] if e['kind']=='call')
        result['workflows'][0]['steps'][0].update(kind='tool',sources=[call])
    return result


def test_response_only_has_real_message_source_and_no_fabricated_observation(recorder):
    episode,packet=finish(recorder);before=copy.deepcopy(packet)
    detail=episode_detail(snapshot(recorder),episode)
    assert detail['execution_profile']['mode']=='response_only'
    assert detail['execution_profile']['tool_call_count']==0
    assert detail['execution_profile']['response_count']==1
    assert detail['step_view']['steps']==[]
    value=validate_workflows(answer(packet),packet)
    flow=compact_workflows(value)['workflows'][0]
    assert len(flow['steps'])==1 and 'method' not in flow['steps'][0]
    assert flow['task_profile']==dict(task_types=['question_answering'],output_kinds=['answer'])
    assert 'sources' not in json.dumps(flow) and packet==before
    assert 'E1' not in workflow_steps(flow)
    model=expand_model_packet(prepare_model_input(packet).model_packet)
    assert model['execution_profile']['mode']=='response_only'
    assert not any(r['kind']=='call' for r in model['records'])


@pytest.mark.parametrize('fault',['reasoning','commentary','user_only','response_as_tool','tool_as_response',
    'no_kind','unknown_type','empty_output','duplicate_type','foreign_classification','missing_profile'])
def test_v6_rejects_invented_actions_and_bad_classification(recorder,fault):
    _,packet=finish(recorder,tools=True);value=answer(packet);w=value['workflows'][0]
    ids={e['content'].get('id'):e['id'] for e in packet['evidence'] if isinstance(e['content'],dict)}
    if fault in ('reasoning','commentary','user_only'):
        w['steps'][0]['sources']=[ids[dict(reasoning='reason',commentary='comment',user_only='user')[fault]]]
    elif fault=='response_as_tool':w['steps'][0]['kind']='tool'
    elif fault=='tool_as_response':w['steps'][0]['sources']=[next(e['id'] for e in packet['evidence'] if e['kind']=='call')]
    elif fault=='no_kind':del w['steps'][0]['kind']
    elif fault=='unknown_type':w['task_profile']['task_types']=['made_up']
    elif fault=='empty_output':w['task_profile']['output_kinds']=[]
    elif fault=='duplicate_type':w['task_profile']['task_types']*=2
    elif fault=='foreign_classification':value['task_profile']['sources']=['E999']
    else:del value['task_profile']
    with pytest.raises(ValueError):validate_workflows(value,packet)


def test_non_ui_tools_retain_types_results_and_fact_references(recorder):
    episode,packet=finish(recorder,tools=True)
    profile=packet['source']['execution_profile']
    assert profile['tool_type_counts']=={'commandExecution':1}
    assert 'task_types' not in profile  # execution language does not determine purpose
    assert profile['calls'][0]['event_refs'] and profile['calls'][0]['reported_status']=='completed'
    model=expand_model_packet(prepare_model_input(packet).model_packet)
    call=next(r for r in model['records'] if r['kind']=='call')
    assert call['tool_type']=='commandExecution' and call['content']['command']=='python compute.py'
    assert any(r['kind']=='result' and r['content']['data']['exitCode']==0 for r in model['records'])
    validate_workflows(answer(packet,tools=True),packet)
    item(recorder,dict(id='new',type='futureTool',data='unclassified'))
    profile=episode_detail(snapshot(recorder),episode)['execution_profile']
    assert profile['unknown_item_types']==['futureTool'] and 'unclassified_item_types' in profile['warnings']


def test_numeric_code_examples_are_not_treated_as_transient_ui_targets(recorder):
    _,packet=finish(recorder);value=answer(packet)
    value['workflows'][0]['steps'][0]['instruction']+=' 可用数组索引 0 解释 {{method}} 的示例。'
    validate_workflows(value,packet)
    from test_agent_workflows import finished
    from test_workflow_trace import lean_answer
    _,ui_packet=finished(recorder)
    ui=lean_answer(ui_packet);ui['format']='agent-workflow-v6'
    profile=dict(task_types=['app_operation'],output_kinds=['app_state'],sources=ui['workflows'][0]['steps'][0]['sources'])
    ui['task_profile']=profile;ui['workflows'][0]['task_profile']=profile
    for step in ui['workflows'][0]['steps']:step['kind']='tool'
    ui['workflows'][0]['steps'][0]['instruction']='click(15)'
    with pytest.raises(ValueError,match='控件数字索引'):validate_workflows(ui,ui_packet)


def test_background_output_record_page_and_source_sample_keep_classification(recorder):
    episode,packet=finish(recorder);journal=(recorder.directory/'events.jsonl').read_bytes()
    jobs=WorkflowStore(recorder.root)
    try:
        jid=jobs.enqueue(packet)
        assert process_one(jobs,None,model=lambda *a:(answer(packet),{}))
        assert jobs.jobs()[0]['status']=='completed'
        audit=json.loads((jobs.directory/(jid+'.audit.json')).read_text())
        value=sample_from_audit(audit)
        assert value['source']['execution_profile']['mode']=='response_only'
        assert value['source']['task_profile']['sources']
        assert value['flow']['task_profile']['task_types']==['question_answering']
        from proximic_ring.agent_workflow.extraction.workflow_prompt import PROMPT_VERSION
        assert audit['generation']['prompt_version']==PROMPT_VERSION
        export_html(recorder.root)
        page=(recorder.directory/('episode-'+episode+'.html')).read_text()
        assert '问答' in page and '0 次工具调用' in page
        saved=json.loads((recorder.directory/('episode-'+episode+'.profile.json')).read_text())
        assert saved['classification']['profile']==audit['task_profile']
        assert saved['execution']==packet['source']['execution_profile']
        assert '问答' in (jobs.directory/'library.html').read_text()
        assert (recorder.directory/'events.jsonl').read_bytes()==journal
    finally:jobs.close()


def test_one_step_reaches_recommendation_and_executes_with_new_parameter(recorder,store):
    _,packet=finish(recorder);flow=compact_workflows(answer(packet))['workflows'][0]
    store.ingest(sample('qa',flow));regroup(store)
    result=request_recommendations(store,{});item=result['items'][0]
    assert item['workflow']==flow
    assert '问答' in workflow_texts(flow)['goal']
    exposure=store.expose(result['request_id'],item['operation_id'])
    command=dispatch_command(store,prepare_execution(store,exposure,{'method':'BM25'}))
    assert 'BM25' in command['prompt'] and '{{method}}' not in command['prompt']
    assert '词频乘以逆文档频率衡量词的区分度' not in command['prompt']
    assert len(library(store)[0]['workflow']['steps'])==1


def test_category_does_not_auto_merge_distinct_goals(recorder,store):
    _,packet=finish(recorder);first=compact_workflows(answer(packet))['workflows'][0]
    store.ingest(sample('explain',first));regroup(store)
    second=copy.deepcopy(first);second['name']='比较信息检索方法';second['goal']='比较 {{method}} 的差异'
    second['steps'][0]['action']='比较 {{method}} 的优缺点，按对照表输出。'
    store.ingest(sample('compare',second));regroup(store)
    assert len(library(store))==1 and len(library(store)[0]['samples'])==1
    def review(p,*a,**k):
        assert 'task_profile' not in p['new_workflow']
        assert isinstance(p['new_workflow']['steps'][0],str)
        response=decision(p)
        for d in response['decisions']:d.update(relation='different',reason='解释单个方法与比较多个方法的目标不同')
        return response,{}
    assert review_one(store,None,model=review)
    assert len(library(store))==2


def test_task_profile_is_not_a_mutable_method_and_apps_do_not_block_semantic_review(recorder):
    _,packet=finish(recorder);flow=compact_workflows(answer(packet))['workflows'][0]
    other=copy.deepcopy(flow);other['task_profile']['task_types']=['writing']
    with pytest.raises(ValueError):
        validate_update(dict(outcome='candidate',reason='错误扩大分类',workflow=other,changes=[]),dict(current_workflow=flow))
    left=dict(flow,name='Safari 获取资料');right=dict(flow,name='备忘录获取资料')
    assert not application_conflict(left,right)
    del left['task_profile'];del right['task_profile']
    assert application_conflict(left,right)  # legacy behavior and cache text stay intact
