"""Reviewed full-group consolidation, immutable provenance and parameter contract."""
import json

import pytest

# Preserve legacy behavior tests behind an explicit test-only opt-in.
pytestmark = pytest.mark.usefixtures('legacy_workflow_pipeline')
from test_operation_prototypes import store, sample
from test_operation_generalization import nearby, unified_candidate as candidate
from proximic_ring.agent_workflow.operations import operation_catalog as catalog
from proximic_ring.agent_workflow.operations.operation_store import encoded
from proximic_ring.agent_workflow.operations.operation_prototypes import heads,revision,seed,prepare
from proximic_ring.agent_workflow.operations.operation_merge import merge_reviewed,membership_token
from proximic_ring.agent_workflow.operations.operation_library import library
from proximic_ring.agent_workflow.operations.operation_view import export_operations
from proximic_ring.agent_workflow.parameters.parameter_history import save_record,current_records
from proximic_ring.agent_workflow.recommendation.recommendation_candidates import parameter_candidates
from proximic_ring.agent_workflow.recommendation.recommendation_catalog import catalog_state
from proximic_ring.agent_workflow.recommendation.operation_recommendations import recommend
from proximic_ring.agent_workflow.recommendation.recommendation_service import request_recommendations
from proximic_ring.agent_workflow.execution.recommendation_execution import prepare_execution,dispatch_command


def setup_merge(store):
    for sid,kind in [('restaurant','food'),('school','school'),('sports','sports')]:
        value=sample(sid,nearby(kind));value['source']['source_file']='../workflows/'+sid+'.audit.json'
        store.ingest(value)
        with store.db:seed(store,next(s for s in store.rows('samples') if s['id']==sid))
    from proximic_ring.agent_workflow.operations.operation_prototypes import recheck
    from test_operation_prototypes import same
    recheck(store,'sports')
    catalog.review_one(store,None,model=same)
    catalog.review_one(store,None,model=lambda p,*a,**kw:(candidate(p),{}))
    before=heads(store)
    rid=next(r['id'] for r in store.rows('operation_revisions') if r['status']=='candidate')
    rev=revision(store,rid)
    # Separate Notes operation must survive unchanged.
    store.ingest(sample('notes'))
    with store.db:seed(store,next(s for s in store.rows('samples') if s['id']=='notes'))
    branches={};moves={}
    with store.db:
        for s in store.rows('samples'):
            if s['operation_id'] not in before:continue
            src=json.loads(s['source']);src['source_file']='../workflows/'+s['id']+'.audit.json'
            s['source']=encoded(src)
            store.db.execute('UPDATE samples SET source=? WHERE id=?',(s['source'],s['id']))
            flow=store.versions()[s['version_id']]
            s['active_revision']=before[s['operation_id']]['id']
            params=[dict(name=p['name'],status='missing' if p['name']=='school_type_filter' else 'observed',
                value='' if p['name']=='school_type_filter' else '5' if p['name']=='top_n' else p['name']+' value',
                evidence=[] if p['name']=='school_type_filter' else [{'quote':'original fixture','source':{'event_refs':['event']}}],reason='fixture') for p in flow['parameters']]
            save_record(store,s,'old-'+s['id'],params,{})
            vid=s['version_id']
            branches[vid]=dict(condition=s['id'],steps=[1,2],verification='分支核验',parameter_notes='角色保留，缺失值留空')
            moves[vid]={target:dict(source=name) for name,targets in rev['generation']['parameter_bindings'][vid].items() for target in targets}
            if 'top_n' in rev['generation']['parameter_bindings'][vid]:
                moves[vid]['requirements'].update(prefix='按评分取前',suffix='项')
    return dict(revision_id=rid,expected_heads={o:r['id'] for o,r in before.items()},membership_token=membership_token(store,before),
        review=dict(authorization='fixture explicit merge request',execution_verified=False,branches=branches),parameter_moves=moves)


def immutable(store):
    return {table:[tuple(r) for r in store.db.execute('SELECT '+fields+' FROM '+table+' ORDER BY rowid')]
        for table,fields in [('versions','*'),('sample_versions','*'),
        ('operation_revisions','id,operation_id,parent_id,flow,sources,generation'),
        ('operation_parameter_history','*'),('recommendation_executions','*'),('recommendation_feedback','*')]}


