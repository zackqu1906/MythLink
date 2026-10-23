"""Historical pairwise pipeline regression; incremental prototype tests are in test_operation_prototypes.py."""
import copy
import json
from datetime import datetime,timezone,timedelta
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import pytest

# These are retained legacy ranking/feedback regressions, not production defaults.
pytestmark = pytest.mark.usefixtures('legacy_workflow_pipeline')
from proximic_ring.agent_workflow.operations.operation_matching import complete_linkage,pair_key,digest,candidates,app_tags
from proximic_ring.agent_workflow.operations.operation_store import OperationStore
from proximic_ring.agent_workflow.operations.operation_legacy import regroup,review_one,ingest_dataset,sample_from_audit
from proximic_ring.agent_workflow.recommendation.operation_recommendations import recommend,context_features,library
from proximic_ring.agent_workflow.operations.operation_view import export_operations
from proximic_ring.agent_workflow.operations.operation_group_view import group_filename

AT=datetime(2026,10,15,12,tzinfo=timezone.utc)


def fake_embeddings(versions):
    # These tests exercise catalog/review invariants, not model quality.
    out={}
    for vid,f in versions.items():
        app=app_tags(f)
        index=1 if 'com.apple.Music' in app else 2 if 'com.apple.Safari' in app else 0
        vector=[0.]*1024;vector[index]=1.
        out[vid]={'goal':vector,'steps':vector}
    return out


@pytest.fixture(autouse=True)
def no_model_download(monkeypatch):
    from proximic_ring.agent_workflow.operations import operation_legacy as operation_catalog
    monkeypatch.setattr(operation_catalog,'ensure_embeddings',lambda directory,versions,**kw:(fake_embeddings(versions),{}))


def flow(name='备忘录新建笔记',method='点击 New Note，将标题和正文粘贴到正文编辑区'):
    return dict(name=name,goal=name+'并确认界面内容',parameters=[],steps=[dict(action=name,method=method,check='核对完成')],conditions=[],fallbacks=[])


def sample(i='a',f=None,kind='test',context=None,days=0):
    return dict(id=i,kind=kind,flow=f or flow(),source=dict(session_id='s',episode_id=i,episode_source='test',outcome='completed'),
                occurred_at=(AT-timedelta(days=days)).isoformat(),context=context or {})


@pytest.fixture
def store(tmp_path):
    s=OperationStore(tmp_path);yield s;s.close()


def model_same(packet,settings,**kwargs):
    out=[]
    for p in packet['pairs']:
        left=[v['name'] for v in p['left']['parameters']];right=[v['name'] for v in p['right']['parameters']]
        mapped={p:p for p in left if p in right}
        out.append(dict(pair_id=p['pair_id'],relation='same',parameter_map=mapped,
                        variant_parameters=dict(left=[p for p in left if p not in mapped],right=[p for p in right if p not in mapped]),reason='相同完整用户意图，不同路径'))
    return {'decisions':out},{'thinking_requested':'enabled'}


def two_variants(store):
    a=sample();b=sample('b',flow(method='点击 New Note 后使用 paste 将完整文本写入'))
    store.ingest(a);store.ingest(b);regroup(store);return a,b


def test_dense_cosine_uses_normalized_cached_vectors():
    versions={'a':flow(),'b':flow(),'c':flow('Music 随机播放')}
    pairs=candidates(versions,fake_embeddings(versions))
    assert pairs[0]['score']==pytest.approx(1)
    assert pairs[1]['score']==pytest.approx(0)


def test_complete_linkage_never_transitively_merges_unverified_pair():
    mapping={'a':'1','b':'2','c':'3'};ab=pair_key('1','2');bc=pair_key('2','3')
    groups=complete_linkage(mapping,{ab,bc},{ab:.6,bc:.9})
    assert {'b','c'} in groups and {'a'} in groups


def test_target_application_blocks_generic_shared_actions():
    versions={'a':flow(),'b':flow('Music 随机播放')}
    pair=candidates(versions,fake_embeddings(versions))[0]
    assert pair['decision']=='rejected' and '应用' in pair['reason']


