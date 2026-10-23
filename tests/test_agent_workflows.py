import copy
import io
import json
from dataclasses import replace

import pytest

# Preserve legacy behavior tests behind an explicit test-only opt-in.
pytestmark = pytest.mark.usefixtures('legacy_workflow_pipeline')

from test_agent_recording import recorder, start, snapshot, item
from test_agent_step_view import call, state_result
from proximic_ring.agent_workflow.extraction.workflow_evidence import prepare_evidence, validate_workflows
from proximic_ring.agent_workflow.extraction.workflow_store import WorkflowStore
from proximic_ring.agent_workflow.extraction.workflows import source_packet, process_one, scan, worker_lock
from proximic_ring.agent_workflow.extraction.workflow_model import extract
from proximic_ring.text_processing.model import LLMSettings


def finished(r):
    episode=start(r)
    call(r,'inspect','started'); call(r,'inspect',result=state_result())
    call(r,'act','started'); call(r,'act',text='调用返回，不是界面观察')
    item(r,{'id':'final','type':'agentMessage','text':'任务已处理'})
    r.rpc('incoming',{'method':'turn/completed','params':{'threadId':'thread','turn':{'id':'t1','status':'completed'}}})
    return episode,prepare_evidence(snapshot(r),episode)


def answer(packet):
    calls=[e for e in packet['evidence'] if e['kind']=='call']
    observation=next(e for e in packet['evidence'] if e['kind']=='observation')
    result=next(e for e in packet['evidence'] if e['kind']=='result' and e['step_id']==calls[-1]['step_id'])
    return dict(workflows=[dict(name='打开目标',intent='打开应用中的目标',parameters=[],preconditions=['应用可用'],
        steps=[dict(title='查看',action='读取界面',method='绑定应用并读取其界面',target='应用窗口',evidence_ids=[calls[0]['id']],
                    observations=[dict(summary='返回了窗口树',evidence_ids=[observation['id']])],expected_outcome='可定位目标',uncertainties=[]),
               dict(title='操作',action='选择目标',method='在当前界面定位目标并选择',target='目标控件',evidence_ids=[calls[1]['id']],observations=[],
                    expected_outcome='打开目标',uncertainties=['没有后续观察'])],
        recovery_notes=[],outcome=dict(summary='调用返回，目标未独立验证',evidence_ids=[result['id']]),limitations=['单次经验'])],extraction_notes=[])


def test_packets_preserve_missing_observation_and_real_refs_without_mutating_records(recorder):
    episode,packet=finished(recorder)
    before=(recorder.directory/'events.jsonl').read_bytes()
    assert source_packet(recorder.root,recorder.session_id,episode)==packet
    assert len([e for e in packet['evidence'] if e['kind']=='observation'])==1
    assert packet['policy']['images']=='metadata_only'
    assert packet['source']['episode']['goal_status']=='unknown'
    assert all(e['event_refs'] for e in packet['evidence'])
    validate_workflows(answer(packet),packet)
    assert before==(recorder.directory/'events.jsonl').read_bytes()


def test_late_diagnostic_timestamp_does_not_trigger_another_paid_extraction(recorder):
    episode,packet=finished(recorder)
    data=snapshot(recorder)
    data['episodes'][0]['updated_at']='2099-01-01T00:00:00+00:00'
    assert prepare_evidence(data,episode)['fingerprint']==packet['fingerprint']


@pytest.mark.parametrize('fault',['foreign','borrowed_observation','invented_observation','single_step','undefined_parameter'])
def test_reject_untraceable_or_malformed_workflows(recorder,fault):
    _,packet=finished(recorder); value=answer(packet); w=value['workflows'][0]
    if fault=='foreign': w['steps'][0]['evidence_ids']=['E999']
    elif fault=='borrowed_observation': w['steps'][1]['observations']=copy.deepcopy(w['steps'][0]['observations'])
    elif fault=='invented_observation': w['steps'][0]['observations'][0]['evidence_ids']=w['steps'][0]['evidence_ids']
    elif fault=='single_step': w['steps']=w['steps'][:1]
    else: w['steps'][0]['action']='打开 {{unknown}}'
    with pytest.raises(ValueError): validate_workflows(value,packet)