def test_actual_merge_has_two_groups_preserves_originals_and_reindexes_statistics(store):
    plan=setup_merge(store);original=immutable(store);token=catalog_state(store)['token'];old_heads=heads(store)
    result=merge_reviewed(store,plan);groups=library(store);merged=next(g for g in groups if len(g['samples'])==3)
    assert len(groups)==2 and len(heads(store))==2
    assert len(merged['variants'])==3 and len(merged['merged_operation_ids'])==2
    assert merged['active_revision']['id']==plan['revision_id']
    assert merged['active_revision']['validation']['execution_verified'] is False
    for old in plan['expected_heads']:assert store.canonical(old)==merged['operation_id']
    assert len(merged['revision_history'])==4
    for table,rows in original.items():assert immutable(store)[table][:len(rows)]==rows
    stats=recommend(store,scope='test')
    assert sorted(i['usage_count'] for i in stats['items'])==[1,3]
    assert stats['evidence']['tasks']==4
    assert catalog_state(store)['token']!=token and catalog_state(store)['members']==4
    assert sum(1 for r in store.rows('operation_membership_events') if r['revision_id']==plan['revision_id'])==3
    assert merge_reviewed(store,plan)['id']==result['id']
    assert len(store.rows('operation_merges'))==1
    prepare(store)  # reopening/sync never recreates retired groups
    assert len(heads(store))==2
    for oid,rev in old_heads.items():
        if oid not in plan['expected_heads']:assert heads(store)[oid]==rev
    export_operations(store)
    text=(store.directory/'index.html').read_text()
    assert '已审阅并实际合并' in text and '尚未重放执行' in text
    for old in merged['merged_operation_ids']:
        assert '原组已合并至' in (store.directory/('group-'+old+'.html')).read_text()
        historical=old_heads[old]
        assert (store.directory/'definitions'/old/(historical['id']+'.json')).exists()
    assert (store.directory/'merges'/(result['id']+'.json')).exists()


def test_parameter_mapping_preserves_evidence_and_missing_values_without_new_model(store,monkeypatch):
    plan=setup_merge(store);merge_reviewed(store,plan)
    oid=revision(store,plan['revision_id'])['operation_id'];records=list(current_records(store,operation_id=oid))
    assert len(records)==3
    assert all(r['generation']['prompt_version'] is None for r in records)
    food=next(r for r in records if r['sample_id']=='restaurant')
    p=next(p for p in food['parameters'] if p['name']=='requirements')
    assert p['status']=='mapped' and p['value']=='按评分取前5项' and p['evidence']
    options=parameter_candidates(store,oid,['location','requirements'],scope='test')
    assert options['requirements'][0]['value']=='按评分取前5项' and options['requirements'][0]['count']==1
    assert options['requirements'][0]['sources'][0]['value_status']=='mapped'
    school=next(r for r in records if r['sample_id']=='school')
    assert next(p for p in school['parameters'] if p['name']=='requirements')['status']=='missing'
    from proximic_ring.agent_workflow.parameters import parameter_jobs
    monkeypatch.setattr(parameter_jobs,'agent_busy',lambda:False)
    assert not parameter_jobs.process_parameters(store,None,scope='test',model=lambda *a,**k:pytest.fail('duplicate extraction'))


@pytest.mark.parametrize('fault',['stale_head','new_member','missing_source','missing_branch','binding','no_authorization','fake_replay','running_job'])
def test_invalid_or_stale_merge_is_atomic(store,fault):
    plan=setup_merge(store)
    if fault=='stale_head':plan['expected_heads'][next(iter(plan['expected_heads']))]='stale'
    elif fault=='new_member':plan['membership_token']='stale'
    elif fault=='missing_source':
        with store.db:store.db.execute('UPDATE operation_revisions SET sources=? WHERE id=?',('[]',plan['revision_id']))
    elif fault=='missing_branch':plan['review']['branches'].pop(next(iter(plan['review']['branches'])))
    elif fault=='binding':next(iter(plan['parameter_moves'].values()))['location']={'source':'madeup'}
    elif fault=='no_authorization':plan['review']['authorization']=''
    elif fault=='fake_replay':plan['review']['execution_verified']=True
    else:
        from proximic_ring.agent_workflow.operations.operation_jobs import create_job
        with store.db:create_job(store,'parameters','running',{})
    before=heads(store);members=store.rows('operation_memberships');params=store.rows('operation_parameter_history')
    with pytest.raises(ValueError):merge_reviewed(store,plan)
    assert heads(store)==before and store.rows('operation_memberships')==members
    assert store.rows('operation_parameter_history')==params and not store.rows('operation_merges')


