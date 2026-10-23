"""场景分数、曝光边界与未来反馈学习接口；全部用隔离的合成数据。"""
import copy
import json
from datetime import datetime, timezone, timedelta
import pytest

# Preserve legacy behavior tests behind an explicit test-only opt-in.
pytestmark = pytest.mark.usefixtures('legacy_workflow_pipeline')
from proximic_ring.agent_workflow.operations.operation_store import OperationStore
from proximic_ring.agent_workflow.recommendation.operation_recommendations import recommend
from proximic_ring.agent_workflow.recommendation.recommendation_context import invocation_context, context_features
from proximic_ring.agent_workflow.recommendation.recommendation_feedback import training_examples
from proximic_ring.agent_workflow.recommendation.recommendation_service import request_recommendations, selection

AT=datetime.now(timezone.utc).replace(microsecond=0)


def context(app='com.apple.Safari',hour=10,weekday=1,at=AT):
    return dict(before_agent_focus=dict(bundle=app,captured_at=at.isoformat()),
                time_context=dict(local_hour=hour,weekday=weekday))


def flow(name):
    return dict(name=name,goal=name,parameters=[],conditions=[],fallbacks=[],
                steps=[dict(action=name,method='执行并核验',check='结果已确认')])


@pytest.fixture
def store(tmp_path):
    s=OperationStore(tmp_path);yield s;s.close()


def add(store,oid,n,ctx=None,*,scope='user',at=AT,episode=None):
    sid=f'{oid}-{n}'
    store.ingest(dict(id=sid,kind=scope,source=dict(session_id='s',episode_id=episode or sid),
                     occurred_at=at.isoformat(),context=ctx or {},flow=flow(oid)))
    return sid


def assign(store,members):
    store.assign([set(group) for group in members])


def test_context_can_outweigh_global_frequency_without_target_app_hint(store):
    a=[add(store,'Notes',n,context('com.apple.Terminal')) for n in range(12)]
    b=[add(store,'Music',n,context()) for n in range(4)]
    assign(store,[a,b])
    assert recommend(store,at=AT)['items'][0]['name']=='Notes'
    result=recommend(store,context(),at=AT)
    assert result['items'][0]['name']=='Music'
    assert sum(i['score'] for i in result['items'])==pytest.approx(1)
    assert result['items'][0]['statistics']['levels'][0]['matched_mass']==4
    assert result['items'][0]['statistics']['levels'][0]['score']==pytest.approx((4+3*(4.5/17))/7)


def test_unknown_fields_backoff_and_sequence_is_not_a_feature(store):
    assign(store,[[add(store,'a',1)],[add(store,'b',1)]])
    base=recommend(store,at=AT)
    with_sequence=recommend(store,dict(last_operation='b',sequence=['a','b']),at=AT)
    assert base==with_sequence
    assert 'last_operation' not in json.dumps(with_sequence)
    assert all(not i['statistics']['levels'] for i in base['items'])
    assert all(i['score']==.5 for i in base['items'])


def test_test_samples_do_not_fill_real_context_counts(store):
    assign(store,[[add(store,'a',1,context(),scope='test')],[add(store,'b',1,context(),scope='test')]])
    result=recommend(store,context(),scope='user',at=AT)
    assert result['abstained'] and result['evidence']['evidence_mass']==0
    assert all(i['score']==.5 for i in result['items'])
    with pytest.raises(ValueError):recommend(store,dict(source='manual_demo'),scope='user')


def test_one_task_has_one_total_mass_even_with_multiple_group_workflows(store):
    assign(store,[[add(store,'a',1,context(),episode='shared')],[add(store,'b',1,context(),episode='shared')]])
    result=recommend(store,context(),at=AT)
    assert result['evidence']['evidence_mass']==1
    assert [i['decayed_usage'] for i in result['items']]==[.5,.5]


def test_future_records_are_not_counted_as_historical_tasks(store):
    assign(store,[[add(store,'a',1)],[add(store,'b',1,at=AT+timedelta(days=1))]])
    result=recommend(store,at=AT)
    assert result['evidence']['tasks']==1 and result['evidence']['evidence_mass']==1


def test_invocation_context_is_frozen_and_historical_age_is_anchored_to_invocation():
    focus=dict(bundle='com.apple.Safari',captured_at=AT.isoformat())
    frozen=invocation_context(focus,trigger='agent_show',at=AT)
    focus['bundle']='com.apple.Notes'
    later=AT+timedelta(minutes=5)
    assert context_features(frozen,later,historical=True)['app']=='com.apple.Safari'
    assert context_features(frozen,later)['app'] is None
    assert 'invocation_stale' in context_features(frozen,later)['missing']
    future=copy.deepcopy(frozen);future['invoked_at']=(later+timedelta(hours=1)).isoformat()
    assert context_features(future,later)['app'] is None