def test_queue_dedup_revisions_recovery_and_raw_source_immutable(recorder):
    _,packet=finished(recorder); journal=(recorder.directory/'events.jsonl').read_bytes()
    store=WorkflowStore(recorder.root)
    try:
        first=store.enqueue(packet)
        assert store.enqueue(packet)==first
        assert store.enqueue(packet,manual=True)==first
        assert process_one(store,None,model=lambda p,s:(answer(p),{'thinking_requested':'enabled'}))
        assert not process_one(store,None,model=lambda *a:pytest.fail('duplicate request'))
        assert store.jobs()[0]['status']=='completed'
        result=json.loads((store.directory/store.jobs()[0]['result_file']).read_text())
        assert result['status']=='draft' and result['goal_verification']=='unknown'
        assert result['validation']['semantics']=='not_verified'
        assert not result['source_changed_after_extraction']
        assert store.jobs()[0]['result_file']==first+'.audit.json'
        flow=json.loads((store.directory/result['workflow_file']).read_text())
        assert set(flow)=={'schema_version','workflows'}
        assert set(flow['workflows'][0]['steps'][0])=={'action','method','check'}
        assert flow['workflows'][0]['steps'][0]['check']=='可定位目标'
        assert 'evidence_ids' not in json.dumps(flow) and 'event_refs' not in json.dumps(flow)
        assert result['evidence_index'] and result['workflows'][0]['steps'][0]['evidence_ids']
        second=store.enqueue(packet,manual=True)
        assert first!=second
        assert store.next_job()['id']==second
        store.recover()
        assert store.jobs()[0]['status']=='failed'
        assert '远端请求结果未知' in store.jobs()[0]['error']
        assert store.next_job() is None
        assert journal==(recorder.directory/'events.jsonl').read_bytes()
    finally: store.close()


def test_failed_model_is_isolated_and_does_not_auto_retry(recorder):
    _,packet=finished(recorder); store=WorkflowStore(recorder.root)
    try:
        store.enqueue(packet)
        def fail(*args): raise RuntimeError('secret provider body')
        assert process_one(store,None,model=fail)
        assert store.jobs()[0]['status']=='failed'
        assert 'secret' not in store.jobs()[0]['error']
        assert store.jobs()[0]['result_file'] is None
        assert not process_one(store,None,model=fail)
    finally: store.close()


def test_rejected_final_json_is_auditable_but_not_a_draft(recorder):
    _,packet=finished(recorder);store=WorkflowStore(recorder.root)
    try:
        job=store.enqueue(packet)
        value=answer(packet); value['workflows'][0]['steps'][0]['evidence_ids']=['missing']
        process_one(store,None,model=lambda *a:(value,{}))
        attempts={j['id']:j for j in store.jobs()}
        assert attempts[job]['status']=='failed' and attempts[job]['result_file'] is None
        assert store.jobs()[0]['status']=='queued' and store.jobs()[0]['retry_number']==1
        rejected=json.loads((store.directory/(job+'.rejected.json')).read_text())
        assert rejected['status']=='rejected' and rejected['model_final_json']==value
        assert not (store.directory/(job+'.flow.json')).exists()
        assert not (store.directory/(job+'.audit.json')).exists()
    finally: store.close()