def test_failure_after_membership_write_rolls_back_all_changes(store,monkeypatch):
    plan=setup_merge(store);before=heads(store);members=store.rows('operation_memberships');events=store.rows('operation_membership_events')
    from proximic_ring.agent_workflow.operations import operation_merge
    def fail(*a,**kw):raise RuntimeError('migration failed')
    monkeypatch.setattr(operation_merge,'_parameters',fail)
    with pytest.raises(RuntimeError):merge_reviewed(store,plan)
    assert heads(store)==before and store.rows('operation_memberships')==members
    assert store.rows('operation_membership_events')==events and not store.rows('operation_merges')


def test_old_exposure_cannot_silently_submit_after_contract_merge(store):
    plan=setup_merge(store)
    frozen=request_recommendations(store,scope='test',surface='cli')
    exposures=[(i,store.expose(frozen['request_id'],i['operation_id'],method='cli_confirmed')) for i in frozen['items'] if i['operation_id'] in plan['expected_heads']]
    merge_reviewed(store,plan)
    for item,e in exposures:
        with pytest.raises(ValueError,match='重新打开推荐'):
            prepare_execution(store,e,{p['name']:'fixture' for p in item['workflow']['parameters']})
    assert not store.rows('recommendation_executions')
    fresh=request_recommendations(store,scope='test',surface='cli')
    item=next(i for i in fresh['items'] if i['workflow_revision']==plan['revision_id'])
    e=store.expose(fresh['request_id'],item['operation_id'],method='cli_confirmed')
    prepare_execution(store,e,{p['name']:'fixture' for p in item['workflow']['parameters']})
    row=store.rows('recommendation_executions')[0]
    assert row['revision_id']==plan['revision_id'] and '{{location}}' not in row['prompt']


def test_prepared_old_ticket_keeps_history_but_cannot_dispatch_after_merge(store):
    plan=setup_merge(store);frozen=request_recommendations(store,scope='test',surface='cli')
    item=next(i for i in frozen['items'] if i['operation_id'] in plan['expected_heads'])
    e=store.expose(frozen['request_id'],item['operation_id'],method='cli_confirmed')
    ticket=prepare_execution(store,e,{p['name']:'fixture' for p in item['workflow']['parameters']})
    before=store.rows('recommendation_executions')
    merge_reviewed(store,plan)
    with pytest.raises(ValueError,match='重新打开推荐'):dispatch_command(store,ticket)
    assert store.rows('recommendation_executions')==before


def test_candidate_membership_snapshot_cannot_be_replaced_with_fresh_token(store):
    plan=setup_merge(store)
    with store.db:
        store.db.execute("UPDATE samples SET occurred_at='2026-10-21' WHERE id='school'")
    plan['membership_token']=membership_token(store,plan['expected_heads'])
    with pytest.raises(ValueError,match='泛化后组成员或来源发生变化'):merge_reviewed(store,plan)


def test_historical_generalization_candidate_still_uses_legacy_validator(store):
    plan=setup_merge(store)
    from test_operation_generalization import candidate as legacy_candidate
    from proximic_ring.agent_workflow.operations.operation_prototypes import add_revision
    from proximic_ring.agent_workflow.operations.operation_revision_validation import validate_update
    new=revision(store,plan['revision_id']);current=heads(store)[new['operation_id']]
    packet=dict(current_workflow=current['flow'],material_information=dict(kind='generalization'),
        sources=[dict(version_id=v,workflow=f) for v,f in store.versions().items()
                 if v in new['generation']['parameter_bindings']])
    value=legacy_candidate(packet);validate_update(value,packet)
    with store.db:
        rid=add_revision(store,new['operation_id'],value['workflow'],parent=current['id'],kind='generalization',
            reason=value['reason'],sources=new['sources'],generation={k:value[k] for k in ('changes','coverage','parameter_bindings')})
    plan['revision_id']=rid
    merge_reviewed(store,plan)
    assert heads(store)[new['operation_id']]['id']==rid