def test_candidate_similarity_cannot_merge_without_review(store):
    two_variants(store)
    assert len(library(store))==2
    assert len([p for p in store.rows('pairs') if p['status']=='pending'])==1


def test_parameter_count_differences_reach_semantic_review(store):
    a,b=two_variants(store)
    b['flow']['parameters']=[dict(name='app_id',description='路径应用标识')];store.ingest(b);regroup(store)
    assert review_one(store,None,model=model_same)
    assert len(library(store))==1
    assert any(v['flow']['parameters'] for v in library(store)[0]['variants'])


def test_stable_identity_aliases_and_original_versions(store):
    a,b=two_variants(store);old={g['operation_id'] for g in library(store)}
    result=recommend(store,scope='test',at=AT);imp=store.remember(result,'test')
    oid=result['items'][0]['operation_id'];store.feedback(store.expose(imp,oid),oid,'accepted')
    assert review_one(store,None,model=model_same)
    merged=library(store);assert len(merged)==1 and len(merged[0]['variants'])==2
    current=merged[0]['operation_id'];assert current in old
    assert {store.canonical(o) for o in old}=={current}
    for _ in range(2):store.ingest(a);store.ingest(b);regroup(store)
    assert library(store)[0]['operation_id']==current
    assert len(store.rows('samples'))==2 and len(store.versions())==2
    result=recommend(store,scope='test')
    assert result['items'][0]['feedback']['accepted']==1


def test_revision_does_not_inflate_usage(store):
    a=sample(kind='user');store.ingest(a);regroup(store);oid=library(store)[0]['operation_id']
    a['flow']['steps'][0]['method']+='，等窗口就绪';store.ingest(a);regroup(store)
    assert len(store.versions())==2 and library(store)[0]['operation_id']==oid
    assert recommend(store,at=AT)['items'][0]['usage_count']==1


def test_multiple_flows_from_same_episode_count_one_operation_use(store):
    a=sample(kind='user');b=copy.deepcopy(a);b['id']='b'
    store.ingest(a);store.ingest(b);regroup(store)
    assert recommend(store,at=AT)['items'][0]['usage_count']==1


def test_test_data_and_feedback_never_become_real_habits(store):
    store.ingest(sample());regroup(store)
    baseline=recommend(store,scope='user',at=AT)
    test=recommend(store,scope='test',at=AT);imp=store.remember(test,'test')
    oid=test['items'][0]['operation_id'];store.feedback(store.expose(imp,oid),oid,'accepted')
    actual=recommend(store,scope='user',at=AT)
    assert actual==baseline and actual['abstained'] and actual['items'][0]['usage_count']==0
    with pytest.raises(ValueError):store.ingest(sample(kind='user'))


def test_ignored_feedback_does_not_penalize_and_duplicate_is_idempotent(store):
    store.ingest(sample(kind='user'));regroup(store)
    before=recommend(store);imp=store.remember(before,'user');oid=before['items'][0]['operation_id']
    imp=store.expose(imp,oid)
    store.feedback(imp,oid,'ignored');store.feedback(imp,oid,'ignored')
    assert len(store.rows('recommendation_feedback'))==1
    assert recommend(store)['items'][0]['components']==before['items'][0]['components']
    with pytest.raises(ValueError):store.feedback(imp,oid,'rejected')
    with pytest.raises(ValueError):store.feedback(imp,'unknown','accepted')


def test_explicit_preference_is_recorded_without_changing_rank_or_usage(store):
    a=sample('a',kind='user');b=sample('b',flow('Music 随机播放'),kind='user')
    store.ingest(a);store.ingest(b);regroup(store)
    result=recommend(store);imp=store.remember(result,'user');oid=result['items'][1]['operation_id']
    store.feedback(store.expose(imp,oid),oid,'accepted')
    after=recommend(store)
    assert [i['operation_id'] for i in after['items']]==[i['operation_id'] for i in result['items']]
    assert [i['score'] for i in after['items']]==pytest.approx([i['score'] for i in result['items']],abs=1e-6)
    assert [v['usage_count'] for v in after['items']]==[1,1]