def test_auto_cutoff_skips_history_and_setup_sessions(recorder,monkeypatch):
    episode,packet=finished(recorder)
    # No sleeps: simulate a settled completed task in the scan's listing.
    from proximic_ring.agent_workflow.extraction import workflows
    task=dict(id=episode,session_id=recorder.session_id,goal='test',status='completed',
              created_at='2026-10-15T01:00:00+00:00',updated_at='2026-10-15T01:01:00+00:00')
    monkeypatch.setattr(workflows,'task_list',lambda root:[task])
    store=WorkflowStore(recorder.root)
    try:
        assert store.activation('2026-10-15T02:00:00+00:00')=='2026-10-15T02:00:00+00:00'
        assert store.activation('2026-10-16T00:00:00+00:00')=='2026-10-15T02:00:00+00:00'
        scan(store,'2026-10-15T02:00:00+00:00',set()); assert store.jobs()==[]
        cache=set(); scan(store,'2026-10-15T00:00:00+00:00',cache)
        scan(store,'2026-10-15T00:00:00+00:00',cache)
        assert len(store.jobs())==1
        with worker_lock(store):
            with pytest.raises(ValueError):
                with worker_lock(store): pass
    finally: store.close()


def test_independent_request_enables_thinking_and_discards_hidden_reasoning(recorder):
    _,packet=finished(recorder); requests=[]
    settings=LLMSettings(enabled=False,provider='volcengine',base_url='https://example.test/api/v3',model='text-model',api_key='secret')
    def fake(req,timeout):
        requests.append((req,timeout))
        return io.BytesIO(json.dumps(dict(status='completed',id='resp',thinking={'type':'enabled'},
            output=[{'type':'reasoning','summary':[{'text':'private reasoning'}]},
                    {'type':'message','role':'assistant','content':[{'type':'output_text','text':json.dumps(answer(packet))}]}],
            usage={'output_tokens':200,'output_tokens_details':{'reasoning_tokens':100}})).encode())
    result,meta=extract(packet,settings,urlopen=fake)
    body=json.loads(requests[0][0].data)
    assert body['thinking']=={'type':'enabled'} and body['store'] is False
    assert 'tools' not in body and 'previous_response_id' not in body
    assert requests[0][1]==180 and settings.enabled is False and settings.timeout_s==30
    assert meta['usage']['output_tokens_details']['reasoning_tokens']==100
    assert 'private reasoning' not in json.dumps((result,meta))
    with pytest.raises(ValueError,match='思考适配'):
        extract(packet,replace(settings,provider='local'),urlopen=lambda *a,**kw:pytest.fail('unsupported model called'))


@pytest.mark.parametrize('status',['incomplete','failed'])
def test_partial_responses_never_become_drafts(status):
    settings=LLMSettings(enabled=True,provider='volcengine',model='test',api_key='secret')
    with pytest.raises(ValueError,match='未完整结束'):
        extract({},settings,urlopen=lambda *a,**kw:io.BytesIO(json.dumps({'status':status}).encode()))


def test_workflow_html_escapes_generated_content_and_links_sources(recorder):
    _,packet=finished(recorder); store=WorkflowStore(recorder.root)
    try:
        value=answer(packet);value['workflows'][0]['name']='<script>alert(1)</script>'
        job=store.enqueue(packet);process_one(store,None,model=lambda *a:(value,{}))
        page=(store.directory/'library.html').read_text()
        assert '<script>' not in page and '&lt;script&gt;' in page
        assert '#event-' not in page and '#step-' not in page and 'evidence_ids' not in page
        audit=(store.directory/(job+'.audit.html')).read_text()
        assert '<script>' not in audit and '&lt;script&gt;' in audit
        assert '#event-' in audit and '#step-' in audit and '.input.json' in audit
    finally: store.close()