def test_request_is_not_exposure_and_all_candidates_are_ranked_before_top_k(store):
    assign(store,[[add(store,str(n),1)] for n in range(4)])
    result=request_recommendations(store,limit=2,trigger='gesture')
    assert len(result['items'])==2 and len(result['candidate_set'])==4
    assert len(store.rows('recommendation_requests'))==1
    assert not store.rows('recommendation_exposures')
    oid=result['items'][0]['operation_id']
    with pytest.raises(ValueError):store.feedback(result['request_id'],oid,'accepted')
    hidden=result['candidate_set'][-1]['operation_id']
    with pytest.raises(ValueError):store.expose(result['request_id'],hidden)
    with pytest.raises(ValueError):store.expose(result['request_id'],oid,method='cli_confirmed')
    exposure=store.expose(result['request_id'],oid)
    assert store.expose(result['request_id'],oid)==exposure
    row=store.rows('recommendation_exposures')[0]
    assert row['position']==1 and row['operation_id']==oid
    store.feedback(exposure,oid,'accepted');store.feedback(exposure,oid,'accepted')
    assert len(store.rows('recommendation_feedback'))==1
    with pytest.raises(ValueError):store.feedback(exposure,oid,'rejected')
    assert selection(store,exposure)['workflow']==result['items'][0]['workflow']


def test_default_four_positions_and_selected_workflow_survive_library_change(store):
    members=[[add(store,str(n),1)] for n in range(6)]
    assign(store,members)
    result=request_recommendations(store,trigger='gesture')
    assert len(result['items'])==4 and len(result['candidate_set'])==6
    assert [i['position'] for i in result['items']]==[1,2,3,4]
    item=result['items'][0]
    exposure=store.expose(result['request_id'],item['operation_id'])
    sample=next(s for s in store.rows('samples') if s['operation_id']==item['operation_id'])
    store.ingest(dict(id=sample['id'],kind='user',source=json.loads(sample['source']),
                     occurred_at=sample['occurred_at'],flow=flow('new content')))
    assert selection(store,exposure)['workflow']==item['workflow']
    assert selection(store,exposure)['position']==1


def test_feedback_cutoff_compares_instants_across_timezones(store):
    from proximic_ring.agent_workflow.recommendation.recommendation_feedback import feedback_summary
    assign(store,[[add(store,'a',1)]])
    result=request_recommendations(store)
    oid=result['items'][0]['operation_id'];exposure=store.expose(result['request_id'],oid)
    store.feedback(exposure,oid,'accepted')
    local=(AT-timedelta(seconds=1)).astimezone(timezone(timedelta(hours=8))).isoformat()
    with store.db:
        store.db.execute('UPDATE recommendation_exposures SET at=?',(local,))
        store.db.execute('UPDATE recommendation_feedback SET at=?',(local,))
    assert feedback_summary(store,'user',at=AT)[oid]['accepted']==1
    assert feedback_summary(store,'user',at=AT)[oid]['exposures']==1


def test_feedback_keeps_exposure_features_and_does_not_change_score(store):
    assign(store,[[add(store,'a',1,context())],[add(store,'b',1,context())]])
    before=recommend(store,context(),at=AT)
    request=store.remember(before,'user')
    first,second=before['items']
    exposure=store.expose(request,first['operation_id'])
    ignored=store.expose(request,second['operation_id'])
    store.feedback(exposure,first['operation_id'],'rejected')
    store.feedback(exposure,first['operation_id'],'failed')
    store.feedback(ignored,second['operation_id'],'ignored')
    after=recommend(store,context(),at=AT)
    assert [i['score'] for i in before['items']]==[i['score'] for i in after['items']]
    examples=list(training_examples(store))
    assert len(examples)==1 and examples[0]['reward']==0
    assert examples[0]['features']==first['features'] and examples[0]['context']==before['context']
    assert examples[0]['action_probability'] is None
    assert examples[0]['position']==1
    assert not list(training_examples(store,scope='test'))


@pytest.mark.parametrize('limit',[0,21,True,'3',None])
def test_invalid_display_limit_is_rejected(store,limit):
    with pytest.raises(ValueError):request_recommendations(store,limit=limit)


def test_web_exposure_requires_foreground_visibility_and_dwell(tmp_path):
    import os,shutil,subprocess
    from pathlib import Path
    from proximic_ring.agent_workflow.recommendation.recommendation_view import OPERATIONS_JS
    node=os.environ.get('PROXIMIC_TEST_NODE') or shutil.which('node')
    if not node:pytest.skip('set PROXIMIC_TEST_NODE to run the synthetic DOM exposure test')
    script=tmp_path/'operations.js';script.write_text(OPERATIONS_JS)
    subprocess.run([node,str(Path(__file__).with_name('recommendation_visibility.cjs')),str(script)],check=True,timeout=10)