def test_decay_and_future_data_excluded(store):
    store.ingest(sample('a',kind='user',days=30));store.ingest(sample('b',kind='user',days=-1));regroup(store)
    item=recommend(store,at=AT)['items'][0]
    assert item['usage_count']==1 and item['decayed_usage']==pytest.approx(.5)


def test_missing_context_is_not_inferred_from_target_and_stale_focus_expires(store):
    store.ingest(sample(kind='user'));regroup(store)
    result=recommend(store,at=AT);assert result['context']['app'] is None
    stale=dict(before_agent_focus={'bundle':'com.apple.Notes','captured_at':(AT-timedelta(seconds=121)).isoformat()})
    assert context_features(stale,AT)['app'] is None
    stale['before_agent_focus']['captured_at']=AT.isoformat()
    assert context_features(stale,AT)['app']=='com.apple.Notes'


def test_time_context_and_missing_parameters_are_reported(store):
    f=flow();f['parameters']=[dict(name='title',description='标题')]
    store.ingest(sample(f=f,kind='user',context=dict(time_context={'local_hour':8,'weekday':4})));regroup(store)
    result=recommend(store,dict(time_context={'local_hour':8,'weekday':4}),at=AT)
    item=result['items'][0];assert item['known_context_weight']==1
    assert item['variants'][0]['missing_parameters']==['title']
    assert recommend(store,at=AT,available_parameters={'title':'测试'})['items'][0]['variants'][0]['parameters_ready']


@pytest.mark.parametrize('bad',['coverage','mapping','unknown'])
def test_malformed_model_response_rejects_whole_batch_no_automatic_retry(store,bad):
    two_variants(store);calls=[]
    def malformed(packet,*args,**kwargs):
        calls.append(1);result,generation=model_same(packet,None)
        if bad=='coverage':result['decisions']=[]
        elif bad=='mapping':result['decisions'][0]['parameter_map']={'fabricated':'fake'}
        else:result['decisions'][0]['pair_id']='unknown'
        return result,generation
    assert review_one(store,None,model=malformed)
    assert len(library(store))==2
    assert store.rows('review_jobs')[0]['status']=='failed'
    assert not review_one(store,None,model=malformed) and len(calls)==1
    assert review_one(store,None,model=model_same,retry=True) and len(library(store))==1


def test_review_failure_does_not_surface_credentials(store):
    two_variants(store)
    def failed(*args,**kwargs):raise RuntimeError('secret credential')
    review_one(store,None,model=failed)
    assert 'secret' not in json.dumps(store.rows('review_jobs'))


def test_different_goals_review_keeps_distinct_even_with_shared_prefix(store):
    a,b=two_variants(store)
    def different(packet,*args,**kwargs):
        data,g=model_same(packet,None);data['decisions'][0]['relation']='different';return data,g
    review_one(store,None,model=different)
    assert len(library(store))==2


def test_dataset_integrity_and_repeat_import(tmp_path,store):
    dataset=Path(__file__).resolve().parents[1]/'experiments/workflow_recommendations/dataset'
    assert ingest_dataset(store,dataset)==6
    assert ingest_dataset(store,dataset)==0
    manifest=json.loads((dataset/'manifest.json').read_text());assert len(manifest['trials'])==26
    copy_path=tmp_path/'bad';copy_path.mkdir();(copy_path/'samples').mkdir()
    value=sample();file=copy_path/'samples/a.json';file.write_text(json.dumps(value))
    (copy_path/'manifest.json').write_text(json.dumps({'samples':[{'file':'samples/a.json','sha256':'bad'}]}))
    with pytest.raises(ValueError,match='校验'):ingest_dataset(store,copy_path)


def test_report_escapes_flow_content_and_preserves_full_steps(store):
    f=flow('<script>alert(1)</script>');store.ingest(sample(f=f));regroup(store)
    page=export_operations(store).read_text()
    assert '<script>' not in page and '&lt;script&gt;' in page
    detail=(store.directory/group_filename(library(store)[0]['operation_id'])).read_text()
    assert '<script>' not in detail and '&lt;script&gt;' in detail
    assert '归并依据' in page and '原始场景' in detail
    assert f['steps'][0]['method'] in detail