def test_library_uses_latest_successful_revision_per_task_and_keeps_legacy_evidence(recorder):
    from proximic_ring.agent_workflow.extraction.workflow_view import export_workflows
    _,packet=finished(recorder);store=WorkflowStore(recorder.root)
    try:
        first=store.enqueue(packet)
        process_one(store,None,model=lambda p,s:(answer(p),{}))
        old_audit=store.directory/(first+'.audit.json')
        legacy=store.directory/(first+'.workflow.json')
        old_audit.rename(legacy)
        store.finish(first,result_file=legacy.name)
        original=legacy.read_bytes()
        second=store.enqueue(packet,manual=True)
        process_one(store,None,model=lambda p,s:(answer(p),{}))
        third=store.enqueue(packet,manual=True)
        store.finish(third,error='模拟后续提取失败')
        export_workflows(store)
        index=json.loads((store.directory/'library.json').read_text())
        assert len(index['entries'])==1
        assert index['entries'][0]['job_id']==second
        assert index['entries'][0]['episode_id']==packet['source']['episode_id']
        assert legacy.read_bytes()==original
        assert (store.directory/(first+'.flow.json')).exists()
        page=(store.directory/'library.html').read_text()
        assert '模拟后续提取失败' in page
        visible=page.split("<details id='attempts'>")[0]
        assert visible.count('<article>')==1
        assert second+'.flow.txt' in visible and first+'.flow.txt' not in visible
    finally: store.close()


def test_compact_flow_preserves_parameterized_actions_and_conditions_without_audit_data(recorder):
    from proximic_ring.agent_workflow.extraction.workflow_artifacts import compact_workflows
    _,packet=finished(recorder);result=answer(packet)
    w=result['workflows'][0]
    w['parameters']=[dict(name='title',description='标题',example='私人标题',evidence_ids=['E1']),
                     dict(name='old_date',description='历史日期',example='旧日期',evidence_ids=['E1'])]
    w['steps'][1]['action']='写入 {{title}}'
    w['outcome']['summary']='历史任务日期为 {{old_date}}'
    w['recovery_notes']=[dict(condition='目标不可见',action='重新定位 {{title}}',evidence_ids=['E1'])]
    original=copy.deepcopy(result)
    flow=compact_workflows(result)['workflows'][0]
    assert flow['parameters']==[dict(name='title',description='标题')]
    assert flow['steps'][1]['action']=='写入 {{title}}'
    assert flow['fallbacks']==[dict(when='目标不可见',action='重新定位 {{title}}')]
    assert flow['conditions']==w['preconditions']
    assert '私人标题' not in json.dumps(flow,ensure_ascii=False)
    assert result==original


def test_text_artifact_is_derived_without_changing_json_or_audit(recorder):
    from proximic_ring.agent_workflow.extraction.workflow_artifacts import save_flow
    from proximic_ring.agent_workflow.extraction.workflow_artifacts import workflow_text
    _,packet=finished(recorder);result=answer(packet)
    directory=recorder.root/'workflows';directory.mkdir()
    filename,compact=save_flow(directory,'example',result)
    before=(directory/filename).read_bytes()
    text=(directory/'example.flow.txt').read_text()
    flow=compact['workflows'][0]
    assert text==workflow_text(flow)
    for step in flow['steps']:
        assert all(step[k] in text for k in ('action','method','check'))
    assert 'evidence_ids' not in text and 'observations' not in text
    save_flow(directory,'example',result)
    assert (directory/filename).read_bytes()==before


def test_different_tasks_are_not_merged_by_workflow_name():
    from proximic_ring.agent_workflow.extraction.workflow_artifacts import latest_successful_jobs
    jobs=[dict(id=str(i),session_id='session',episode_id=str(i),status='completed',result_file='file',created_at=str(i)) for i in range(2)]
    assert len(list(latest_successful_jobs(jobs)))==2


def test_worker_subprocess_keeps_jobs_queued_while_foreground_busy_and_resumes(recorder):
    import os
    import subprocess
    import sys
    import time
    from dataclasses import asdict
    from pathlib import Path
    _,packet=finished(recorder)
    store=WorkflowStore(recorder.root)
    store.enqueue(packet)
    env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'src')}
    # An in-process fixture cannot cross the subprocess boundary. Explicitly
    # enable only this isolated legacy worker; the real CLI stays disabled.
    code = ('from proximic_ring.agent_workflow import pipeline_policy; '
            'pipeline_policy.LEGACY_WORKFLOW_ENABLED=True; '
            'from proximic_ring.agent_workflow.extraction.workflows import main; main()')
    process=subprocess.Popen([sys.executable,'-c',code,
        '--root',str(recorder.root),'--serve','--since','2099-01-01T00:00:00+00:00'],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
    config=dict(type='configure',automatic=False,busy=True,settings=asdict(LLMSettings(provider='local')))
    def send(value): process.stdin.write(json.dumps(value)+'\n'); process.stdin.flush()
    def until(check):
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            if check(): return
            assert process.poll() is None, process.stderr.read()
            time.sleep(.05)
        pytest.fail('worker did not update queue')
    try:
        send(config)
        until(lambda:(store.directory/'status.json').exists())
        assert store.jobs()[0]['status']=='queued'
        send({**config,'busy':False})
        until(lambda:'火山方舟' in json.loads((store.directory/'status.json').read_text()).get('error',''))
        assert store.jobs()[0]['status']=='queued'
        # A missing setup must preserve the queue. After setup, the worker
        # resumes; a closed localhost port exercises failure without cloud calls.
        send({**config,'busy':False,'settings':asdict(LLMSettings(provider='volcengine',
            model='test',api_key='fixture',base_url='http://127.0.0.1:1'))})
        until(lambda:any(j['status']=='failed' for j in store.jobs()))
        assert any('网络失败' in j['error'] for j in store.jobs() if j['status']=='failed')
        process.stdin.close()
        assert process.wait(timeout=4)==0
    finally:
        if process.poll() is None: process.kill();process.wait()
        store.close()


def test_old_pending_version_is_replaced_only_by_explicit_retry(recorder):
    from proximic_ring.agent_workflow.extraction.workflow_prompt import PROMPT_VERSION
    _,packet=finished(recorder);store=WorkflowStore(recorder.root)
    try:
        old=store.enqueue(packet)
        store.db.execute("UPDATE jobs SET prompt_version='desktop-awm-2' WHERE id=?",(old,));store.db.commit()
        new=store.enqueue(packet,manual=True)
        assert new!=old and store.enqueue(packet,manual=True)==new
        jobs={j['id']:j for j in store.jobs()}
        assert jobs[old]['status']=='failed' and jobs[new]['prompt_version']==PROMPT_VERSION
        assert process_one(store,None,model=lambda p,s:(answer(p),{}))
        result=json.loads((store.directory/(new+'.audit.json')).read_text())
        assert result['schema_version']==2 and result['generation']['prompt_version']==PROMPT_VERSION
        text=(store.directory/(new+'.flow.txt')).read_text()
        assert '执行方法：绑定应用并读取其界面' in text
        page=(store.directory/'library.html').read_text()
        assert new+'.flow.txt' in page and '下载简洁 Workflow JSON' not in page
    finally: store.close()


def test_unparseable_final_text_is_saved_separately_without_reasoning(recorder):
    from proximic_ring.agent_workflow.extraction.workflow_model import ModelOutputError
    _,packet=finished(recorder);store=WorkflowStore(recorder.root)
    try:
        job=store.enqueue(packet)
        def bad_model(*args):
            raise ModelOutputError('模型未返回完整 JSON','incomplete final text',{'thinking_requested':'enabled'})
        assert process_one(store,None,model=bad_model)
        rejected=json.loads((store.directory/(job+'.rejected.json')).read_text())
        assert rejected['model_final_text']=='incomplete final text'
        assert rejected['generation']['thinking_requested']=='enabled'
        assert next(j for j in store.jobs() if j['id']==job)['status']=='failed'
        assert store.jobs()[0]['retry_number']==1
        assert not (store.directory/(job+'.flow.txt')).exists()
    finally: store.close()