@pytest.mark.parametrize('kind,has_demos',[('user',False),('unknown',False),('test',True)])
def test_report_only_generates_fixture_demos_for_test_samples(store,kind,has_demos):
    store.ingest(sample(kind=kind));regroup(store)
    page=export_operations(store).read_text()
    catalog=json.loads((store.directory/'catalog.json').read_text())
    assert bool(catalog['demos']) is has_demos
    assert ('固定测试场景演示' in page) is has_demos
    assert '历史测试数据集' not in page
    for table in ('recommendation_requests','recommendation_exposures','recommendation_feedback'):
        assert not store.rows(table)


def test_http_feedback_and_scope_security(store):
    from proximic_ring.agent_workflow.recording.records_server import RecordsServer
    store.ingest(sample());regroup(store);export_operations(store)
    server=RecordsServer(store.root,on_automatic=lambda _:None)
    def post(route,body,origin=None):
        request=Request(server.origin+server.prefix+'api/operations/'+route,data=json.dumps(body).encode(),headers={
            'Origin':origin or server.origin,'Content-Type':'application/json'})
        with urlopen(request,timeout=3) as response:return json.load(response)
    try:
        result=post('recommend',{'scope':'test','context':{'app':'Notes'}})
        assert result['request_id'] and result['context']['app']=='com.apple.Notes'
        assert not store.rows('recommendation_exposures')
        exposure=post('exposure',dict(request_id=result['request_id'],operation_id=result['items'][0]['operation_id']))
        body=dict(exposure_id=exposure['exposure_id'],operation_id=result['items'][0]['operation_id'],kind='accepted')
        post('feedback',body);post('feedback',body);assert len(store.rows('recommendation_feedback'))==1
        with pytest.raises(HTTPError) as exc:post('recommend',{'scope':'user','context':{'app':'Notes'}})
        assert exc.value.code==400
        with pytest.raises(HTTPError) as exc:post('feedback',body,origin='https://external.invalid')
        assert exc.value.code==403
        with urlopen(server.origin+server.prefix+'operations/index.html') as r:page=r.read().decode()
        assert 'operations.js' in page and "script-src 'self'" in page
    finally:server.close();server.thread.join(2)


def test_local_recommendation_never_calls_model(store,monkeypatch):
    from proximic_ring.agent_workflow.extraction import workflow_model
    monkeypatch.setattr(workflow_model,'extract',lambda *a,**k:pytest.fail('local ranking called model'))
    store.ingest(sample());regroup(store);recommend(store);export_operations(store)


def test_two_review_consumers_do_not_call_model_twice(store):
    two_variants(store)
    def first(packet,*args,**kwargs):
        second=OperationStore(store.root)
        try:assert not review_one(second,None,model=lambda *a,**k:pytest.fail('duplicate network request'))
        finally:second.close()
        return model_same(packet,None)
    assert review_one(store,None,model=first)
    assert len(store.rows('review_jobs'))==1


def test_audit_source_kind_unknown_not_assumed_real():
    path=next((Path(__file__).resolve().parents[1]/'experiments/workflow_recommendations/dataset/extractions').glob('*.audit.json'))
    result=json.loads(path.read_text())
    result['source']['episode']['source']='manual'
    assert sample_from_audit(result)['kind']=='unknown'
    result['source']['episode']['source']='ring_voice'
    assert sample_from_audit(result)['kind']=='user'
    result['source']['episode']['source']='workflow_trial'
    assert sample_from_audit(result)['kind']=='test'


def test_operation_toolbar_relative_links():
    from proximic_ring.agent_workflow.recording.record_controls import workflow_toolbar
    from urllib.parse import urljoin
    for page,library_href in [('index.html','workflows/library.html'),('sessions/a/index.html','../../workflows/library.html'),('workflows/library.html','library.html')]:
        value=workflow_toolbar(library_href)
        import re
        targets=re.findall("href='([^']+)'",value)
        assert len(targets)==1
        assert urljoin('http://local/root/'+page,targets[0])=='http://local/root/overview.html'
